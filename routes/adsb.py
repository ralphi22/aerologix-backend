"""
AD/SB (Airworthiness Directives / Service Bulletins) Routes for AeroLogix AI
"""

from fastapi import APIRouter, Depends, HTTPException, status
from typing import List, Optional
from datetime import datetime
from bson import ObjectId
from database.mongodb import get_database
from services.auth_deps import get_current_user
from models.adsb import (
    ADSBRecord, ADSBRecordCreate, ADSBRecordUpdate,
    ADSBType, ADSBStatus
)
from models.user import User
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/adsb", tags=["adsb"])


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
