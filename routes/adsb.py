"""
AD/SB (Airworthiness Directives / Service Bulletins) Routes for AeroLogix AI

CANONICAL ENDPOINTS:
- GET /api/adsb/lookup/{aircraft_id} - TC AD/SB lookup by manufacturer+model
- GET /api/adsb/structured/{aircraft_id} - Structured comparison with OCR evidence
- POST /api/adsb/mark-reviewed/{aircraft_id} - Mark AD/SB as reviewed

AD/SB LOOKUP RULES:
- Lookup based on: manufacturer, model (normalized), model family
- Registration is NOT used for AD/SB lookup (only for aircraft identification)
- Model matching: "172" matches "172M", "150, 152" matches both
- Designator is optional (never blocking)
- TC-SAFE: Informational only, no compliance decisions
"""

from fastapi import APIRouter, Depends, HTTPException, status
from typing import List, Optional, Dict, Any
from datetime import datetime
from bson import ObjectId
from pydantic import BaseModel, Field
from database.mongodb import get_database
from services.auth_deps import get_current_user
from models.adsb import (
    ADSBRecord, ADSBRecordCreate, ADSBRecordUpdate,
    ADSBType, ADSBStatus
)
from models.user import User
import logging
import re

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/adsb", tags=["adsb"])


# ============================================================
# MODEL NORMALIZATION & MATCHING FUNCTIONS
# ============================================================

def normalize_model(model: str) -> str:
    """
    Normalize aircraft model for matching.
    
    Removes spaces, hyphens, converts to uppercase.
    Example: "172M" → "172M", "PA-28" → "PA28"
    """
    if not model:
        return ""
    return model.upper().replace(" ", "").replace("-", "")


def model_matches(aircraft_model: str, ad_model: str) -> bool:
    """
    Check if aircraft model matches AD/SB model specification.
    
    Supports:
    - Exact match: "172M" == "172M"
    - Family match: "172M" starts with "172"
    - Multi-model: "150, 152" matches both "150" and "152"
    
    Args:
        aircraft_model: Aircraft's model (e.g., "172M")
        ad_model: AD/SB model field (e.g., "172" or "150, 152")
        
    Returns:
        True if aircraft model matches any of the AD/SB model specifications
    """
    if not aircraft_model or not ad_model:
        return False
    
    ac = normalize_model(aircraft_model)
    
    # Split AD model by comma (handles "150, 152, 172")
    for token in ad_model.split(","):
        token_norm = normalize_model(token.strip())
        
        if not token_norm:
            continue
        
        # Exact match
        if ac == token_norm:
            return True
        
        # Family match (172M starts with 172)
        if ac.startswith(token_norm):
            return True
        
        # Reverse family match (172 in AD matches 172M aircraft)
        if token_norm.startswith(ac):
            return True
    
    return False


def adsb_applies(aircraft: dict, item: dict) -> bool:
    """
    Check if an AD/SB item applies to an aircraft.
    
    Matching rules:
    1. Manufacturer MUST match (case-insensitive)
    2. Model MUST match (using model_matches function)
    3. Designator is OPTIONAL (never blocking)
    
    Args:
        aircraft: Aircraft dict with manufacturer, model
        item: TC AD/SB item with manufacturer, model, designator
        
    Returns:
        True if the AD/SB applies to this aircraft
    """
    # Manufacturer must match (case-insensitive)
    ac_mfr = (aircraft.get("manufacturer") or "").upper().strip()
    item_mfr = (item.get("manufacturer") or "").upper().strip()
    
    if not ac_mfr or not item_mfr:
        return False
    
    if ac_mfr != item_mfr:
        return False
    
    # Model must match using flexible matching
    if not model_matches(
        aircraft.get("model", ""),
        item.get("model", "")
    ):
        return False
    
    # Designator is OPTIONAL - if present in both, prefer match
    # But NEVER block on designator mismatch
    # This ensures we don't miss applicable AD/SB
    
    return True


# ============================================================
# RESPONSE MODELS FOR LOOKUP
# ============================================================

class ADSBLookupItem(BaseModel):
    """Single AD/SB item in lookup response"""
    ref: str
    type: str  # "AD" or "SB"
    title: Optional[str] = None
    effective_date: Optional[str] = None
    recurrence_type: Optional[str] = None
    recurrence_value: Optional[int] = None
    source_url: Optional[str] = None
    designator: Optional[str] = None
    model: Optional[str] = None


class ADSBLookupAircraft(BaseModel):
    """Aircraft info in lookup response"""
    id: str
    registration: Optional[str] = None
    manufacturer: Optional[str] = None
    model: Optional[str] = None
    designator: Optional[str] = None


class ADSBLookupCount(BaseModel):
    """Count of AD vs SB"""
    ad: int = 0
    sb: int = 0
    total: int = 0


class ADSBLookupResponse(BaseModel):
    """
    TC AD/SB Lookup Response.
    
    INFORMATIONAL ONLY - NOT a compliance assessment.
    """
    aircraft: ADSBLookupAircraft
    adsb: List[ADSBLookupItem] = []
    count: ADSBLookupCount
    lookup_method: str = "manufacturer+model"
    informational_only: bool = True
    disclaimer: str = (
        "This lookup is for INFORMATIONAL PURPOSES ONLY. "
        "It does NOT constitute a compliance assessment. "
        "Verify with Transport Canada and a licensed AME."
    )


# ============================================================
# CANONICAL ENDPOINT: AD/SB BASELINE
# ============================================================
# Returns TC baseline AD/SB with OCR cross-reference (count_seen, last_seen_date)
# MongoDB is the single source of truth for TC AD/SB baseline data

class BaselineItem(BaseModel):
    """Single AD/SB baseline item with OCR history"""
    identifier: str
    type: str  # "AD" or "SB"
    title: Optional[str] = None
    effective_date: Optional[str] = None
    recurrence_raw: Optional[str] = None  # Raw recurrence type (ONCE, HOURS, etc.)
    recurrence_value: Optional[int] = None
    count_seen: int = 0  # Number of OCR Apply occurrences
    last_seen_date: Optional[str] = None  # Most recent OCR detection
    status: str = "NOT_FOUND"  # FOUND or NOT_FOUND (neutral, no compliance)
    origin: str = "TC_BASELINE"  # TC_BASELINE or USER_IMPORTED_REFERENCE
    source: Optional[str] = None  # TC_PDF_IMPORT for imported items
    # PDF import metadata (USER_IMPORTED_REFERENCE only)
    pdf_available: bool = False
    pdf_filename: Optional[str] = None
    # CANONICAL IDs for USER_IMPORTED_REFERENCE (for delete/view operations)
    # tc_reference_id = MongoDB _id (use for DELETE)
    # tc_pdf_id = UUID of PDF file (use for GET PDF)
    tc_reference_id: Optional[str] = None
    tc_pdf_id: Optional[str] = None
    imported_at: Optional[str] = None  # ISO datetime when imported
    # TC search link (generic, TC-SAFE)
    tc_search_url: str = "https://wwwapps.tc.gc.ca/Saf-Sec-Sur/2/cawis-swimn/AD_h.aspx?lang=eng"


class BaselineResponse(BaseModel):
    """TC AD/SB Baseline Response"""
    aircraft_id: str
    registration: Optional[str] = None
    manufacturer: Optional[str] = None
    model: Optional[str] = None
    ad_list: List[BaselineItem] = []
    sb_list: List[BaselineItem] = []
    total_ad: int = 0
    total_sb: int = 0
    ocr_documents_analyzed: int = 0
    source: str = "MongoDB tc_ad/tc_sb"
    informational_only: bool = True
    disclaimer: str = (
        "This is an INFORMATIONAL baseline only. "
        "It does NOT indicate compliance status. "
        "Verify with Transport Canada and a licensed AME."
    )


