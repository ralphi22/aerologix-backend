"""
STC (Supplemental Type Certificate) Routes for AeroLogix AI
"""

from fastapi import APIRouter, Depends, HTTPException, status
from typing import List
from datetime import datetime
from bson import ObjectId
from database.mongodb import get_database
from services.auth_deps import get_current_user
from models.stc import STCRecord, STCRecordCreate, STCRecordUpdate
from models.user import User
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/stc", tags=["stc"])


@router.post("", response_model=dict)
async def create_stc_record(
    record: STCRecordCreate,
    current_user: User = Depends(get_current_user),
    db=Depends(get_database)
):
    """Create a new STC record"""
    
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
    doc["created_at"] = now
    doc["updated_at"] = now
    
    result = await db.stc_records.insert_one(doc)
    
    return {
        "id": str(result.inserted_id),
        "message": "STC record created successfully"
    }


@router.get("/{aircraft_id}", response_model=List[dict])
async def get_stc_records(
    aircraft_id: str,
    current_user: User = Depends(get_current_user),
    db=Depends(get_database)
):
    """Get all STC records for an aircraft"""
    
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
    
    cursor = db.stc_records.find({
        "aircraft_id": aircraft_id,
        "user_id": current_user.id
    }).sort("installation_date", -1)
    
    records = []
    async for record in cursor:
        record["_id"] = str(record["_id"])
        records.append(record)
    
    return records


@router.get("/record/{record_id}", response_model=dict)
async def get_stc_record(
    record_id: str,
    current_user: User = Depends(get_current_user),
    db=Depends(get_database)
):
    """Get a specific STC record"""
    
    record = await db.stc_records.find_one({
        "_id": record_id,
        "user_id": current_user.id
    })
    
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="STC record not found"
        )
    
    record["_id"] = str(record["_id"])
    return record


@router.put("/record/{record_id}", response_model=dict)
async def update_stc_record(
    record_id: str,
    update_data: STCRecordUpdate,
    current_user: User = Depends(get_current_user),
    db=Depends(get_database)
):
    """Update an STC record"""
    
    record = await db.stc_records.find_one({
        "_id": record_id,
        "user_id": current_user.id
    })
    
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="STC record not found"
        )
    
    update_dict = {k: v for k, v in update_data.model_dump().items() if v is not None}
    update_dict["updated_at"] = datetime.utcnow()
    
    await db.stc_records.update_one(
        {"_id": record_id},
        {"$set": update_dict}
    )
    
    return {"message": "STC record updated successfully"}


@router.delete("/record/{record_id}")
async def delete_stc_record(
    record_id: str,
    current_user: User = Depends(get_current_user),
    db=Depends(get_database)
):
    """Delete an STC record"""
    
    result = await db.stc_records.delete_one({
        "_id": record_id,
        "user_id": current_user.id
    })
    
    if result.deleted_count == 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="STC record not found"
        )
    
    return {"message": "STC record deleted successfully"}
