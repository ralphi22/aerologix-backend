"""
Maintenance Records Routes for AeroLogix AI
"""

from fastapi import APIRouter, Depends, HTTPException, status
from typing import List, Optional
from datetime import datetime
from bson import ObjectId
from database.mongodb import get_database
from services.auth_deps import get_current_user
from models.maintenance import (
    MaintenanceRecord, MaintenanceRecordCreate, 
    MaintenanceRecordUpdate, MaintenanceType
)
from models.user import User
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/maintenance", tags=["maintenance"])


@router.post("", response_model=dict)
async def create_maintenance_record(
    record: MaintenanceRecordCreate,
    current_user: User = Depends(get_current_user),
    db=Depends(get_database)
):
    """Create a new maintenance record"""
    
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
    doc["maintenance_type"] = doc["maintenance_type"].value if isinstance(doc["maintenance_type"], MaintenanceType) else doc["maintenance_type"]
    doc["created_at"] = now
    doc["updated_at"] = now
    
    result = await db.maintenance_records.insert_one(doc)
    
    return {
        "id": str(result.inserted_id),
        "message": "Maintenance record created successfully"
    }


@router.get("/{aircraft_id}", response_model=List[dict])
async def get_maintenance_records(
    aircraft_id: str,
    limit: int = 50,
    current_user: User = Depends(get_current_user),
    db=Depends(get_database)
):
    """Get all maintenance records for an aircraft"""
    
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
    
    cursor = db.maintenance_records.find({
        "aircraft_id": aircraft_id,
        "user_id": current_user.id
    }).sort("date", -1).limit(limit)
    
    records = []
    async for record in cursor:
        record["_id"] = str(record["_id"])
        records.append(record)
    
    return records


@router.get("/record/{record_id}", response_model=dict)
async def get_maintenance_record(
    record_id: str,
    current_user: User = Depends(get_current_user),
    db=Depends(get_database)
):
    """Get a specific maintenance record"""
    
    record = await db.maintenance_records.find_one({
        "_id": record_id,
        "user_id": current_user.id
    })
    
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Maintenance record not found"
        )
    
    record["_id"] = str(record["_id"])
    return record


@router.put("/record/{record_id}", response_model=dict)
async def update_maintenance_record(
    record_id: str,
    update_data: MaintenanceRecordUpdate,
    current_user: User = Depends(get_current_user),
    db=Depends(get_database)
):
    """Update a maintenance record"""
    
    record = await db.maintenance_records.find_one({
        "_id": record_id,
        "user_id": current_user.id
    })
    
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Maintenance record not found"
        )
    
    update_dict = {k: v for k, v in update_data.model_dump().items() if v is not None}
    
    if "maintenance_type" in update_dict and isinstance(update_dict["maintenance_type"], MaintenanceType):
        update_dict["maintenance_type"] = update_dict["maintenance_type"].value
    
    update_dict["updated_at"] = datetime.utcnow()
    
    await db.maintenance_records.update_one(
        {"_id": record_id},
        {"$set": update_dict}
    )
    
    return {"message": "Maintenance record updated successfully"}


@router.delete("/record/{record_id}")
async def delete_maintenance_record(
    record_id: str,
    current_user: User = Depends(get_current_user),
    db=Depends(get_database)
):
    """Delete a maintenance record"""
    
    result = await db.maintenance_records.delete_one({
        "_id": record_id,
        "user_id": current_user.id
    })
    
    if result.deleted_count == 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Maintenance record not found"
        )
    
    return {"message": "Maintenance record deleted successfully"}
