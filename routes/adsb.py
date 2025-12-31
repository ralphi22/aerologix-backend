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
    """Get all AD/SB records for an aircraft"""
    
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
    
    cursor = db.adsb_records.find(query).sort("created_at", -1)
    
    records = []
    async for record in cursor:
        record["_id"] = str(record["_id"])
        records.append(record)
    
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
    """Delete an AD/SB record"""
    
    # Try to convert to ObjectId if it's a valid format, otherwise use string
    try:
        query_id = ObjectId(record_id)
    except Exception:
        query_id = record_id
    
    result = await db.adsb_records.delete_one({
        "_id": query_id,
        "user_id": current_user.id
    })
    
    if result.deleted_count == 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="AD/SB record not found"
        )
    
    return {"message": "AD/SB record deleted successfully"}


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
