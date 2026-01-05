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
    """Get all STC records for an aircraft - READS DIRECTLY FROM DB"""
    
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
    
    # DIRECT DB READ - NO CACHE, NO OCR RECONSTRUCTION
    cursor = db.stc_records.find({
        "aircraft_id": aircraft_id,
        "user_id": current_user.id
    }).sort("installation_date", -1)
    
    records = []
    async for record in cursor:
        record["_id"] = str(record["_id"])
        records.append(record)
    
    # LOG: GET stc count
    logger.info(f"GET stc | aircraft={aircraft_id} | count={len(records)}")
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
    """Delete an STC record - PERMANENT DELETION by _id only"""
    return await _delete_stc_by_id(record_id, current_user, db)


@router.delete("/{stc_id}")
async def delete_stc_direct(
    stc_id: str,
    current_user: User = Depends(get_current_user),
    db=Depends(get_database)
):
    """Delete an STC record by ID - PERMANENT DELETION (frontend route)"""
    return await _delete_stc_by_id(stc_id, current_user, db)


async def _delete_stc_by_id(
    record_id: str,
    current_user: User,
    db
):
    """Internal function to delete an STC by _id - ATOMIC OPERATION (same pattern as OCR delete)"""
    
    # Try to find by string ID first
    record = await db.stc_records.find_one({
        "_id": record_id,
        "user_id": current_user.id
    })
    
    if not record:
        # Try with ObjectId
        try:
            record = await db.stc_records.find_one({
                "_id": ObjectId(record_id),
                "user_id": current_user.id
            })
        except:
            pass
    
    if not record:
        logger.warning(f"DELETE FAILED | reason=not_found_or_not_owner | collection=stc | id={record_id} | user={current_user.id}")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="STC record not found"
        )
    
    # DELETE using the EXACT _id from the found document (same as OCR delete)
    if isinstance(record["_id"], ObjectId):
        result = await db.stc_records.delete_one({"_id": record["_id"]})
    else:
        result = await db.stc_records.delete_one({"_id": record_id})
    
    if result.deleted_count == 0:
        logger.warning(f"DELETE FAILED | reason=delete_count_zero | collection=stc | id={record_id} | user={current_user.id}")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="STC record not found or already deleted"
        )
    
    # DELETE CONFIRMED log - MANDATORY
    logger.info(f"DELETE CONFIRMED | collection=stc | id={record_id} | user={current_user.id}")
    
    return {"message": "STC record deleted successfully", "deleted_id": record_id}
