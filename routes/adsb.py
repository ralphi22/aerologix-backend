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
    # PDF import metadata (USER_IMPORTED_REFERENCE only)
    pdf_available: bool = False
    pdf_filename: Optional[str] = None
    # Stable IDs for USER_IMPORTED_REFERENCE (for delete/view operations)
    tc_reference_id: Optional[str] = None  # MongoDB _id as string
    tc_pdf_id: Optional[str] = None  # PDF storage identifier
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
    # PATCH: ADD USER-IMPORTED AD/SB FROM TC_PDF_IMPORT
    # ============================================================
    # These are references imported manually by user via PDF upload.
    # They are linked to this specific aircraft, not by type/model.
    # TC-SAFE: Marked as USER_IMPORTED_REFERENCE, not canonical TC data.
    
    existing_ad_refs = {item.identifier for item in ad_list}
    existing_sb_refs = {item.identifier for item in sb_list}
    
    user_imported_ad_count = 0
    user_imported_sb_count = 0
    ghost_ad_filtered = 0
    ghost_sb_filtered = 0
    
    # Query AD imported for this specific aircraft via PDF
    async for ad in db.tc_ad.find({
        "source": "TC_PDF_IMPORT",
        "import_aircraft_id": aircraft_id,
        "is_active": True
    }):
        identifier = ad.get("ref", "")
        
        # Skip if already in canonical baseline (union by ref)
        if identifier in existing_ad_refs:
            continue
        
        # FILTER GHOST ADs: Must have valid import metadata
        import_filename = ad.get("import_filename") or ad.get("last_import_filename")
        if not import_filename:
            ghost_ad_filtered += 1
            logger.debug(f"[AD/SB BASELINE] Filtered ghost AD: {identifier} (no import_filename)")
            continue
        
        norm_id = service._normalize_identifier(identifier)
        
        # Count OCR occurrences (same logic as canonical)
        count_seen = 0
        all_dates = []
        for ocr_ref, dates in ocr_references.items():
            if service._identifiers_match(norm_id, ocr_ref):
                count_seen += len(dates)
                all_dates.extend(dates)
        
        sorted_dates = sorted(set(all_dates), reverse=True)
        last_seen = sorted_dates[0] if sorted_dates else None
        
        eff_date = ad.get("effective_date")
        eff_str = eff_date.strftime("%Y-%m-%d") if hasattr(eff_date, 'strftime') else str(eff_date)[:10] if eff_date else None
        
        # Get stable IDs for USER_IMPORTED_REFERENCE
        tc_reference_id = str(ad.get("_id")) if ad.get("_id") else ad.get("ref")
        created_at = ad.get("created_at")
        imported_at_str = created_at.isoformat() if hasattr(created_at, 'isoformat') else str(created_at) if created_at else None
        
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
            origin="USER_IMPORTED_REFERENCE",
            pdf_available=True,
            pdf_filename=import_filename,
            tc_reference_id=tc_reference_id,
            tc_pdf_id=ad.get("pdf_storage_path"),
            imported_at=imported_at_str,
        ))
        user_imported_ad_count += 1
    
    # Query SB imported for this specific aircraft via PDF
    async for sb in db.tc_sb.find({
        "source": "TC_PDF_IMPORT",
        "import_aircraft_id": aircraft_id,
        "is_active": True
    }):
        identifier = sb.get("ref", "")
        
        # Skip if already in canonical baseline
        if identifier in existing_sb_refs:
            continue
        
        # FILTER GHOST SBs: Must have valid import metadata
        import_filename = sb.get("import_filename") or sb.get("last_import_filename")
        if not import_filename:
            ghost_sb_filtered += 1
            logger.debug(f"[AD/SB BASELINE] Filtered ghost SB: {identifier} (no import_filename)")
            continue
        
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
        
        # Get stable IDs for USER_IMPORTED_REFERENCE
        tc_reference_id = str(sb.get("_id")) if sb.get("_id") else sb.get("ref")
        created_at = sb.get("created_at")
        imported_at_str = created_at.isoformat() if hasattr(created_at, 'isoformat') else str(created_at) if created_at else None
        
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
            origin="USER_IMPORTED_REFERENCE",
            pdf_available=True,
            pdf_filename=import_filename,
            tc_reference_id=tc_reference_id,
            tc_pdf_id=sb.get("pdf_storage_path"),
            imported_at=imported_at_str,
        ))
        user_imported_sb_count += 1
    
    # Log filtered ghosts
    if ghost_ad_filtered > 0 or ghost_sb_filtered > 0:
        logger.info(
            f"[AD/SB BASELINE] Filtered {ghost_ad_filtered} ghost AD, {ghost_sb_filtered} ghost SB "
            f"(missing import_filename)"
        )
    
    # Log user-imported additions (TC-SAFE audit)
    if user_imported_ad_count > 0 or user_imported_sb_count > 0:
        logger.info(
            f"[AD/SB BASELINE] +{user_imported_ad_count} AD, +{user_imported_sb_count} SB "
            f"user-imported references added from TC_PDF_IMPORT"
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