@router.get(
    "/baseline/{aircraft_id}",
    response_model=BaselineResponse,
    summary="TC AD/SB Baseline with OCR History [CANONICAL]",
    description="""
    **✅ CANONICAL ENDPOINT - TC AD/SB BASELINE**
    
    Returns the full TC baseline AD/SB list for an aircraft
    with OCR history cross-reference.
    
    **Data Source:** MongoDB collections (tc_ad, tc_sb)
    
    **For each AD/SB item:**
    - `count_seen`: Number of OCR Apply occurrences
    - `last_seen_date`: Most recent OCR detection (if any)
    - `recurrence_raw`: Raw recurrence type from TC
    - `status`: FOUND or NOT_FOUND (neutral, no compliance inference)
    
    **One line per AD/SB** (no duplicates)
    
    **INFORMATIONAL ONLY** - No compliance decisions.
    """
)
async def get_adsb_baseline(
    aircraft_id: str,
    current_user: User = Depends(get_current_user),
    db=Depends(get_database)
):
    """
    CANONICAL ENDPOINT: TC AD/SB Baseline with OCR cross-reference.
    
    MongoDB is the single source of truth.
    Returns count_seen and last_seen_date per item.
    """
    logger.info(f"[CANONICAL] AD/SB Baseline | aircraft_id={aircraft_id} | user={current_user.id}")
    
    # Get aircraft
    aircraft = await db.aircrafts.find_one({
        "_id": aircraft_id,
        "user_id": current_user.id
    })
    
    if not aircraft:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Aircraft not found"
        )
    
    manufacturer = aircraft.get("manufacturer", "")
    model = aircraft.get("model", "")
    registration = aircraft.get("registration")
    designator = aircraft.get("designator")
    
    # Import helper functions from structured service
    from services.structured_adsb_service import StructuredADSBComparisonService
    service = StructuredADSBComparisonService(db)
    
    # Get OCR references (user-validated APPLIED documents)
    ocr_references, doc_count = await service.get_ocr_adsb_references(aircraft_id, current_user.id)
    
    # Fetch TC AD baseline from MongoDB
    # ONLY canonical TC data (source != OCR_SCAN, != USER_MANUAL)
    # TC_PDF_IMPORT items are handled separately below
    ad_list = []
    async for ad in db.tc_ad.find({
        "is_active": True,
        "source": {"$nin": ["OCR_SCAN", "USER_MANUAL"]}  # Exclude non-TC sources
    }):
        # Check if applies to this aircraft (by designator or manufacturer+model)
        applies = False
        
        # Skip TC_PDF_IMPORT items here - they are added separately by aircraft_id
        if ad.get("source") == "TC_PDF_IMPORT":
            continue
        
        if designator and ad.get("designator") == designator:
            applies = True
        elif service._model_matches(model, ad.get("model", "")) and \
             manufacturer.upper() == (ad.get("manufacturer") or "").upper():
            applies = True
        
        if applies:
            identifier = ad.get("ref", "")
            norm_id = service._normalize_identifier(identifier)
            
            # Count OCR occurrences
            count_seen = 0
            all_dates = []
            for ocr_ref, dates in ocr_references.items():
                if service._identifiers_match(norm_id, ocr_ref):
                    count_seen += len(dates)
                    all_dates.extend(dates)
            
            sorted_dates = sorted(set(all_dates), reverse=True)
            last_seen = sorted_dates[0] if sorted_dates else None
            
            # Format effective date
            eff_date = ad.get("effective_date")
            eff_str = eff_date.strftime("%Y-%m-%d") if hasattr(eff_date, 'strftime') else str(eff_date)[:10] if eff_date else None
            
            ad_list.append(BaselineItem(
                identifier=identifier,
                type="AD",
                title=ad.get("title"),
                effective_date=eff_str,
                recurrence_raw=ad.get("recurrence_type"),
                recurrence_value=ad.get("recurrence_value"),
                count_seen=count_seen,
                last_seen_date=last_seen,
                status="FOUND" if count_seen > 0 else "NOT_FOUND",
                origin="TC_BASELINE",  # Explicit: canonical TC data
            ))
    
    # Fetch TC SB baseline from MongoDB
    # ONLY canonical TC data (source != OCR_SCAN, != USER_MANUAL)
    # TC_PDF_IMPORT items are handled separately below
    sb_list = []
    async for sb in db.tc_sb.find({
        "is_active": True,
        "source": {"$nin": ["OCR_SCAN", "USER_MANUAL"]}  # Exclude non-TC sources
    }):
        # Skip TC_PDF_IMPORT items here - they are added separately by aircraft_id
        if sb.get("source") == "TC_PDF_IMPORT":
            continue
        
        applies = False
        if designator and sb.get("designator") == designator:
            applies = True
        elif service._model_matches(model, sb.get("model", "")) and \
             manufacturer.upper() == (sb.get("manufacturer") or "").upper():
            applies = True
        
        if applies:
            identifier = sb.get("ref", "")
            norm_id = service._normalize_identifier(identifier)
            
            count_seen = 0
            all_dates = []
            for ocr_ref, dates in ocr_references.items():
                if service._identifiers_match(norm_id, ocr_ref):
                    count_seen += len(dates)
                    all_dates.extend(dates)
            
            sorted_dates = sorted(set(all_dates), reverse=True)
            last_seen = sorted_dates[0] if sorted_dates else None
            
            eff_date = sb.get("effective_date")
            eff_str = eff_date.strftime("%Y-%m-%d") if hasattr(eff_date, 'strftime') else str(eff_date)[:10] if eff_date else None
            
            sb_list.append(BaselineItem(
                identifier=identifier,
                type="SB",
                title=sb.get("title"),
                effective_date=eff_str,
                recurrence_raw=sb.get("recurrence_type"),
                recurrence_value=sb.get("recurrence_value"),
                count_seen=count_seen,
                last_seen_date=last_seen,
                status="FOUND" if count_seen > 0 else "NOT_FOUND",
                origin="TC_BASELINE",  # Explicit: canonical TC data
            ))
    
    # ============================================================
    # PATCH: ADD USER-IMPORTED AD/SB FROM tc_imported_references
    # ============================================================
    # V2: Reads from dedicated tc_imported_references collection
    # These are references imported manually by user via PDF upload.
    # TC-SAFE: Marked as USER_IMPORTED_REFERENCE, not canonical TC data.
    
    existing_ad_refs = {item.identifier for item in ad_list}
    existing_sb_refs = {item.identifier for item in sb_list}
    
    user_imported_ad_count = 0
    user_imported_sb_count = 0
    
    # Query tc_imported_references for this aircraft
    async for ref in db.tc_imported_references.find({"aircraft_id": aircraft_id}):
        identifier = ref.get("identifier", "")
        ref_type = ref.get("type", "AD")
        
        # Skip if already in canonical baseline (union by identifier)
        if ref_type == "AD" and identifier in existing_ad_refs:
            continue
        if ref_type == "SB" and identifier in existing_sb_refs:
            continue
        
        norm_id = service._normalize_identifier(identifier)
        
        # Count OCR occurrences
        count_seen = 0
        all_dates = []
        for ocr_ref, dates in ocr_references.items():
            if service._identifiers_match(norm_id, ocr_ref):
                count_seen += len(dates)
                all_dates.extend(dates)
        
        sorted_dates = sorted(set(all_dates), reverse=True)
        last_seen = sorted_dates[0] if sorted_dates else None
        
        # Get stable IDs:
        # tc_reference_id = MongoDB _id (24-char hex) → for DELETE
        # tc_pdf_id = UUID → for GET PDF
        tc_reference_id = str(ref.get("_id"))
        tc_pdf_id = ref.get("tc_pdf_id")
        created_at = ref.get("created_at")
        imported_at_str = created_at.isoformat() if hasattr(created_at, 'isoformat') else str(created_at) if created_at else None
        
        # Get PDF filename from tc_pdf_imports if available
        pdf_filename = None
        if tc_pdf_id:
            pdf_doc = await db.tc_pdf_imports.find_one({"tc_pdf_id": tc_pdf_id}, {"filename": 1})
            if pdf_doc:
                pdf_filename = pdf_doc.get("filename")
        
        item = BaselineItem(
            identifier=identifier,
            type=ref_type,
            title=ref.get("title"),
            effective_date=None,
            recurrence_raw=None,
            recurrence_value=None,
            count_seen=count_seen,
            last_seen_date=last_seen,
            status="FOUND" if count_seen > 0 else "NOT_FOUND",
            origin="USER_IMPORTED_REFERENCE",
            source="TC_PDF_IMPORT",
            pdf_available=bool(tc_pdf_id),
            pdf_filename=pdf_filename,
            tc_reference_id=tc_reference_id,
            tc_pdf_id=tc_pdf_id,
            imported_at=imported_at_str,
        )
        
        if ref_type == "AD":
            ad_list.append(item)
            user_imported_ad_count += 1
        else:
            sb_list.append(item)
            user_imported_sb_count += 1
    
    # Log user-imported additions (TC-SAFE audit)
    if user_imported_ad_count > 0 or user_imported_sb_count > 0:
        logger.info(
            f"[AD/SB BASELINE] +{user_imported_ad_count} AD, +{user_imported_sb_count} SB "
            f"user-imported references from tc_imported_references"
        )
    
    # Sort by identifier
    ad_list.sort(key=lambda x: x.identifier)
    sb_list.sort(key=lambda x: x.identifier)
    
    logger.info(
        f"[AD/SB BASELINE] {manufacturer} {model} | "
        f"AD={len(ad_list)}, SB={len(sb_list)} | OCR docs={doc_count}"
    )
    
    return BaselineResponse(
        aircraft_id=aircraft_id,
        registration=registration,
        manufacturer=manufacturer,
        model=model,
        ad_list=ad_list,
        sb_list=sb_list,
        total_ad=len(ad_list),
        total_sb=len(sb_list),
        ocr_documents_analyzed=doc_count,
        source="MongoDB tc_ad/tc_sb",
        informational_only=True,
    )


