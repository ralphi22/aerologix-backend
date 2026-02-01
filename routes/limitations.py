"""
Operational Limitations Routes for AeroLogix AI

Provides API to retrieve TEA operational limitations detected from OCR reports.
These are FACTS from documents - NOT calculated statuses.

ABSOLUTE RULES:
❌ Never transform to status
❌ Never deduce rules
❌ Never calculate compliance
✔ Always keep raw text
✔ Always reference the report
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from typing import List, Optional
from datetime import datetime
import logging

from models.operational_limitations import (
    LimitationCategory,
    OperationalLimitationResponse,
    AircraftLimitationsResponse,
)
from models.user import User
from services.auth_deps import get_current_user
from database.mongodb import get_database

router = APIRouter(prefix="/api/limitations", tags=["limitations"])
logger = logging.getLogger(__name__)


@router.get("/{aircraft_id}", response_model=AircraftLimitationsResponse)
async def get_aircraft_limitations(
    aircraft_id: str,
    category: Optional[str] = Query(None, description="Filter by category: ELT, AVIONICS, PROPELLER, ENGINE, AIRFRAME, GENERAL"),
    limit: int = Query(100, ge=1, le=500),
    current_user: User = Depends(get_current_user),
    db = Depends(get_database)
):
    """
    Get all operational limitations for an aircraft.
    
    Returns raw limitation texts detected from OCR reports.
    These are FACTS written by TEA/AME - NOT calculated statuses.
    
    INFORMATIONAL ONLY - Always verify with AME and official records.
    """
    logger.info(f"Getting limitations for aircraft_id={aircraft_id}, user_id={current_user.id}")
    
    # Verify aircraft belongs to user
    aircraft = await db.aircrafts.find_one({
        "_id": aircraft_id,
        "user_id": current_user.id
    })
    
    if not aircraft:
        logger.warning(f"Aircraft {aircraft_id} not found for user {current_user.id}")
        raise HTTPException(status_code=404, detail="Aircraft not found")
    
    registration = aircraft.get("registration")
    
    # Build query
    query = {
        "aircraft_id": aircraft_id,
        "user_id": current_user.id
    }
    
    # Filter by category if provided
    if category:
        try:
            cat_enum = LimitationCategory(category.upper())
            query["category"] = cat_enum.value
        except ValueError:
            raise HTTPException(
                status_code=400, 
                detail=f"Invalid category. Must be one of: {[c.value for c in LimitationCategory]}"
            )
    
    # Fetch limitations
    cursor = db.operational_limitations.find(query).sort("created_at", -1).limit(limit)
    
    limitations_list: List[OperationalLimitationResponse] = []
    category_counts = {}
    
    async for doc in cursor:
        cat = doc.get("category", "GENERAL")
        category_counts[cat] = category_counts.get(cat, 0) + 1
        
        # Format dates
        report_date_str = None
        if doc.get("report_date"):
            if isinstance(doc["report_date"], datetime):
                report_date_str = doc["report_date"].strftime("%Y-%m-%d")
            elif isinstance(doc["report_date"], str):
                report_date_str = doc["report_date"]
        
        created_at_str = ""
        if doc.get("created_at"):
            if isinstance(doc["created_at"], datetime):
                created_at_str = doc["created_at"].isoformat()
            elif isinstance(doc["created_at"], str):
                created_at_str = doc["created_at"]
        
        limitations_list.append(OperationalLimitationResponse(
            id=str(doc.get("_id", "")),
            limitation_text=doc.get("limitation_text", ""),
            detected_keywords=doc.get("detected_keywords", []),
            category=LimitationCategory(cat),
            confidence=doc.get("confidence", 0.5),
            report_id=doc.get("report_id", ""),
            report_date=report_date_str,
            created_at=created_at_str
        ))
    
    logger.info(f"Found {len(limitations_list)} limitations for aircraft {aircraft_id}")
    
    return AircraftLimitationsResponse(
        aircraft_id=aircraft_id,
        registration=registration,
        limitations=limitations_list,
        total_count=len(limitations_list),
        categories=category_counts
    )


@router.get("/{aircraft_id}/summary")
async def get_limitations_summary(
    aircraft_id: str,
    current_user: User = Depends(get_current_user),
    db = Depends(get_database)
):
    """
    Get a summary of limitations by category for an aircraft.
    
    Returns counts per category - useful for dashboard display.
    """
    logger.info(f"Getting limitations summary for aircraft_id={aircraft_id}")
    
    # Verify aircraft belongs to user
    aircraft = await db.aircrafts.find_one({
        "_id": aircraft_id,
        "user_id": current_user.id
    })
    
    if not aircraft:
        raise HTTPException(status_code=404, detail="Aircraft not found")
    
    # Aggregate by category
    pipeline = [
        {
            "$match": {
                "aircraft_id": aircraft_id,
                "user_id": current_user.id
            }
        },
        {
            "$group": {
                "_id": "$category",
                "count": {"$sum": 1},
                "latest": {"$max": "$created_at"}
            }
        }
    ]
    
    categories = {}
    total = 0
    
    async for doc in db.operational_limitations.aggregate(pipeline):
        cat = doc["_id"]
        count = doc["count"]
        categories[cat] = {
            "count": count,
            "latest": doc["latest"].isoformat() if doc.get("latest") else None
        }
        total += count
    
    return {
        "aircraft_id": aircraft_id,
        "registration": aircraft.get("registration"),
        "total_limitations": total,
        "by_category": categories,
        "has_elt_limitations": "ELT" in categories,
        "has_avionics_limitations": "AVIONICS" in categories
    }


# ============================================================
# CRITICAL MENTIONS ENDPOINT - Aggregated view with DEDUPLICATION
# ============================================================

def _extract_key_phrases(text: str) -> set:
    """
    Extract key phrases from limitation text for fuzzy matching.
    Returns a set of important keywords/phrases.
    """
    import re
    if not text:
        return set()
    
    # Uppercase for consistency
    text = text.upper()
    
    # Remove JSON artifacts
    text = re.sub(r'"[A-Z_]+"\s*:\s*(?:"[^"]*"|[\d.]+)\s*,?\s*', '', text)
    text = re.sub(r'[\{\}\[\]\(\)]', '', text)
    
    # Key aviation phrases to detect
    key_phrases = []
    
    # ELT related
    if 'ELT' in text or 'E.L.T' in text:
        key_phrases.append('ELT')
    if 'REMOVED' in text and ('CERTIFICATION' in text or '25' in text):
        key_phrases.append('ELT_REMOVED')
    if 'LIMITED TO 25' in text or '25 N.M' in text or '25 NM' in text:
        key_phrases.append('LIMITED_25NM')
    
    # Avionics related
    if 'PITOT' in text or 'STATIC' in text:
        key_phrases.append('PITOT_STATIC')
    if 'TRANSPONDER' in text:
        key_phrases.append('TRANSPONDER')
    if 'ENCODER' in text:
        key_phrases.append('ENCODER')
    if '24 MONTH' in text or '24MONTH' in text:
        key_phrases.append('24_MONTH_INSPECTION')
    if 'OVERDUE' in text:
        key_phrases.append('OVERDUE')
    if 'CONTROL ZONE' in text:
        key_phrases.append('CONTROL_ZONE')
    
    # First Aid Kit
    if 'FIRST AID' in text or 'FIRSTAID' in text:
        key_phrases.append('FIRST_AID_KIT')
    if 'SERVICEABLE' in text or 'SERVICIABLE' in text:
        key_phrases.append('SERVICEABLE')
    
    # Fire extinguisher
    if 'FIRE' in text and 'EXTINGUISHER' in text:
        key_phrases.append('FIRE_EXTINGUISHER')
    
    # If no specific phrases found, use first 50 chars normalized
    if not key_phrases:
        # Clean and take first significant words
        cleaned = re.sub(r'[^A-Z0-9\s]', '', text)
        words = cleaned.split()[:6]
        key_phrases.append('_'.join(words))
    
    return set(key_phrases)


def _normalize_limitation_text(text: str) -> str:
    """
    Normalize limitation text for deduplication.
    Creates a canonical key based on important content.
    """
    import re
    if not text:
        return ""
    
    # Uppercase
    text = text.upper()
    
    # Remove JSON-like patterns
    text = re.sub(r'"[A-Z_]+"\s*:\s*(?:"[^"]*"|[\d.]+)\s*,?\s*', '', text)
    text = re.sub(r'[\{\}\[\]\(\)]', '', text)
    
    # Remove common noise words and punctuation
    text = re.sub(r'[.,;:\'"!?]', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip()
    
    # Extract key phrases and sort them for consistent hashing
    phrases = sorted(_extract_key_phrases(text))
    
    if phrases:
        return '|'.join(phrases)
    
    # Fallback: first 60 chars of cleaned text
    return text[:60]


def _deduplicate_mentions(mentions: list) -> list:
    """
    Aggressively deduplicate mentions by extracted key phrases.
    Keeps the most recent one (by report_date) for each unique pattern.
    """
    seen_keys = {}
    
    for mention in mentions:
        text = mention.get("text", "")
        if not text:
            continue
        
        # Get normalized key
        normalized_key = _normalize_limitation_text(text)
        if not normalized_key:
            continue
        
        # If we haven't seen this key, or this one is newer, keep it
        if normalized_key not in seen_keys:
            seen_keys[normalized_key] = mention
        else:
            # Compare dates - keep newer one
            existing_date = seen_keys[normalized_key].get("report_date") or ""
            new_date = mention.get("report_date") or ""
            if new_date > existing_date:
                seen_keys[normalized_key] = mention
    
    return list(seen_keys.values())


@router.get("/{aircraft_id}/critical-mentions")
async def get_critical_mentions(
    aircraft_id: str,
    current_user: User = Depends(get_current_user),
    db = Depends(get_database)
):
    """
    Get all critical mentions for an aircraft with report dates.
    
    DEDUPLICATION: Removes duplicate mentions based on normalized text.
    
    Aggregates data from:
    - operational_limitations (ELT, AVIONICS, GENERAL limitations)
    - ocr_scans.extracted_data.elt_data (ELT component data)
    
    Returns structured view by category:
    - ELT mentions
    - Avionics mentions (pitot/static/transponder)
    - Fire extinguisher mentions
    - Other limitations
    
    Each mention has an 'id' field for deletion via DELETE /api/limitations/{aircraft_id}/{id}
    
    READ-ONLY - No calculations, no status inference.
    INFORMATIONAL ONLY - Always verify with AME.
    """
    logger.info(f"Getting critical mentions for aircraft_id={aircraft_id}")
    
    # Verify aircraft belongs to user
    aircraft = await db.aircrafts.find_one({
        "_id": aircraft_id,
        "user_id": current_user.id
    })
    
    if not aircraft:
        raise HTTPException(status_code=404, detail="Aircraft not found")
    
    registration = aircraft.get("registration")
    
    # Initialize raw lists (before deduplication)
    raw_elt = []
    raw_avionics = []
    raw_fire_extinguisher = []
    raw_general = []
    
    # ============================================================
    # 1. Get limitations by category from operational_limitations
    # ============================================================
    
    # ELT limitations
    elt_cursor = db.operational_limitations.find({
        "aircraft_id": aircraft_id,
        "user_id": current_user.id,
        "category": "ELT"
    }).sort("report_date", -1)
    
    async for doc in elt_cursor:
        report_date_str = None
        if doc.get("report_date"):
            if isinstance(doc["report_date"], datetime):
                report_date_str = doc["report_date"].strftime("%Y-%m-%d")
            else:
                report_date_str = str(doc["report_date"])
        
        raw_elt.append({
            "id": str(doc.get("_id", "")),
            "text": doc.get("limitation_text", ""),
            "keywords": doc.get("detected_keywords", []),
            "confidence": doc.get("confidence", 0.5),
            "report_id": doc.get("report_id"),
            "report_date": report_date_str,
            "source": "limitation_detector",
            "can_delete": True
        })
    
    # Avionics limitations (pitot/static/transponder)
    avionics_cursor = db.operational_limitations.find({
        "aircraft_id": aircraft_id,
        "user_id": current_user.id,
        "category": "AVIONICS"
    }).sort("report_date", -1)
    
    async for doc in avionics_cursor:
        report_date_str = None
        if doc.get("report_date"):
            if isinstance(doc["report_date"], datetime):
                report_date_str = doc["report_date"].strftime("%Y-%m-%d")
            else:
                report_date_str = str(doc["report_date"])
        
        raw_avionics.append({
            "id": str(doc.get("_id", "")),
            "text": doc.get("limitation_text", ""),
            "keywords": doc.get("detected_keywords", []),
            "confidence": doc.get("confidence", 0.5),
            "report_id": doc.get("report_id"),
            "report_date": report_date_str,
            "source": "limitation_detector",
            "can_delete": True
        })
    
    # Fire extinguisher limitations (dedicated category)
    fire_cursor = db.operational_limitations.find({
        "aircraft_id": aircraft_id,
        "user_id": current_user.id,
        "category": "FIRE_EXTINGUISHER"
    }).sort("report_date", -1)
    
    async for doc in fire_cursor:
        report_date_str = None
        if doc.get("report_date"):
            if isinstance(doc["report_date"], datetime):
                report_date_str = doc["report_date"].strftime("%Y-%m-%d")
            else:
                report_date_str = str(doc["report_date"])
        
        raw_fire_extinguisher.append({
            "id": str(doc.get("_id", "")),
            "text": doc.get("limitation_text", ""),
            "keywords": doc.get("detected_keywords", []),
            "confidence": doc.get("confidence", 0.5),
            "report_id": doc.get("report_id"),
            "report_date": report_date_str,
            "source": "limitation_detector",
            "can_delete": True
        })
    
    # General limitations (other categories)
    general_cursor = db.operational_limitations.find({
        "aircraft_id": aircraft_id,
        "user_id": current_user.id,
        "category": {"$in": ["GENERAL", "ENGINE", "PROPELLER", "AIRFRAME"]}
    }).sort("report_date", -1)
    
    async for doc in general_cursor:
        report_date_str = None
        if doc.get("report_date"):
            if isinstance(doc["report_date"], datetime):
                report_date_str = doc["report_date"].strftime("%Y-%m-%d")
            else:
                report_date_str = str(doc["report_date"])
        
        text_lower = (doc.get("limitation_text") or "").lower()
        
        # Legacy fallback: Check if this is a fire extinguisher mention (for old data)
        if "fire" in text_lower or "extinguisher" in text_lower:
            raw_fire_extinguisher.append({
                "id": str(doc.get("_id", "")),
                "text": doc.get("limitation_text", ""),
                "keywords": doc.get("detected_keywords", []),
                "confidence": doc.get("confidence", 0.5),
                "report_id": doc.get("report_id"),
                "report_date": report_date_str,
                "source": "limitation_detector",
                "can_delete": True
            })
        else:
            raw_general.append({
                "id": str(doc.get("_id", "")),
                "text": doc.get("limitation_text", ""),
                "keywords": doc.get("detected_keywords", []),
                "confidence": doc.get("confidence", 0.5),
                "report_id": doc.get("report_id"),
                "report_date": report_date_str,
                "category": doc.get("category", "GENERAL"),
                "source": "limitation_detector",
                "can_delete": True
            })
    
    # ============================================================
    # 2. Get ELT data from OCR scans (extracted_data.elt_data)
    # ============================================================
    
    ocr_cursor = db.ocr_scans.find({
        "aircraft_id": aircraft_id,
        "user_id": current_user.id,
        "status": {"$in": ["COMPLETED", "APPLIED"]},
        "extracted_data.elt_data.detected": True
    }).sort("created_at", -1)
    
    async for scan in ocr_cursor:
        extracted = scan.get("extracted_data", {})
        elt_data = extracted.get("elt_data", {})
        
        if not elt_data or not elt_data.get("detected"):
            continue
        
        # Get report date from extracted_data or scan created_at
        report_date_str = None
        if extracted.get("date"):
            report_date_str = extracted["date"]
        elif scan.get("created_at"):
            if isinstance(scan["created_at"], datetime):
                report_date_str = scan["created_at"].strftime("%Y-%m-%d")
        
        # Build readable text summary
        text_parts = []
        if elt_data.get("brand"):
            text_parts.append(f"ELT: {elt_data['brand']}")
        if elt_data.get("model"):
            text_parts.append(f"Model: {elt_data['model']}")
        if elt_data.get("battery_expiry_date"):
            text_parts.append(f"Battery expires: {elt_data['battery_expiry_date']}")
        
        elt_text = " | ".join(text_parts) if text_parts else "ELT detected"
        
        # Build ELT mention from OCR data
        elt_mention = {
            "id": f"ocr_{scan.get('_id', '')}",
            "text": elt_text,
            "brand": elt_data.get("brand"),
            "model": elt_data.get("model"),
            "serial_number": elt_data.get("serial_number"),
            "battery_expiry_date": elt_data.get("battery_expiry_date"),
            "battery_install_date": elt_data.get("battery_install_date"),
            "certification_date": elt_data.get("certification_date"),
            "beacon_hex_id": elt_data.get("beacon_hex_id"),
            "report_id": str(scan.get("_id", "")),
            "report_date": report_date_str,
            "source": "ocr_extraction",
            "can_delete": False  # OCR data cannot be deleted directly
        }
        
        raw_elt.append(elt_mention)
    
    # ============================================================
    # 3. DEDUPLICATE each category
    # ============================================================
    
    dedup_elt = _deduplicate_mentions(raw_elt)
    dedup_avionics = _deduplicate_mentions(raw_avionics)
    dedup_fire = _deduplicate_mentions(raw_fire_extinguisher)
    dedup_general = _deduplicate_mentions(raw_general)
    
    # Build final response structure
    critical_mentions = {
        "elt": dedup_elt,
        "avionics": dedup_avionics,
        "fire_extinguisher": dedup_fire,
        "general_limitations": dedup_general
    }
    
    # ============================================================
    # 4. Build summary counts
    # ============================================================
    
    summary = {
        "elt_count": len(dedup_elt),
        "avionics_count": len(dedup_avionics),
        "fire_extinguisher_count": len(dedup_fire),
        "general_limitations_count": len(dedup_general),
        "total_count": len(dedup_elt) + len(dedup_avionics) + len(dedup_fire) + len(dedup_general),
        # Also include raw counts for debugging
        "raw_total": len(raw_elt) + len(raw_avionics) + len(raw_fire_extinguisher) + len(raw_general),
        "duplicates_removed": (len(raw_elt) + len(raw_avionics) + len(raw_fire_extinguisher) + len(raw_general)) - (len(dedup_elt) + len(dedup_avionics) + len(dedup_fire) + len(dedup_general))
    }
    
    logger.info(f"Critical mentions for {aircraft_id}: {summary}")
    
    return {
        "aircraft_id": aircraft_id,
        "registration": registration,
        "critical_mentions": critical_mentions,
        "summary": summary,
        "disclaimer": "INFORMATIONAL ONLY - Always verify with AME and official records"
    }


@router.delete("/{aircraft_id}/{limitation_id}")
async def delete_limitation(
    aircraft_id: str,
    limitation_id: str,
    current_user: User = Depends(get_current_user),
    db = Depends(get_database)
):
    """
    Delete a specific limitation record.
    
    Use this if a limitation was detected incorrectly or is no longer relevant.
    """
    logger.info(f"Deleting limitation {limitation_id} for aircraft {aircraft_id}")
    
    # Verify aircraft belongs to user
    aircraft = await db.aircrafts.find_one({
        "_id": aircraft_id,
        "user_id": current_user.id
    })
    
    if not aircraft:
        raise HTTPException(status_code=404, detail="Aircraft not found")
    
    # Delete the limitation
    from bson import ObjectId
    
    try:
        result = await db.operational_limitations.delete_one({
            "_id": ObjectId(limitation_id),
            "aircraft_id": aircraft_id,
            "user_id": current_user.id
        })
    except Exception:
        # Try with string ID
        result = await db.operational_limitations.delete_one({
            "_id": limitation_id,
            "aircraft_id": aircraft_id,
            "user_id": current_user.id
        })
    
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Limitation not found")
    
    logger.info(f"Deleted limitation {limitation_id}")
    
    return {"message": "Limitation deleted", "limitation_id": limitation_id}
