"""
TC AD/SB Detection Routes

Endpoints for the monthly TC AD/SB detection mechanism.

Provides:
- Manual trigger endpoint (admin/maintenance)
- Scheduled job endpoint (monthly)
- Alert status check
- Audit log access

TC-SAFE: All operations are informational only.
"""

from fastapi import APIRouter, Depends, HTTPException, status, Query
from motor.motor_asyncio import AsyncIOMotorDatabase
from typing import Optional
import logging

from database.mongodb import get_database
from services.auth_deps import get_current_user
from services.tc_adsb_detection_service import TCADSBDetectionService
from models.tc_adsb_alert import (
    DetectionTriggerRequest,
    DetectionSummaryResponse,
    MarkReviewedResponse,
    AircraftADSBAlertStatus,
)
from models.user import User

router = APIRouter(prefix="/api/tc-adsb", tags=["tc-adsb-detection"])
logger = logging.getLogger(__name__)


# ============================================================
# DETECTION ENDPOINTS
# ============================================================

@router.post(
    "/detect",
    response_model=DetectionSummaryResponse,
    summary="Trigger TC AD/SB detection for current user's aircraft",
    description="""
    Manually trigger TC AD/SB detection for all aircraft owned by the current user.
    
    **Use cases:**
    - After manual TC data import
    - Testing detection logic
    - Troubleshooting
    
    **TC-SAFE:** This is informational only. No compliance decisions are made.
    """
)
async def trigger_detection_user(
    request: Optional[DetectionTriggerRequest] = None,
    current_user: User = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(get_database)
):
    """
    Trigger TC AD/SB detection for current user's aircraft.
    """
    logger.info(f"Manual TC AD/SB detection triggered by user {current_user.id}")
    
    service = TCADSBDetectionService(db)
    
    tc_version = request.tc_adsb_version if request else None
    force = request.force_all if request else False
    
    try:
        result = await service.run_detection_for_user(
            user_id=current_user.id,
            tc_version=tc_version,
            force=force,
            triggered_by=f"user:{current_user.id}"
        )
        return result
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        logger.error(f"Detection failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Detection failed. Check logs for details."
        )


@router.post(
    "/detect-all",
    response_model=DetectionSummaryResponse,
    summary="Trigger TC AD/SB detection for ALL aircraft (admin)",
    description="""
    Trigger TC AD/SB detection for ALL aircraft in the system.
    
    **Intended for:**
    - Scheduled monthly job after TC import
    - Admin maintenance tasks
    
    **TC-SAFE:** This is informational only. No compliance decisions are made.
    """
)
async def trigger_detection_all(
    request: Optional[DetectionTriggerRequest] = None,
    current_user: User = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(get_database)
):
    """
    Trigger TC AD/SB detection for ALL aircraft (system-wide).
    """
    logger.info(f"System-wide TC AD/SB detection triggered by user {current_user.id}")
    
    service = TCADSBDetectionService(db)
    
    tc_version = request.tc_adsb_version if request else None
    force = request.force_all if request else False
    
    try:
        result = await service.run_detection_all_aircraft(
            tc_version=tc_version,
            force=force,
            triggered_by=f"admin:{current_user.id}"
        )
        return result
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        logger.error(f"System-wide detection failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Detection failed. Check logs for details."
        )


@router.post(
    "/detect-scheduled",
    response_model=DetectionSummaryResponse,
    summary="Monthly scheduled TC AD/SB detection",
    description="""
    Endpoint for scheduled monthly detection job.
    
    Called after each monthly TC AD/SB data import.
    
    **Trigger methods:**
    - Render cron job
    - Internal job scheduler
    - Manual trigger after import
    
    **TC-SAFE:** This is informational only.
    """
)
async def scheduled_detection(
    tc_version: Optional[str] = Query(
        None,
        description="TC AD/SB version (defaults to current)"
    ),
    api_key: Optional[str] = Query(
        None,
        description="API key for scheduled job authentication"
    ),
    db: AsyncIOMotorDatabase = Depends(get_database)
):
    """
    Monthly scheduled detection endpoint.
    
    Can be called without user auth (for cron jobs) using API key.
    """
    # Note: In production, validate api_key against environment variable
    # For now, we allow the call but log it
    logger.info(f"Scheduled TC AD/SB detection triggered, version={tc_version}")
    
    service = TCADSBDetectionService(db)
    
    try:
        result = await service.run_detection_all_aircraft(
            tc_version=tc_version,
            force=False,
            triggered_by="scheduled"
        )
        return result
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        logger.error(f"Scheduled detection failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Scheduled detection failed. Check logs for details."
        )