# ============================================================
# CANONICAL ENDPOINT: AD/SB LOOKUP
# ============================================================

@router.get(
    "/lookup/{aircraft_id}",
    response_model=ADSBLookupResponse,
    summary="TC AD/SB Lookup by Manufacturer + Model [CANONICAL]",
    description="""
    **✅ CANONICAL ENDPOINT - TC AD/SB LOOKUP**
    
    Retrieves all applicable Transport Canada AD/SB for an aircraft
    based on **manufacturer** and **model** matching.
    
    **MATCHING RULES:**
    - Manufacturer: Exact match (case-insensitive)
    - Model: Flexible match (172 matches 172M, "150, 152" matches both)
    - Designator: Optional (never blocking)
    
    **NOT USED FOR LOOKUP:**
    - Registration (C-XXXX) - only used to identify the aircraft
    
    **INFORMATIONAL ONLY:**
    - No compliance decisions
    - No "compliant/non-compliant" status
    - Verify with TC and licensed AME
    """
)
async def lookup_adsb(
    aircraft_id: str,
    current_user: User = Depends(get_current_user),
    db=Depends(get_database)
):
    """
    CANONICAL ENDPOINT: TC AD/SB Lookup.
    
    Looks up applicable AD/SB based on manufacturer + model.
    Registration is NOT used for AD/SB matching.
    """
    logger.info(f"[CANONICAL] AD/SB Lookup | aircraft_id={aircraft_id} | user={current_user.id}")
    
    # Get aircraft
    aircraft = await db.aircrafts.find_one({
        "_id": aircraft_id,
        "user_id": current_user.id
    })
    
    if not aircraft:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Aircraft not found"
        )
    
    manufacturer = aircraft.get("manufacturer")
    model = aircraft.get("model")
    
    # Validate required fields
    if not manufacturer:
        logger.warning(f"[AD/SB LOOKUP] No manufacturer | aircraft_id={aircraft_id}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Aircraft manufacturer is required for AD/SB lookup"
        )
    
    if not model:
        logger.warning(f"[AD/SB LOOKUP] No model | aircraft_id={aircraft_id}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Aircraft model is required for AD/SB lookup"
        )
    
    # Fetch TC AD/SB by manufacturer (broad query)
    # Then filter by model matching in Python for flexibility
    manufacturer_upper = manufacturer.upper().strip()
    
    tc_items = []
    
    # Query TC AD collection
    ad_cursor = db.tc_ad.find({
        "manufacturer": {"$regex": f"^{re.escape(manufacturer_upper)}$", "$options": "i"},
        "is_active": True
    })
    async for ad in ad_cursor:
        tc_items.append({
            **ad,
            "_type": "AD"
        })
    
    # Query TC SB collection
    sb_cursor = db.tc_sb.find({
        "manufacturer": {"$regex": f"^{re.escape(manufacturer_upper)}$", "$options": "i"},
        "is_active": True
    })
    async for sb in sb_cursor:
        tc_items.append({
            **sb,
            "_type": "SB"
        })
    
    logger.info(f"[AD/SB LOOKUP] Fetched {len(tc_items)} TC items for manufacturer={manufacturer}")
    
    # Filter by model matching
    applicable = []
    for item in tc_items:
        if adsb_applies(aircraft, item):
            # Format effective_date
            eff_date = item.get("effective_date")
            if eff_date and hasattr(eff_date, 'strftime'):
                eff_date = eff_date.strftime("%Y-%m-%d")
            elif eff_date:
                eff_date = str(eff_date)
            
            applicable.append(ADSBLookupItem(
                ref=item.get("ref", ""),
                type=item.get("_type", "AD"),
                title=item.get("title"),
                effective_date=eff_date,
                recurrence_type=item.get("recurrence_type"),
                recurrence_value=item.get("recurrence_value"),
                source_url=item.get("source_url"),
                designator=item.get("designator"),
                model=item.get("model"),
            ))
    
    # Sort by type (AD first) then by ref
    applicable.sort(key=lambda x: (0 if x.type == "AD" else 1, x.ref))
    
    # Count AD vs SB
    ad_count = sum(1 for x in applicable if x.type == "AD")
    sb_count = sum(1 for x in applicable if x.type == "SB")
    
    # AUDIT LOG
    logger.info(
        f"[AD/SB LOOKUP] {manufacturer} {model} | "
        f"matched={len(applicable)} (AD={ad_count}, SB={sb_count})"
    )
    
    return ADSBLookupResponse(
        aircraft=ADSBLookupAircraft(
            id=aircraft_id,
            registration=aircraft.get("registration"),
            manufacturer=manufacturer,
            model=model,
            designator=aircraft.get("designator"),
        ),
        adsb=applicable,
        count=ADSBLookupCount(
            ad=ad_count,
            sb=sb_count,
            total=len(applicable),
        ),
        lookup_method="manufacturer+model",
        informational_only=True,
    )


@router.post("", response_model=dict)
async def create_adsb_record(
    record: ADSBRecordCreate,
    current_user: User = Depends(get_current_user),
    db=Depends(get_database)
):
    """Create a new AD/SB record"""
    
    # Verify aircraft belongs to user
    aircraft = await db.aircrafts.find_one({
        "_id": record.aircraft_id,
        "user_id": current_user.id
    })
    
    if not aircraft:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Aircraft not found"
        )
    
    now = datetime.utcnow()
    doc = record.model_dump()
    doc["user_id"] = current_user.id
    doc["adsb_type"] = doc["adsb_type"].value if isinstance(doc["adsb_type"], ADSBType) else doc["adsb_type"]
    doc["status"] = doc["status"].value if isinstance(doc["status"], ADSBStatus) else doc["status"]
    doc["created_at"] = now
    doc["updated_at"] = now
    
    result = await db.adsb_records.insert_one(doc)
    
    return {
        "id": str(result.inserted_id),
        "message": "AD/SB record created successfully"
    }


@router.get("/{aircraft_id}", response_model=List[dict])
async def get_adsb_records(
    aircraft_id: str,
    adsb_type: Optional[str] = None,
    status_filter: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db=Depends(get_database)
):
    """Get all AD/SB records for an aircraft - READS DIRECTLY FROM DB"""
    
    # Verify aircraft belongs to user
    aircraft = await db.aircrafts.find_one({
        "_id": aircraft_id,
        "user_id": current_user.id
    })
    
    if not aircraft:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Aircraft not found"
        )
    
    query = {
        "aircraft_id": aircraft_id,
        "user_id": current_user.id
    }
    
    if adsb_type:
        query["adsb_type"] = adsb_type.upper()
    
    if status_filter:
        query["status"] = status_filter.upper()
    
    # DIRECT DB READ - NO CACHE, NO OCR RECONSTRUCTION
    cursor = db.adsb_records.find(query).sort("created_at", -1)
    
    records = []
    async for record in cursor:
        record["_id"] = str(record["_id"])
        records.append(record)
    
    # LOG: GET adsb count
    logger.info(f"GET adsb | aircraft={aircraft_id} | count={len(records)}")
    return records


@router.get("/record/{record_id}", response_model=dict)
async def get_adsb_record(
    record_id: str,
    current_user: User = Depends(get_current_user),
    db=Depends(get_database)
):
    """Get a specific AD/SB record"""
    
    record = await db.adsb_records.find_one({
        "_id": record_id,
        "user_id": current_user.id
    })
    
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="AD/SB record not found"
        )
    
    record["_id"] = str(record["_id"])
    return record


