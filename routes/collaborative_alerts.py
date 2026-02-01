"""
Collaborative AD/SB Alerts Routes

Endpoints for managing collaborative AD/SB alerts.

ENDPOINTS:
- GET /api/alerts/adsb - Get user's AD/SB alerts
- PUT /api/alerts/adsb/{alert_id}/read - Mark alert as read
- PUT /api/alerts/adsb/{alert_id}/dismiss - Dismiss alert
- GET /api/alerts/adsb/count - Get unread alert count
- GET /api/alerts - ALIAS for frontend compatibility

TC-SAFE: Informational alerts only, no compliance decisions.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from typing import List, Optional
from pydantic import BaseModel, Field
from datetime import datetime, timezone
from bson import ObjectId
import logging

from database.mongodb import get_database
from services.auth_deps import get_current_user
from models.user import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/alerts", tags=["alerts"])


# ============================================================
# RESPONSE MODELS
# ============================================================

class ADSBAlertItem(BaseModel):
    """Single AD/SB alert"""
    id: str
    type: str = "NEW_AD_SB"
    aircraft_id: str
    aircraft_type_key: str  # CANONICAL: manufacturer::model
    manufacturer: Optional[str] = None
    model: Optional[str] = None
    reference: str
    reference_type: str
    message: str
    status: str  # UNREAD, READ, DISMISSED
    created_at: str


class ADSBAlertListResponse(BaseModel):
    """Response for listing alerts"""
    alerts: List[ADSBAlertItem]
    total_count: int
    unread_count: int


class AlertCountResponse(BaseModel):
    """Response for alert count"""
    unread_count: int
    total_count: int


# ============================================================
# ENDPOINTS
# ============================================================

@router.get(
    "",
    response_model=ADSBAlertListResponse,
    summary="Get user's AD/SB alerts"
)
async def get_alerts(
    status_filter: Optional[str] = None,  # UNREAD, READ, DISMISSED
    limit: int = 50,
    current_user: User = Depends(get_current_user),
    db=Depends(get_database)
):
    """
    Get all AD/SB alerts for the current user.
    
    Optionally filter by status (UNREAD, READ, DISMISSED).
    """
    logger.info(f"[ADSB ALERTS] Get alerts | user={current_user.id} | filter={status_filter}")
    
    # Build query
    query = {"user_id": current_user.id}
    
    if status_filter:
        query["status"] = status_filter.upper()
    
    # Get alerts
    cursor = db.tc_adsb_alerts.find(query).sort("created_at", -1).limit(limit)
    docs = await cursor.to_list(length=limit)
    
    alerts = []
    for doc in docs:
        created_at = doc.get("created_at")
        created_at_str = created_at.isoformat() if hasattr(created_at, 'isoformat') else str(created_at)
        
        alerts.append(ADSBAlertItem(
            id=str(doc["_id"]),
            type=doc.get("type", "NEW_AD_SB"),
            aircraft_id=doc.get("aircraft_id", ""),
            aircraft_type_key=doc.get("aircraft_type_key", ""),  # CANONICAL KEY
            manufacturer=doc.get("manufacturer"),
            model=doc.get("model"),
            reference=doc.get("reference", ""),
            reference_type=doc.get("reference_type", "AD"),
            message=doc.get("message", ""),
            status=doc.get("status", "UNREAD"),
            created_at=created_at_str
        ))
    
    # Get counts
    total_count = await db.tc_adsb_alerts.count_documents({"user_id": current_user.id})
    unread_count = await db.tc_adsb_alerts.count_documents({
        "user_id": current_user.id,
        "status": "UNREAD"
    })
    
    return ADSBAlertListResponse(
        alerts=alerts,
        total_count=total_count,
        unread_count=unread_count
    )


@router.get(
    "/count",
    response_model=AlertCountResponse,
    summary="Get alert count"
)
async def get_alert_count(
    current_user: User = Depends(get_current_user),
    db=Depends(get_database)
):
    """Get unread and total alert counts."""
    unread_count = await db.tc_adsb_alerts.count_documents({
        "user_id": current_user.id,
        "status": "UNREAD"
    })
    
    total_count = await db.tc_adsb_alerts.count_documents({
        "user_id": current_user.id,
        "status": {"$ne": "DISMISSED"}
    })
    
    return AlertCountResponse(
        unread_count=unread_count,
        total_count=total_count
    )


@router.put(
    "/{alert_id}/read",
    summary="Mark alert as read"
)
async def mark_alert_read(
    alert_id: str,
    current_user: User = Depends(get_current_user),
    db=Depends(get_database)
):
    """Mark an alert as read."""
    try:
        obj_id = ObjectId(alert_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid alert_id")
    
    # Find and update
    result = await db.tc_adsb_alerts.update_one(
        {"_id": obj_id, "user_id": current_user.id},
        {
            "$set": {
                "status": "READ",
                "read_at": datetime.now(timezone.utc)
            }
        }
    )
    
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Alert not found")
    
    logger.info(f"[ADSB ALERTS] Marked as read | alert={alert_id} | user={current_user.id}")
    
    return {"ok": True, "status": "READ"}


@router.put(
    "/{alert_id}/dismiss",
    summary="Dismiss alert"
)
async def dismiss_alert(
    alert_id: str,
    current_user: User = Depends(get_current_user),
    db=Depends(get_database)
):
    """Dismiss an alert (hide from list)."""
    try:
        obj_id = ObjectId(alert_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid alert_id")
    
    # Find and update
    result = await db.tc_adsb_alerts.update_one(
        {"_id": obj_id, "user_id": current_user.id},
        {
            "$set": {
                "status": "DISMISSED",
                "dismissed_at": datetime.now(timezone.utc)
            }
        }
    )
    
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Alert not found")
    
    logger.info(f"[ADSB ALERTS] Dismissed | alert={alert_id} | user={current_user.id}")
    
    return {"ok": True, "status": "DISMISSED"}


@router.put(
    "/read-all",
    summary="Mark all alerts as read"
)
async def mark_all_read(
    current_user: User = Depends(get_current_user),
    db=Depends(get_database)
):
    """Mark all unread alerts as read."""
    result = await db.tc_adsb_alerts.update_many(
        {"user_id": current_user.id, "status": "UNREAD"},
        {
            "$set": {
                "status": "READ",
                "read_at": datetime.now(timezone.utc)
            }
        }
    )
    
    logger.info(f"[ADSB ALERTS] Marked all as read | user={current_user.id} | count={result.modified_count}")
    
    return {"ok": True, "marked_count": result.modified_count}


# ============================================================
# GLOBAL REFERENCE POOL STATS (ADMIN/DEBUG)
# ============================================================

@router.get(
    "/global-stats",
    summary="Get global reference pool statistics"
)
async def get_global_stats(
    current_user: User = Depends(get_current_user),
    db=Depends(get_database)
):
    """Get statistics about the global reference pool (debug endpoint)."""
    
    # Count by model
    pipeline = [
        {"$group": {
            "_id": "$model_normalized",
            "count": {"$sum": 1},
            "models": {"$addToSet": "$model_original"}
        }},
        {"$sort": {"count": -1}},
        {"$limit": 20}
    ]
    
    model_stats = []
    async for doc in db.tc_adsb_global_references.aggregate(pipeline):
        model_stats.append({
            "model": doc["_id"],
            "reference_count": doc["count"],
            "original_models": doc["models"][:5]  # Limit variations
        })
    
    total_refs = await db.tc_adsb_global_references.count_documents({})
    total_alerts = await db.tc_adsb_alerts.count_documents({})
    
    return {
        "total_global_references": total_refs,
        "total_alerts_created": total_alerts,
        "top_models": model_stats,
        "disclaimer": "TC-SAFE: Statistics only, no compliance inference"
    }
