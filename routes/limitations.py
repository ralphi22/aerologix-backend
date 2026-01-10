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