@router.put("/record/{record_id}", response_model=dict)
async def update_adsb_record(
    record_id: str,
    update_data: ADSBRecordUpdate,
    current_user: User = Depends(get_current_user),
    db=Depends(get_database)
):
    """Update an AD/SB record"""
    
    record = await db.adsb_records.find_one({
        "_id": record_id,
        "user_id": current_user.id
    })
    
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="AD/SB record not found"
        )
    
    update_dict = {k: v for k, v in update_data.model_dump().items() if v is not None}
    
    if "status" in update_dict and isinstance(update_dict["status"], ADSBStatus):
        update_dict["status"] = update_dict["status"].value
    
    update_dict["updated_at"] = datetime.utcnow()
    
    await db.adsb_records.update_one(
        {"_id": record_id},
        {"$set": update_dict}
    )
    
    return {"message": "AD/SB record updated successfully"}


@router.delete("/record/{record_id}")
async def delete_adsb_record(
    record_id: str,
    current_user: User = Depends(get_current_user),
    db=Depends(get_database)
):
    """Delete an AD/SB record - PERMANENT DELETION by _id only"""
    logger.info(f"DELETE REQUEST RECEIVED | route=/api/adsb/record/{record_id} | user={current_user.id}")
    return await _delete_adsb_by_id(record_id, current_user, db)


@router.delete("/{adsb_id}")
async def delete_adsb_direct(
    adsb_id: str,
    current_user: User = Depends(get_current_user),
    db=Depends(get_database)
):
    """Delete an AD/SB record by ID - PERMANENT DELETION (frontend route)"""
    logger.info(f"DELETE REQUEST RECEIVED | route=/api/adsb/{adsb_id} | user={current_user.id}")
    return await _delete_adsb_by_id(adsb_id, current_user, db)


async def _delete_adsb_by_id(
    record_id: str,
    current_user: User,
    db
):
    """Internal function to delete an AD/SB by _id - ATOMIC OPERATION (same pattern as OCR delete)"""
    
    # Try to find by string ID first
    record = await db.adsb_records.find_one({
        "_id": record_id,
        "user_id": current_user.id
    })
    
    if not record:
        # Try with ObjectId
        try:
            record = await db.adsb_records.find_one({
                "_id": ObjectId(record_id),
                "user_id": current_user.id
            })
        except Exception:
            pass
    
    if not record:
        logger.warning(f"DELETE FAILED | reason=not_found_or_not_owner | collection=adsb | id={record_id} | user={current_user.id}")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="AD/SB record not found"
        )
    
    # DELETE using the EXACT _id from the found document (same as OCR delete)
    if isinstance(record["_id"], ObjectId):
        result = await db.adsb_records.delete_one({"_id": record["_id"]})
    else:
        result = await db.adsb_records.delete_one({"_id": record_id})
    
    if result.deleted_count == 0:
        logger.warning(f"DELETE FAILED | reason=delete_count_zero | collection=adsb | id={record_id} | user={current_user.id}")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="AD/SB record not found or already deleted"
        )
    
    # DELETE CONFIRMED log - MANDATORY
    logger.info(f"DELETE CONFIRMED | collection=adsb | id={record_id} | user={current_user.id}")
    
    return {"message": "AD/SB record deleted successfully", "deleted_id": record_id}


# ============================================================
# DELETE BY REFERENCE (ALL OCCURRENCES)
# ============================================================

@router.delete("/ocr/{aircraft_id}/reference/{reference}")
async def delete_adsb_by_reference(
    aircraft_id: str,
    reference: str,
    current_user: User = Depends(get_current_user),
    db=Depends(get_database)
):
    """
    Delete ALL AD/SB records matching a reference for an aircraft.
    
    This endpoint is used by the frontend to delete all occurrences
    of an AD/SB reference detected by OCR.
    
    URL encoded reference is automatically decoded by FastAPI.
    Example: DELETE /api/adsb/ocr/123/reference/AD%202011-10-09
    """
    logger.info(f"DELETE BY REFERENCE | aircraft={aircraft_id} | reference={reference} | user={current_user.id}")
    
    # Verify aircraft belongs to user
    aircraft = await db.aircrafts.find_one({
        "_id": aircraft_id,
        "user_id": current_user.id
    })
    
    if not aircraft:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Aircraft not found"
        )
    
    # Normalize reference for matching
    ref_normalized = normalize_adsb_reference(reference)
    
    # Find all matching records
    # Try multiple matching strategies
    matching_records = []
    
    # Strategy 1: Exact match on reference_number
    cursor = db.adsb_records.find({
        "aircraft_id": aircraft_id,
        "user_id": current_user.id,
        "reference_number": reference
    })
    async for rec in cursor:
        matching_records.append(rec)
    
    # Strategy 2: Normalized match (case insensitive)
    if not matching_records:
        cursor = db.adsb_records.find({
            "aircraft_id": aircraft_id,
            "user_id": current_user.id,
            "reference_number": {"$regex": f"^{re.escape(reference)}$", "$options": "i"}
        })
        async for rec in cursor:
            matching_records.append(rec)
    
    # Strategy 3: Match with normalized version
    if not matching_records:
        all_records = db.adsb_records.find({
            "aircraft_id": aircraft_id,
            "user_id": current_user.id
        })
        async for rec in all_records:
            rec_normalized = normalize_adsb_reference(rec.get("reference_number", ""))
            if rec_normalized == ref_normalized:
                matching_records.append(rec)
    
    if not matching_records:
        logger.warning(f"DELETE BY REF FAILED | reason=no_records_found | aircraft={aircraft_id} | ref={reference}")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No AD/SB records found for reference: {reference}"
        )
    
    # Delete all matching records
    deleted_ids = []
    for record in matching_records:
        record_id = record["_id"]
        result = await db.adsb_records.delete_one({"_id": record_id})
        if result.deleted_count > 0:
            deleted_ids.append(str(record_id))
    
    logger.info(f"DELETE BY REF CONFIRMED | aircraft={aircraft_id} | ref={reference} | deleted_count={len(deleted_ids)}")
    
    return {
        "message": f"Deleted {len(deleted_ids)} AD/SB record(s) for reference: {reference}",
        "reference": reference,
        "deleted_count": len(deleted_ids),
        "deleted_ids": deleted_ids
    }


@router.get("/{aircraft_id}/summary")
async def get_adsb_summary(
    aircraft_id: str,
    current_user: User = Depends(get_current_user),
    db=Depends(get_database)
):
    """Get AD/SB compliance summary for an aircraft"""
    
    # Verify aircraft belongs to user
    aircraft = await db.aircrafts.find_one({
        "_id": aircraft_id,
        "user_id": current_user.id
    })
    
    if not aircraft:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Aircraft not found"
        )
    
    # Count by type and status
    pipeline = [
        {"$match": {"aircraft_id": aircraft_id, "user_id": current_user.id}},
        {
            "$group": {
                "_id": {"type": "$adsb_type", "status": "$status"},
                "count": {"$sum": 1}
            }
        }
    ]
    
    cursor = db.adsb_records.aggregate(pipeline)
    
    summary = {
        "AD": {"COMPLIED": 0, "PENDING": 0, "NOT_APPLICABLE": 0, "UNKNOWN": 0},
        "SB": {"COMPLIED": 0, "PENDING": 0, "NOT_APPLICABLE": 0, "UNKNOWN": 0}
    }
    
    async for item in cursor:
        adsb_type = item["_id"]["type"]
        record_status = item["_id"]["status"]
        count = item["count"]
        
        if adsb_type in summary and record_status in summary[adsb_type]:
            summary[adsb_type][record_status] = count
    
    return summary



# ============================================================
# ============================================================
# DEPRECATED: TC AD/SB COMPARISON ENDPOINT (LEGACY)
# ============================================================
# DEPRECATED: compatibility alias for older frontend builds
# Use /api/adsb/structured/{aircraft_id} instead
# This endpoint remains functional but is not recommended for new usage

from services.adsb_comparison_service import ADSBComparisonService
from models.tc_adsb import ADSBComparisonResponse


