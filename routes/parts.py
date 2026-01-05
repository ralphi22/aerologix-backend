"""
Parts Records Routes for AeroLogix AI
"""

from fastapi import APIRouter, Depends, HTTPException, status
from typing import List, Optional
from datetime import datetime
from bson import ObjectId
from database.mongodb import get_database
from services.auth_deps import get_current_user
from models.parts import PartRecord, PartRecordCreate, PartRecordUpdate
from models.user import User
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/parts", tags=["parts"])


@router.get("/{aircraft_id}", response_model=List[dict])
async def get_parts_by_aircraft(
    aircraft_id: str,
    current_user: User = Depends(get_current_user),
    db=Depends(get_database)
):
    """Get all parts for an aircraft (frontend route) - READS DIRECTLY FROM DB"""
    
    # Verify aircraft belongs to user
    aircraft = await db.aircrafts.find_one({
        "_id": aircraft_id,
        "user_id": current_user.id
    })
    
    if not aircraft:
        # Return empty list instead of 404 for frontend compatibility
        logger.info(f"GET parts | aircraft={aircraft_id} | count=0 (aircraft not found)")
        return []
    
    # Get parts sorted by created_at desc - DIRECT DB READ, NO CACHE, NO OCR RECONSTRUCTION
    cursor = db.part_records.find({
        "aircraft_id": aircraft_id,
        "user_id": current_user.id
    }).sort("created_at", -1)
    
    records = []
    async for record in cursor:
        record["_id"] = str(record["_id"])
        records.append(record)
    
    # LOG: GET parts count
    logger.info(f"GET parts | aircraft={aircraft_id} | count={len(records)}")
    return records


@router.post("", response_model=dict)
async def create_part_record(
    record: PartRecordCreate,
    current_user: User = Depends(get_current_user),
    db=Depends(get_database)
):
    """Create a new part record"""
    
    # If aircraft_id provided, verify it belongs to user
    if record.aircraft_id:
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
    
    result = await db.part_records.insert_one(doc)
    
    return {
        "id": str(result.inserted_id),
        "message": "Part record created successfully"
    }


@router.get("/aircraft/{aircraft_id}", response_model=List[dict])
async def get_aircraft_parts(
    aircraft_id: str,
    installed_only: bool = False,
    current_user: User = Depends(get_current_user),
    db=Depends(get_database)
):
    """Get all parts for an aircraft"""
    
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
    
    if installed_only:
        query["installed_on_aircraft"] = True
    
    cursor = db.part_records.find(query).sort("installation_date", -1)
    
    records = []
    async for record in cursor:
        record["_id"] = str(record["_id"])
        records.append(record)
    
    return records


@router.get("/inventory", response_model=List[dict])
async def get_inventory_parts(
    current_user: User = Depends(get_current_user),
    db=Depends(get_database)
):
    """Get all inventory parts (not installed on aircraft)"""
    
    cursor = db.part_records.find({
        "user_id": current_user.id,
        "$or": [
            {"aircraft_id": None},
            {"installed_on_aircraft": False}
        ]
    }).sort("created_at", -1)
    
    records = []
    async for record in cursor:
        record["_id"] = str(record["_id"])
        records.append(record)
    
    return records


@router.get("/record/{record_id}", response_model=dict)
async def get_part_record(
    record_id: str,
    current_user: User = Depends(get_current_user),
    db=Depends(get_database)
):
    """Get a specific part record"""
    
    record = await db.part_records.find_one({
        "_id": record_id,
        "user_id": current_user.id
    })
    
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Part record not found"
        )
    
    record["_id"] = str(record["_id"])
    return record


@router.put("/record/{record_id}", response_model=dict)
async def update_part_record(
    record_id: str,
    update_data: PartRecordUpdate,
    current_user: User = Depends(get_current_user),
    db=Depends(get_database)
):
    """Update a part record"""
    
    record = await db.part_records.find_one({
        "_id": record_id,
        "user_id": current_user.id
    })
    
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Part record not found"
        )
    
    update_dict = {k: v for k, v in update_data.model_dump().items() if v is not None}
    update_dict["updated_at"] = datetime.utcnow()
    
    await db.part_records.update_one(
        {"_id": record_id},
        {"$set": update_dict}
    )
    
    return {"message": "Part record updated successfully"}


@router.put("/record/{record_id}/confirm", response_model=dict)
async def confirm_part_record(
    record_id: str,
    current_user: User = Depends(get_current_user),
    db=Depends(get_database)
):
    """Confirm an OCR part record (makes it undeletable)"""
    
    record = await db.part_records.find_one({
        "_id": record_id,
        "user_id": current_user.id
    })
    
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Part record not found"
        )
    
    await db.part_records.update_one(
        {"_id": record_id},
        {"$set": {"confirmed": True, "updated_at": datetime.utcnow()}}
    )
    
    return {"message": "Part record confirmed successfully"}


@router.delete("/record/{record_id}")
async def delete_part_record(
    record_id: str,
    current_user: User = Depends(get_current_user),
    db=Depends(get_database)
):
    """Delete a part record - PERMANENT DELETION by _id only"""
    
    # Try BOTH ObjectId and string formats for _id lookup
    query_id_objectid = None
    query_id_string = record_id
    
    try:
        query_id_objectid = ObjectId(record_id)
    except Exception:
        pass
    
    # First, try to find the record (try ObjectId first, then string)
    record = None
    actual_query_id = None
    
    if query_id_objectid:
        record = await db.part_records.find_one({
            "_id": query_id_objectid,
            "user_id": current_user.id
        })
        if record:
            actual_query_id = query_id_objectid
    
    if not record:
        record = await db.part_records.find_one({
            "_id": query_id_string,
            "user_id": current_user.id
        })
        if record:
            actual_query_id = query_id_string
    
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Part record not found"
        )
    
    # AVIATION SAFETY RULE: Only OCR/invoice parts can be deleted (manual parts are protected)
    source = record.get("source", "manual")
    
    if source not in ["ocr", "ocr_invoice"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Suppression interdite — Les pièces saisies manuellement ne peuvent pas être supprimées."
        )
    
    # Get aircraft_id for logging before deletion
    aircraft_id = record.get("aircraft_id")
    
    # PERMANENT DELETE - ONLY THIS SPECIFIC RECORD by _id
    result = await db.part_records.delete_one({
        "_id": actual_query_id,
        "user_id": current_user.id
    })
    
    if result.deleted_count == 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Part record not found or already deleted"
        )
    
    # DELETE AUDIT log - MANDATORY
    logger.info(f"DELETE AUDIT | collection=parts | id={record_id} | user={current_user.id}")
    logger.info(f"DELETE CONFIRMED | collection=parts | id={record_id}")
    
    return {"message": "Part record deleted successfully", "deleted_id": record_id}


@router.get("/search")
async def search_parts(
    part_number: Optional[str] = None,
    name: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db=Depends(get_database)
):
    """Search parts by part number or name"""
    
    query = {"user_id": current_user.id}
    
    if part_number:
        query["part_number"] = {"$regex": part_number, "$options": "i"}
    
    if name:
        query["name"] = {"$regex": name, "$options": "i"}
    
    cursor = db.part_records.find(query).limit(50)
    
    records = []
    async for record in cursor:
        record["_id"] = str(record["_id"])
        records.append(record)
    
    return records