# ============================================================
# ALERT MANAGEMENT ENDPOINTS
# ============================================================

@router.get(
    "/alert/{aircraft_id}",
    response_model=AircraftADSBAlertStatus,
    summary="Get AD/SB alert status for an aircraft",
    description="""
    Get the current TC AD/SB alert status for a specific aircraft.
    
    **Returns:**
    - `adsb_has_new_tc_items`: Whether new TC items exist
    - `count_new_adsb`: Number of new items
    - `last_tc_adsb_version`: Last TC version checked
    - `last_adsb_reviewed_at`: When user last reviewed AD/SB module
    """
)
async def get_alert_status(
    aircraft_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(get_database)
):
    """
    Get AD/SB alert status for an aircraft.
    """
    service = TCADSBDetectionService(db)
    
    try:
        result = await service.get_alert_status(aircraft_id, current_user.id)
        return AircraftADSBAlertStatus(**result)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e)
        )


@router.post(
    "/mark-reviewed/{aircraft_id}",
    response_model=MarkReviewedResponse,
    summary="Mark AD/SB module as reviewed",
    description="""
    Mark the AD/SB module as reviewed for an aircraft.
    
    **Called when:** User opens/views the AD/SB module.
    
    **Effect:**
    - Sets `last_adsb_reviewed_at` to current timestamp
    - Clears `adsb_has_new_tc_items` flag
    - Resets `count_new_adsb` to 0
    
    **Audit:** This action is logged for traceability.
    """
)
async def mark_reviewed(
    aircraft_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(get_database)
):
    """
    Mark AD/SB as reviewed for an aircraft.
    
    Clears the alert flag and records the review timestamp.
    """
    logger.info(f"Mark AD/SB reviewed | aircraft={aircraft_id} | user={current_user.id}")
    
    service = TCADSBDetectionService(db)
    
    try:
        result = await service.mark_adsb_reviewed(aircraft_id, current_user.id)
        return result
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e)
        )


# ============================================================
# AUDIT LOG ENDPOINTS
# ============================================================

@router.get(
    "/audit-log",
    summary="Get TC AD/SB detection audit log",
    description="""
    Get audit log entries for TC AD/SB detection events.
    
    **Events logged:**
    - Detection started/completed
    - New items found per aircraft
    - Alert cleared on review
    - Errors and skipped aircraft
    
    **Filtering:** Optional filter by aircraft_id
    """
)
async def get_audit_log(
    aircraft_id: Optional[str] = Query(
        None,
        description="Filter by aircraft ID"
    ),
    limit: int = Query(
        100,
        ge=1,
        le=500,
        description="Maximum entries to return"
    ),
    current_user: User = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(get_database)
):
    """
    Get TC AD/SB detection audit log.
    """
    service = TCADSBDetectionService(db)
    
    entries = await service.get_audit_log(
        aircraft_id=aircraft_id,
        limit=limit
    )
    
    return {
        "total": len(entries),
        "entries": entries
    }


# ============================================================
# TC VERSION INFO
# ============================================================

@router.get(
    "/version",
    summary="Get current TC AD/SB data version",
    description="Get the current TC AD/SB data version used for detection."
)
async def get_tc_version(
    db: AsyncIOMotorDatabase = Depends(get_database)
):
    """
    Get current TC AD/SB data version.
    """
    service = TCADSBDetectionService(db)
    version = await service.get_current_tc_version()
    
    return {
        "tc_adsb_version": version,
        "description": "TC AD/SB data version used for detection"
    }