@router.get(
    "/compare/{aircraft_id}",
    response_model=ADSBComparisonResponse,
    summary="[DEPRECATED] Compare aircraft records against TC AD/SB database",
    description="""
    **⚠️ DEPRECATED: Use /api/adsb/structured/{aircraft_id} instead**
    
    This endpoint remains functional for backward compatibility.
    
    Compares aircraft OCR/manual records against Transport Canada AD/SB database.
    
    **TC-SAFE:** This endpoint does NOT determine compliance status.
    It only provides factual comparison data for informational purposes.
    
    All airworthiness decisions must be made by a licensed AME/TEA.
    """
)
async def compare_adsb(
    aircraft_id: str,
    current_user: User = Depends(get_current_user),
    db=Depends(get_database)
):
    """
    DEPRECATED: Use structured_adsb_compare instead.
    
    Compare aircraft AD/SB records against Transport Canada requirements.
    TC-SAFE: Never returns compliance status.
    """
    # DEPRECATION WARNING LOG
    logger.warning(f"Deprecated AD/SB endpoint used: /api/adsb/compare/{aircraft_id} | user={current_user.id}")
    
    logger.info(f"AD/SB Compare | aircraft_id={aircraft_id} | user={current_user.id}")
    
    try:
        service = ADSBComparisonService(db)
        result = await service.compare(aircraft_id, current_user.id)
        
        logger.info(
            f"AD/SB Compare complete | aircraft_id={aircraft_id} | "
            f"found={result.found_count} | missing={result.missing_count}"
        )
        
        return result
        
    except ValueError as e:
        logger.warning(f"AD/SB Compare failed | aircraft_id={aircraft_id} | error={e}")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e)
        )
    except Exception as e:
        logger.error(f"AD/SB Compare error | aircraft_id={aircraft_id} | error={e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to compare AD/SB records"
        )


# ============================================================
# CANONICAL ENDPOINT: TC-SAFE STRUCTURED AD/SB COMPARISON
# ============================================================
# THIS IS THE OFFICIAL AD/SB ENDPOINT FOR FRONTEND USAGE
# All other AD/SB comparison endpoints are deprecated aliases

from services.structured_adsb_service import (
    StructuredADSBComparisonService,
    StructuredComparisonResponse
)
from services.tc_adsb_detection_service import TCADSBDetectionService


@router.get(
    "/structured/{aircraft_id}",
    response_model=StructuredComparisonResponse,
    summary="TC-Safe Structured AD/SB Comparison [CANONICAL]",
    description="""
    **✅ CANONICAL ENDPOINT - USE THIS FOR AD/SB COMPARISON**
    
    Performs a factual comparison between TC AD/SB requirements and OCR documentary evidence.
    
    **DATA FLOW:**
    1. Registration → TC Registry lookup (authoritative identity)
    2. TC Registry → aircraft identity (manufacturer, model, designator)
    3. Designator → TC AD/SB applicability lookup (FAIL-FAST if invalid)
    4. TC AD/SB → comparison against OCR-applied references
    
    **IMPORTANT:**
    - This is INFORMATIONAL ONLY
    - NO compliance decision is made
    - NO "compliant" / "non-compliant" language
    - All airworthiness decisions must be made by a licensed AME/TEA
    
    **Returns:**
    - aircraft_identity: From TC Registry
    - tc_ad_list: Applicable ADs with detection counts
    - tc_sb_list: Applicable SBs with detection counts
    - lookup_status: SUCCESS or UNAVAILABLE (if designator invalid)
    - Each item shows detected_count from OCR documents
    
    **SIDE EFFECT:** Clears AD/SB alert badge on aircraft.
    """
)
async def structured_adsb_compare(
    aircraft_id: str,
    current_user: User = Depends(get_current_user),
    db=Depends(get_database)
):
    """
    CANONICAL ENDPOINT: TC-Safe Structured AD/SB Comparison.
    
    Uses TC Registry as authoritative source for aircraft identity.
    Uses TC AD/SB database as authoritative source for applicability.
    Uses OCR documents as documentary evidence only.
    
    FAIL-FAST: Returns UNAVAILABLE if designator is missing/invalid.
    NO compliance decision is made.
    
    SIDE EFFECT: Marks AD/SB as reviewed (clears alert flag).
    """
    logger.info(f"[CANONICAL] Structured AD/SB Compare | aircraft_id={aircraft_id} | user={current_user.id}")
    
    # Get aircraft registration from user's aircraft
    aircraft = await db.aircrafts.find_one({
        "_id": aircraft_id,
        "user_id": current_user.id
    })
    
    if not aircraft:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Aircraft not found"
        )
    
    registration = aircraft.get("registration")
    if not registration:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Aircraft registration is required for TC comparison"
        )
    
    try:
        # Mark as reviewed (clear alert flag) - TC-SAFE auditable action
        detection_service = TCADSBDetectionService(db)
        try:
            await detection_service.mark_adsb_reviewed(aircraft_id, current_user.id)
            logger.info(f"AD/SB alert cleared on module view | aircraft_id={aircraft_id}")
        except Exception as e:
            # Don't fail comparison if mark-reviewed fails
            logger.warning(f"Failed to mark AD/SB reviewed: {e}")
        
        # Perform structured comparison
        service = StructuredADSBComparisonService(db)
        result = await service.compare(
            registration=registration,
            aircraft_id=aircraft_id,
            user_id=current_user.id
        )
        
        logger.info(
            f"Structured AD/SB Compare complete | registration={registration} | "
            f"ADs={result.total_applicable_ad} ({result.total_ad_with_evidence} with evidence) | "
            f"SBs={result.total_applicable_sb} ({result.total_sb_with_evidence} with evidence)"
        )
        
        return result
        
    except ValueError as e:
        logger.warning(f"Structured AD/SB Compare failed | registration={registration} | error={e}")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e)
        )
    except Exception as e:
        logger.error(f"Structured AD/SB Compare error | registration={registration} | error={e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to perform structured AD/SB comparison"
        )


# ============================================================
# CANONICAL: MARK AD/SB AS REVIEWED
# ============================================================
# This endpoint clears the AD/SB alert badge when user views the module

from models.tc_adsb_alert import MarkReviewedResponse


@router.post(
    "/mark-reviewed/{aircraft_id}",
    response_model=MarkReviewedResponse,
    summary="Mark AD/SB module as reviewed [CANONICAL]",
    description="""
    **✅ CANONICAL ENDPOINT - MARK AD/SB AS REVIEWED**
    
    Clears the AD/SB alert badge for an aircraft after user views the module.
    
    **Effect:**
    - Sets `adsb_has_new_tc_items = false`
    - Sets `count_new_adsb = 0`
    - Records `last_adsb_reviewed_at` timestamp
    
    **Audit:** This action is logged for traceability.
    """
)
async def mark_adsb_reviewed(
    aircraft_id: str,
    current_user: User = Depends(get_current_user),
    db=Depends(get_database)
):
    """
    CANONICAL ENDPOINT: Mark AD/SB as reviewed for an aircraft.
    
    Clears the alert flag and records the review timestamp.
    """
    logger.info(f"[CANONICAL] AD/SB mark-reviewed endpoint hit | aircraft_id={aircraft_id} | user={current_user.id}")
    
    try:
        service = TCADSBDetectionService(db)
        result = await service.mark_adsb_reviewed(aircraft_id, current_user.id)
        return result
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e)
        )


# ============================================================
# DEPRECATED: ROUTE ALIASES FOR FRONTEND COMPATIBILITY
# ============================================================
# DEPRECATED: compatibility aliases for older frontend builds
# Use /api/adsb/structured/{aircraft_id} instead
# These endpoints remain functional but are not recommended for new usage

from fastapi import APIRouter as AliasRouter

# Create a separate router for /api/aircraft/{aircraft_id}/adsb pattern
aircraft_adsb_router = AliasRouter(prefix="/api/aircraft", tags=["aircraft-adsb-deprecated"])


@aircraft_adsb_router.get(
    "/{aircraft_id}/adsb/compare",
    response_model=ADSBComparisonResponse,
    summary="[DEPRECATED] AD/SB Comparison (aircraft-nested route)",
    description="⚠️ DEPRECATED: Use /api/adsb/structured/{aircraft_id} instead."
)
async def aircraft_adsb_compare_alias(
    aircraft_id: str,
    current_user: User = Depends(get_current_user),
    db=Depends(get_database)
):
    """
    DEPRECATED: Use /api/adsb/structured/{aircraft_id} instead.
    Alias endpoint for AD/SB comparison under aircraft path.
    """
    # DEPRECATION WARNING LOG
    logger.warning(f"Deprecated AD/SB endpoint used: /api/aircraft/{aircraft_id}/adsb/compare | user={current_user.id}")
    
    try:
        service = ADSBComparisonService(db)
        result = await service.compare(aircraft_id, current_user.id)
        
        logger.info(
            f"AD/SB Compare complete (deprecated alias) | aircraft_id={aircraft_id} | "
            f"found={result.found_count} | missing={result.missing_count}"
        )
        
        return result
        
    except ValueError as e:
        logger.warning(f"AD/SB Compare failed (deprecated alias) | aircraft_id={aircraft_id} | error={e}")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e)
        )
    except Exception as e:
        logger.error(f"AD/SB Compare error (deprecated alias) | aircraft_id={aircraft_id} | error={e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to compare AD/SB records"
        )


@aircraft_adsb_router.get(
    "/{aircraft_id}/adsb/structured",
    response_model=StructuredComparisonResponse,
    summary="[DEPRECATED] Structured AD/SB Comparison (aircraft-nested route)",
    description="⚠️ DEPRECATED: Use /api/adsb/structured/{aircraft_id} instead."
)
async def aircraft_adsb_structured_alias(
    aircraft_id: str,
    current_user: User = Depends(get_current_user),
    db=Depends(get_database)
):
    """
    DEPRECATED: Use /api/adsb/structured/{aircraft_id} instead.
    Alias endpoint for structured AD/SB comparison under aircraft path.
    """
    # DEPRECATION WARNING LOG
    logger.warning(f"Deprecated AD/SB endpoint used: /api/aircraft/{aircraft_id}/adsb/structured | user={current_user.id}")
    
    # Get aircraft registration from user's aircraft
    aircraft = await db.aircrafts.find_one({
        "_id": aircraft_id,
        "user_id": current_user.id
    })
    
    if not aircraft:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Aircraft not found"
        )
    
    registration = aircraft.get("registration")
    if not registration:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Aircraft registration is required for TC comparison"
        )
    
    try:
        # Mark as reviewed (clear alert flag) - TC-SAFE auditable action
        detection_service = TCADSBDetectionService(db)
        try:
            await detection_service.mark_adsb_reviewed(aircraft_id, current_user.id)
            logger.info(f"AD/SB alert cleared on module view (deprecated alias) | aircraft_id={aircraft_id}")
        except Exception as e:
            logger.warning(f"Failed to mark AD/SB reviewed: {e}")
        
        # Perform structured comparison
        service = StructuredADSBComparisonService(db)
        result = await service.compare(
            registration=registration,
            aircraft_id=aircraft_id,
            user_id=current_user.id
        )
        
        logger.info(
            f"Structured AD/SB Compare complete (deprecated alias) | registration={registration} | "
            f"ADs={result.total_applicable_ad} ({result.total_ad_with_evidence} with evidence) | "
            f"SBs={result.total_applicable_sb} ({result.total_sb_with_evidence} with evidence)"
        )
        
        return result
        
    except ValueError as e:
        logger.warning(f"Structured AD/SB Compare failed (aircraft alias) | registration={registration} | error={e}")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e)
        )
    except Exception as e:
        logger.error(f"Structured AD/SB Compare error (aircraft alias) | registration={registration} | error={e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to perform structured AD/SB comparison"
        )


# ============================================================
# ENDPOINT: AD/SB OCR AGGREGATION (SCANNED DOCUMENTS ONLY)
# ============================================================
# This endpoint provides aggregated AD/SB references detected
# from OCR scanned documents ONLY.
#
# NO TC baseline, NO compliance, NO recurrence logic.
# Pure factual counting: how many times was this reference seen?
# ============================================================

class OCRScanADSBItem(BaseModel):
    """Single AD/SB reference detected from scanned documents"""
    id: Optional[str] = None  # MongoDB _id of the most recent record (if from adsb_records)
    reference: str
    type: str  # "AD" or "SB"
    title: Optional[str] = None  # Title/description of the AD/SB
    description: Optional[str] = None  # Additional description
    status: Optional[str] = None  # COMPLIED, PENDING, etc.
    occurrence_count: int  # Number of scanned documents where this reference appears
    source: str = "scanned_documents"
    first_seen_date: Optional[str] = None  # Earliest detection date
    last_seen_date: Optional[str] = None  # Most recent detection date
    scan_ids: List[str] = []  # List of OCR scan IDs where detected
    record_ids: List[str] = []  # List of adsb_records _ids for this reference


class OCRScanADSBResponse(BaseModel):
    """
    Aggregated AD/SB references from OCR scanned documents.
    
    TC-SAFE: Pure documentary evidence, no compliance decisions.
    """
    aircraft_id: str
    registration: Optional[str] = None
    items: List[OCRScanADSBItem] = []
    total_unique_references: int = 0
    total_ad: int = 0
    total_sb: int = 0
    documents_analyzed: int = 0
    source: str = "scanned_documents"
    disclaimer: str = (
        "These references were detected by OCR from scanned maintenance documents. "
        "This is DOCUMENTARY EVIDENCE ONLY, not a compliance assessment. "
        "Verify all information with original documents and a licensed AME."
    )


def normalize_adsb_reference(ref: str) -> str:
    """
    Normalize AD/SB reference for aggregation.
    
    - Uppercase
    - Remove extra whitespace
    - Standardize separators
    """
    if not ref:
        return ""
    
    # Uppercase and strip
    normalized = ref.strip().upper()
    
    # Remove multiple spaces
    normalized = re.sub(r'\s+', ' ', normalized)
    
    # Standardize separators (. and space -> -)
    normalized = re.sub(r'[.\s]+', '-', normalized)
    
    # Remove trailing/leading hyphens
    normalized = normalized.strip('-')
    
    return normalized


def is_valid_cf_reference(ref: str) -> bool:
    """
    Validate that reference matches a known AD/SB format.
    
    Supported formats:
    - CF (Canada): CF-YYYY-NN, CF-YY-NN (with optional R revision)
    - US (FAA): YY-NN-NN, YYYY-NN-NN (with optional R revision)
    - EU (EASA): YYYY-NNNN
    - FR (France): F-YYYY-NNN (with optional R revision)
    
    Valid examples:
    - CF-2024-01, CF-90-03R2, CF-1987-15R4
    - 80-11-04, 72-03-03R3, 2022-03-15
    - 2009-0278, 2008-0183
    - F-2005-023, F-2001-139R1
    """
    if not ref:
        return False
    
    # Normalize first
    normalized = ref.strip().upper()
    normalized = re.sub(r'[\s.]+', '-', normalized)
    normalized = normalized.strip('-')
    
    # Pattern 1: CF Canadian format - CF-YYYY-NN or CF-YY-NN (with optional revision)
    cf_pattern = r'^CF-\d{2,4}-\d{1,4}(R\d*)?$'
    if re.match(cf_pattern, normalized):
        return True
    
    # Pattern 2: US FAA format - YY-NN-NN or YYYY-NN-NN (with optional revision)
    # Examples: 80-11-04, 72-03-03R3, 2022-03-15, 2016-16-12
    us_pattern = r'^\d{2,4}-\d{2}-\d{2}(R\d*)?$'
    if re.match(us_pattern, normalized):
        return True
    
    # Pattern 3: EU EASA format - YYYY-NNNN
    # Examples: 2009-0278, 2008-0183
    eu_pattern = r'^\d{4}-\d{4}$'
    if re.match(eu_pattern, normalized):
        return True
    
    # Pattern 4: French format - F-YYYY-NNN (with optional revision)
    # Examples: F-2005-023, F-2001-139R1
    fr_pattern = r'^F-\d{4}-\d{2,4}(R\d*)?$'
    if re.match(fr_pattern, normalized):
        return True
    
    return False


def normalize_to_cf_reference(ref: str) -> Optional[str]:
    """
    Normalize and validate reference to standard format.
    
    Returns normalized reference or None if invalid.
    Preserves the original format type (CF, US, EU, FR).
    """
    if not ref:
        return None
    
    # Normalize
    normalized = ref.strip().upper()
    normalized = re.sub(r'[\s.]+', '-', normalized)
    normalized = normalized.strip('-')
    
    # Remove "AD" or "SB" prefix if present
    normalized = re.sub(r'^(AD|SB)[\s\-]*', '', normalized)
    
    # Try to add CF- prefix if it looks like a Canadian reference without prefix
    # Only do this if it starts with a 2-digit year that could be Canadian
    if not normalized.startswith(('CF-', 'F-')):
        # Check if it's a US-style reference (valid as-is)
        if re.match(r'^\d{2,4}-\d{2}-\d{2}(R\d*)?$', normalized):
            # This is likely US format - keep as is
            pass
        # Check if it's EU format (valid as-is)
        elif re.match(r'^\d{4}-\d{4}$', normalized):
            # This is EU format - keep as is
            pass
        # Check if it could be a CF reference missing the prefix
        elif re.match(r'^\d{2,4}-\d{1,4}(R\d*)?$', normalized):
            # This might be a Canadian reference without CF- prefix
            # Only add CF- if it looks like YY-NN format (not YY-NN-NN)
            parts = normalized.split('-')
            if len(parts) == 2 or (len(parts) == 2 and 'R' in parts[-1]):
                normalized = f"CF-{normalized}"
    
    # Validate against all known patterns
    if is_valid_cf_reference(normalized):
        return normalized
    
    return None


def detect_adsb_type(reference: str) -> str:
    """
    Detect if reference is AD or SB based on common patterns.
    
    Returns: "AD", "SB", or "AD" as default
    """
    ref_upper = reference.upper()
    
    # SB patterns
    sb_patterns = [
        r'^SB[\s\-]',  # Starts with SB
        r'\bSB\d',     # SB followed by number
        r'\bSB-',      # SB-
        r'SERVICE\s*BULLETIN',
        r'^PSB[\s\-]', # Piper Service Bulletin
        r'^CSB[\s\-]', # Cessna Service Bulletin
        r'^SEL[\s\-]', # Service Letter
    ]
    
    for pattern in sb_patterns:
        if re.search(pattern, ref_upper):
            return "SB"
    
    # AD patterns (or default)
    # AD typically starts with "AD", "CF-", year format, etc.
    return "AD"


@router.get(
    "/ocr-scan/{aircraft_id}",
    response_model=OCRScanADSBResponse,
    summary="AD/SB from OCR Scanned Documents [AGGREGATED]",
    description="""
    **AD/SB References from Scanned Documents**
    
    Returns aggregated AD/SB references detected by OCR from 
    scanned maintenance documents.
    
    **Key Features:**
    - One row per unique reference (no duplicates)
    - `occurrence_count`: Number of documents where reference appears
    - NO TC baseline data
    - NO compliance logic
    - NO recurrence calculations
    
    **Source:** OCR Applied documents only (user-validated)
    
    **TC-SAFE:** Documentary evidence only, not compliance assessment.
    """
)
async def get_ocr_scan_adsb(
    aircraft_id: str,
    current_user: User = Depends(get_current_user),
    db=Depends(get_database)
):
    """
    Get aggregated AD/SB references from OCR scanned documents.
    
    Pure counting: how many times was each reference detected?
    No duplicates, deterministic payload.
    """
    logger.info(f"[OCR-SCAN AD/SB] Aggregating | aircraft_id={aircraft_id} | user={current_user.id}")
    
    # Verify aircraft belongs to user
    aircraft = await db.aircrafts.find_one({
        "_id": aircraft_id,
        "user_id": current_user.id
    })
    
    if not aircraft:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Aircraft not found"
        )
    
    registration = aircraft.get("registration")
    
    # Aggregation structure: {normalized_ref: {data}}
    aggregation: Dict[str, Dict[str, Any]] = {}
    documents_analyzed = 0
    
    # Get ONLY APPLIED OCR scans (user-validated documentary evidence)
    cursor = db.ocr_scans.find({
        "aircraft_id": aircraft_id,
        "user_id": current_user.id,
        "status": "APPLIED"
    }).sort("created_at", 1)  # Oldest first for first_seen
    
    async for scan in cursor:
        documents_analyzed += 1
        scan_id = str(scan.get("_id", ""))
        scan_date = scan.get("created_at")
        date_str = scan_date.strftime("%Y-%m-%d") if scan_date else None
        
        extracted_data = scan.get("extracted_data", {})
        adsb_refs = extracted_data.get("ad_sb_references", [])
        
        for ref_idx, ref in enumerate(adsb_refs):
            # Extract reference string and metadata
            if isinstance(ref, dict):
                raw_ref = ref.get("reference_number") or ref.get("identifier") or ref.get("ref") or ""
                ref_type = ref.get("type", "").upper()
                ref_title = ref.get("title") or ref.get("description")
                ref_status = ref.get("status")
            elif isinstance(ref, str):
                raw_ref = ref
                ref_type = ""
                ref_title = None
                ref_status = None
            else:
                continue
            
            if not raw_ref:
                continue
            
            # Normalize reference (supports CF, US, EU, FR formats)
            normalized = normalize_to_cf_reference(raw_ref)
            
            if not normalized:
                # Keep the original reference even if not matching strict pattern
                # This ensures all OCR-detected references are shown
                normalized = normalize_adsb_reference(raw_ref)
                if not normalized:
                    continue
            
            # Detect type if not provided
            if ref_type not in ["AD", "SB"]:
                ref_type = detect_adsb_type(normalized)
            
            # Initialize or update aggregation
            if normalized not in aggregation:
                aggregation[normalized] = {
                    "reference": normalized,
                    "type": ref_type,
                    "occurrence_count": 0,
                    "first_seen_date": date_str,
                    "last_seen_date": date_str,
                    "scan_ids": [],
                    "record_ids": [],
                    "title": ref_title,
                    "description": ref_title,  # Use title as description if available
                    "status": ref_status,
                    "id": scan_id  # Use first scan_id as the main ID for deletion
                }
            
            # Increment count and update tracking
            agg = aggregation[normalized]
            agg["occurrence_count"] += 1
            agg["last_seen_date"] = date_str  # Update to most recent
            
            # Update title/description if not set
            if ref_title and not agg["title"]:
                agg["title"] = ref_title
                agg["description"] = ref_title
            
            if scan_id not in agg["scan_ids"]:
                agg["scan_ids"].append(scan_id)
                # Use scan_ids as record_ids for deletion (from ocr_scans)
                agg["record_ids"].append(scan_id)
    
    # ============================================================
    # ALSO CHECK adsb_records collection for applied OCR AD/SB
    # This contains the actual MongoDB _ids needed for deletion
    # ============================================================
    adsb_cursor = db.adsb_records.find({
        "aircraft_id": aircraft_id,
        "user_id": current_user.id,
        "source": "ocr"  # Only OCR-sourced records
    }).sort("created_at", -1)
    
    async for record in adsb_cursor:
        record_id = str(record.get("_id", ""))
        raw_ref = record.get("reference_number", "")
        ref_type = record.get("adsb_type", "AD").upper()
        title = record.get("title")
        description = record.get("description")
        record_status = record.get("status")
        created_at = record.get("created_at")
        date_str = created_at.strftime("%Y-%m-%d") if created_at else None
        
        if not raw_ref:
            continue
        
        # STRICT VALIDATION: Only CF-YYYY-NN pattern accepted
        normalized = normalize_to_cf_reference(raw_ref)
        
        if not normalized:
            # Invalid reference format - skip it
            logger.debug(f"[OCR-SCAN AD/SB] Skipping invalid adsb_record reference: {raw_ref}")
            continue
        
        # Initialize or update aggregation
        if normalized not in aggregation:
            aggregation[normalized] = {
                "reference": normalized,
                "type": ref_type,
                "occurrence_count": 1,
                "first_seen_date": date_str,
                "last_seen_date": date_str,
                "scan_ids": [],
                "record_ids": [record_id],
                "title": title,
                "description": description,
                "status": record_status,
                "id": record_id  # Most recent _id
            }
        else:
            agg = aggregation[normalized]
            # Add record_id to list
            if record_id not in agg["record_ids"]:
                agg["record_ids"].append(record_id)
            # Update id to most recent if not set
            if not agg["id"]:
                agg["id"] = record_id
            # Update title/description if not set
            if title and not agg["title"]:
                agg["title"] = title
            if description and not agg["description"]:
                agg["description"] = description
            if record_status and not agg["status"]:
                agg["status"] = record_status
    
    # Build response items
    items = [
        OCRScanADSBItem(
            id=data.get("id"),
            reference=data["reference"],
            type=data["type"],
            title=data.get("title"),
            description=data.get("description"),
            status=data.get("status"),
            occurrence_count=data["occurrence_count"],
            source="scanned_documents",
            first_seen_date=data["first_seen_date"],
            last_seen_date=data["last_seen_date"],
            scan_ids=data["scan_ids"],
            record_ids=data.get("record_ids", [])
        )
        for data in aggregation.values()
    ]
    
    # Sort by reference for deterministic output
    items.sort(key=lambda x: x.reference)
    
    # Count AD vs SB
    total_ad = sum(1 for item in items if item.type == "AD")
    total_sb = sum(1 for item in items if item.type == "SB")
    
    # Logging
    logger.info(
        f"[OCR-SCAN AD/SB] aircraft={aircraft_id} | "
        f"unique_refs={len(items)} (AD={total_ad}, SB={total_sb}) | "
        f"documents={documents_analyzed}"
    )
    
    # Debug: Top occurrences
    if items:
        top_items = sorted(items, key=lambda x: x.occurrence_count, reverse=True)[:5]
        top_log = ", ".join([f"{i.reference}({i.occurrence_count})" for i in top_items])
        logger.info(f"[OCR-SCAN AD/SB] Top occurrences: {top_log}")
    
    return OCRScanADSBResponse(
        aircraft_id=aircraft_id,
        registration=registration,
        items=items,
        total_unique_references=len(items),
        total_ad=total_ad,
        total_sb=total_sb,
        documents_analyzed=documents_analyzed,
        source="scanned_documents"
    )


# ============================================================
# ENDPOINT: TC AD/SB vs OCR COMPARISON (BADGES)
# ============================================================
# Compares TC official AD/SB against OCR-detected references.
# Returns a simple badge status: seen_in_documents = true/false
#
# TC-SAFE: No compliance, no regulatory logic, factual only.
# ============================================================

class TCvsOCRBadgeItem(BaseModel):
    """Single TC AD/SB item with OCR visibility badge"""
    reference: str
    type: str  # "AD" or "SB"
    title: Optional[str] = None
    seen_in_documents: bool  # True if detected in any OCR scan
    occurrence_count: int = 0  # How many times seen in OCR
    last_seen_date: Optional[str] = None  # Most recent OCR detection


class TCvsOCRBadgeResponse(BaseModel):
    """
    TC AD/SB vs OCR Comparison Response.
    
    TC-SAFE: Documentary visibility check only.
    No compliance, no regulatory decisions.
    """
    aircraft_id: str
    registration: Optional[str] = None
    items: List[TCvsOCRBadgeItem] = []
    total_tc_references: int = 0
    total_seen: int = 0
    total_not_seen: int = 0
    ocr_documents_analyzed: int = 0
    source: str = "tc_imported_references"
    disclaimer: str = (
        "This comparison shows which TC AD/SB references have been detected "
        "in scanned maintenance documents. This is DOCUMENTARY VISIBILITY ONLY, "
        "not a compliance assessment. Verify with original documents and a licensed AME."
    )


def normalize_reference_for_comparison(ref: str) -> str:
    """
    Normalize AD/SB reference for comparison matching.
    
    - Uppercase
    - Remove extra whitespace
    - Remove common separators for loose matching
    """
    if not ref:
        return ""
    
    normalized = ref.strip().upper()
    
    # Remove multiple spaces
    normalized = re.sub(r'\s+', '', normalized)
    
    # Remove common separators for comparison
    normalized = re.sub(r'[.\-_/]', '', normalized)
    
    return normalized


def references_match(tc_ref: str, ocr_ref: str) -> bool:
    """
    Check if TC reference matches OCR reference.
    
    Uses normalized comparison for flexibility.
    Also checks if one contains the other (partial match).
    """
    tc_norm = normalize_reference_for_comparison(tc_ref)
    ocr_norm = normalize_reference_for_comparison(ocr_ref)
    
    if not tc_norm or not ocr_norm:
        return False
    
    # Exact match
    if tc_norm == ocr_norm:
        return True
    
    # Partial match (TC ref contained in OCR or vice versa)
    # This handles cases like "CF-90-03" matching "CF-90-03R2"
    if len(tc_norm) >= 4 and len(ocr_norm) >= 4:
        if tc_norm in ocr_norm or ocr_norm in tc_norm:
            return True
    
    return False


@router.get(
    "/tc-comparison/{aircraft_id}",
    response_model=TCvsOCRBadgeResponse,
    summary="TC AD/SB vs OCR Comparison [BADGES]",
    description="""
    **TC AD/SB vs OCR Comparison**
    
    Compares Transport Canada AD/SB references against 
    OCR-detected references from scanned documents.
    
    **For each TC AD/SB:**
    - `seen_in_documents`: True if detected in any OCR scan
    - `occurrence_count`: Number of OCR documents where detected
    - `last_seen_date`: Most recent detection date
    
    **Data Sources:**
    - TC: `tc_imported_references` collection (user-imported)
    - OCR: `ocr_scans` with status=APPLIED
    
    **TC-SAFE:** Documentary visibility only, no compliance.
    """
)
async def get_tc_vs_ocr_comparison(
    aircraft_id: str,
    current_user: User = Depends(get_current_user),
    db=Depends(get_database)
):
    """
    Compare TC AD/SB references against OCR-detected references.
    
    Returns badge status for each TC reference.
    """
    logger.info(f"[TC-VS-OCR] Comparison | aircraft_id={aircraft_id} | user={current_user.id}")
    
    # Verify aircraft belongs to user
    aircraft = await db.aircrafts.find_one({
        "_id": aircraft_id,
        "user_id": current_user.id
    })
    
    if not aircraft:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Aircraft not found"
        )
    
    registration = aircraft.get("registration")
    
    # ============================================================
    # STEP 1: Get OCR-detected references (from APPLIED scans)
    # ============================================================
    ocr_references: Dict[str, Dict[str, Any]] = {}  # {normalized_ref: {dates, count}}
    ocr_documents_analyzed = 0
    
    cursor = db.ocr_scans.find({
        "aircraft_id": aircraft_id,
        "user_id": current_user.id,
        "status": "APPLIED"
    })
    
    async for scan in cursor:
        ocr_documents_analyzed += 1
        scan_date = scan.get("created_at")
        date_str = scan_date.strftime("%Y-%m-%d") if scan_date else None
        
        extracted_data = scan.get("extracted_data", {})
        adsb_refs = extracted_data.get("ad_sb_references", [])
        
        for ref in adsb_refs:
            if isinstance(ref, dict):
                raw_ref = ref.get("reference_number") or ref.get("identifier") or ref.get("ref") or ""
            elif isinstance(ref, str):
                raw_ref = ref
            else:
                continue
            
            if not raw_ref:
                continue
            
            normalized = normalize_reference_for_comparison(raw_ref)
            
            if normalized not in ocr_references:
                ocr_references[normalized] = {
                    "original": raw_ref,
                    "dates": [],
                    "count": 0
                }
            
            ocr_references[normalized]["count"] += 1
            if date_str and date_str not in ocr_references[normalized]["dates"]:
                ocr_references[normalized]["dates"].append(date_str)
    
    # ============================================================
    # STEP 2: Get TC AD/SB references (user-imported)
    # ============================================================
    items: List[TCvsOCRBadgeItem] = []
    
    async for tc_ref in db.tc_imported_references.find({"aircraft_id": aircraft_id}):
        identifier = tc_ref.get("identifier", "")
        ref_type = tc_ref.get("type", "AD")
        title = tc_ref.get("title")
        
        if not identifier:
            continue
        
        # Check if this TC reference was seen in OCR
        seen_in_documents = False
        occurrence_count = 0
        last_seen_date = None
        
        tc_normalized = normalize_reference_for_comparison(identifier)
        
        for ocr_norm, ocr_data in ocr_references.items():
            if references_match(identifier, ocr_data["original"]) or tc_normalized == ocr_norm:
                seen_in_documents = True
                occurrence_count += ocr_data["count"]
                
                # Get most recent date
                if ocr_data["dates"]:
                    sorted_dates = sorted(ocr_data["dates"], reverse=True)
                    if not last_seen_date or sorted_dates[0] > last_seen_date:
                        last_seen_date = sorted_dates[0]
        
        items.append(TCvsOCRBadgeItem(
            reference=identifier,
            type=ref_type,
            title=title,
            seen_in_documents=seen_in_documents,
            occurrence_count=occurrence_count,
            last_seen_date=last_seen_date
        ))
    
    # Sort by reference for deterministic output
    items.sort(key=lambda x: x.reference)
    
    # Calculate counts
    total_seen = sum(1 for item in items if item.seen_in_documents)
    total_not_seen = len(items) - total_seen
    
    # Logging
    logger.info(
        f"[TC-VS-OCR] aircraft={aircraft_id} | "
        f"TC refs={len(items)} | seen={total_seen} | not_seen={total_not_seen} | "
        f"OCR docs={ocr_documents_analyzed}"
    )
    
    return TCvsOCRBadgeResponse(
        aircraft_id=aircraft_id,
        registration=registration,
        items=items,
        total_tc_references=len(items),
        total_seen=total_seen,
        total_not_seen=total_not_seen,
        ocr_documents_analyzed=ocr_documents_analyzed,
        source="tc_imported_references"
    )
