from fastapi import APIRouter, Depends, HTTPException, status
from motor.motor_asyncio import AsyncIOMotorDatabase
from database.mongodb import get_database
from models.aircraft import Aircraft, AircraftCreate, AircraftUpdate
from models.user import User
from routes.auth import get_current_user
from datetime import datetime
from typing import List
import logging

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/aircraft", tags=["aircraft"])

def format_registration(registration: str) -> str:
    """Format registration to uppercase"""
    return registration.upper().strip()

@router.post("", response_model=Aircraft, status_code=status.HTTP_201_CREATED)
async def create_aircraft(
    aircraft: AircraftCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(get_database)
):
    """Create a new aircraft for the current user"""
    # Check user aircraft limit based on plan
    if current_user.limits.max_aircrafts != -1:  # -1 = unlimited
        user_aircraft_count = await db.aircrafts.count_documents({"user_id": current_user.id})
        if user_aircraft_count >= current_user.limits.max_aircrafts:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Aircraft limit reached. Your plan allows {current_user.limits.max_aircrafts} aircraft(s). Upgrade your plan to add more."
            )
    
    # Format registration to uppercase
    registration = format_registration(aircraft.registration)
    
    # Check if registration already exists for this user
    existing = await db.aircrafts.find_one({
        "user_id": current_user.id,
        "registration": registration
    })
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Aircraft with registration {registration} already exists"
        )
    
    # Create aircraft document
    aircraft_id = str(datetime.utcnow().timestamp()).replace(".", "")
    aircraft_dict = {
        "_id": aircraft_id,
        "user_id": current_user.id,
        "registration": registration,
        "aircraft_type": aircraft.aircraft_type,
        "manufacturer": aircraft.manufacturer,
        "model": aircraft.model,
        "year": aircraft.year,
        "serial_number": aircraft.serial_number,
        "airframe_hours": aircraft.airframe_hours,
        "engine_hours": aircraft.engine_hours,
        "propeller_hours": aircraft.propeller_hours,
        "photo_url": aircraft.photo_url,
        "description": aircraft.description,
        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow()
    }
    
    await db.aircrafts.insert_one(aircraft_dict)
    logger.info(f"Aircraft {registration} created for user {current_user.email}")
    
    return Aircraft(**aircraft_dict)

@router.get("", response_model=List[Aircraft])
async def get_user_aircraft(
    current_user: User = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(get_database)
):
    """Get all aircraft for the current user"""
    cursor = db.aircrafts.find({"user_id": current_user.id}).sort("created_at", -1)
    aircraft_list = await cursor.to_list(length=100)
    return [Aircraft(**aircraft) for aircraft in aircraft_list]

@router.get("/{aircraft_id}", response_model=Aircraft)
async def get_aircraft(
    aircraft_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(get_database)
):
    """Get a specific aircraft by ID"""
    aircraft_doc = await db.aircrafts.find_one({
        "_id": aircraft_id,
        "user_id": current_user.id
    })
    
    if not aircraft_doc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Aircraft not found"
        )
    
    return Aircraft(**aircraft_doc)

@router.put("/{aircraft_id}", response_model=Aircraft)
async def update_aircraft(
    aircraft_id: str,
    aircraft_update: AircraftUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(get_database)
):
    """Update an aircraft"""
    # Check if aircraft exists and belongs to user
    existing = await db.aircrafts.find_one({
        "_id": aircraft_id,
        "user_id": current_user.id
    })
    
    if not existing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Aircraft not found"
        )
    
    # Build update dict (only non-None values)
    update_data = aircraft_update.dict(exclude_unset=True)
    
    # Format registration if provided
    if "registration" in update_data:
        update_data["registration"] = format_registration(update_data["registration"])
    
    if update_data:
        update_data["updated_at"] = datetime.utcnow()
        await db.aircrafts.update_one(
            {"_id": aircraft_id},
            {"$set": update_data}
        )
    
    # Fetch updated aircraft
    updated_aircraft = await db.aircrafts.find_one({"_id": aircraft_id})
    logger.info(f"Aircraft {aircraft_id} updated for user {current_user.email}")
    
    return Aircraft(**updated_aircraft)

@router.delete("/{aircraft_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_aircraft(
    aircraft_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(get_database)
):
    """Delete an aircraft"""
    result = await db.aircrafts.delete_one({
        "_id": aircraft_id,
        "user_id": current_user.id
    })
    
    if result.deleted_count == 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Aircraft not found"
        )
    
    logger.info(f"Aircraft {aircraft_id} deleted for user {current_user.email}")
    return None


# ==================== FLIGHT TRACKING TOGGLE ====================

@router.post("/{aircraft_id}/flight-tracking")
async def toggle_flight_tracking(
    aircraft_id: str,
    enabled: bool,
    current_user: User = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(get_database)
):
    """
    Enable or disable flight tracking for an aircraft.
    Only the owner can toggle this setting.
    """
    aircraft = await db.aircrafts.find_one({
        "_id": aircraft_id,
        "user_id": current_user.id
    })
    
    if not aircraft:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Aéronef non trouvé"
        )
    
    await db.aircrafts.update_one(
        {"_id": aircraft_id},
        {"$set": {
            "flight_tracking_enabled": enabled,
            "updated_at": datetime.utcnow()
        }}
    )
    
    logger.info(f"Flight tracking {'enabled' if enabled else 'disabled'} for {aircraft_id}")
    return {"flight_tracking_enabled": enabled}


@router.get("/{aircraft_id}/flight-tracking")
async def get_flight_tracking_status(
    aircraft_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(get_database)
):
    """Get flight tracking status for an aircraft."""
    aircraft = await db.aircrafts.find_one({"_id": aircraft_id})
    
    if not aircraft:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Aéronef non trouvé"
        )
    
    # Verify access (owner or shared)
    is_owner = aircraft.get("user_id") == current_user.id
    if not is_owner:
        share = await db.aircraft_shares.find_one({
            "aircraft_id": aircraft_id,
            "mechanic_user_id": current_user.id,
            "status": "active"
        })
        if not share:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Accès refusé")
    
    return {"flight_tracking_enabled": aircraft.get("flight_tracking_enabled", False)}


# ==================== PILOT SHARING (up to 5) ====================

@router.get("/{aircraft_id}/pilots")
async def get_aircraft_pilots(
    aircraft_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(get_database)
):
    """Get pilots shared on this aircraft (max 5)."""
    aircraft = await db.aircrafts.find_one({
        "_id": aircraft_id,
        "user_id": current_user.id
    })
    
    if not aircraft:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Aéronef non trouvé"
        )
    
    pilots = await db.aircraft_pilots.find({
        "aircraft_id": aircraft_id,
        "status": "active"
    }).to_list(10)
    
    result = []
    for pilot in pilots:
        user = await db.users.find_one({"_id": pilot["user_id"]})
        result.append({
            "id": pilot["_id"],
            "user_id": pilot["user_id"],
            "email": user.get("email") if user else None,
            "pilot_label": pilot.get("pilot_label", ""),
            "status": pilot["status"],
            "created_at": pilot["created_at"]
        })
    
    return result


@router.post("/{aircraft_id}/pilots")
async def add_pilot(
    aircraft_id: str,
    email: str,
    pilot_label: str = "",
    current_user: User = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(get_database)
):
    """
    Invite a pilot to contribute to this aircraft's log book.
    Maximum 5 pilots per aircraft.
    """
    # Verify ownership
    aircraft = await db.aircrafts.find_one({
        "_id": aircraft_id,
        "user_id": current_user.id
    })
    
    if not aircraft:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Aéronef non trouvé"
        )
    
    # Check pilot limit
    pilot_count = await db.aircraft_pilots.count_documents({
        "aircraft_id": aircraft_id,
        "status": "active"
    })
    
    if pilot_count >= 5:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Maximum 5 pilotes par aéronef"
        )
    
    # Find pilot user
    pilot_user = await db.users.find_one({"email": email.lower()})
    if not pilot_user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Utilisateur non trouvé avec cet email"
        )
    
    if pilot_user["_id"] == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Vous êtes déjà le propriétaire"
        )
    
    # Check if already added
    existing = await db.aircraft_pilots.find_one({
        "aircraft_id": aircraft_id,
        "user_id": pilot_user["_id"],
        "status": "active"
    })
    
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Ce pilote est déjà ajouté"
        )
    
    pilot_id = str(datetime.utcnow().timestamp()).replace(".", "")
    pilot_doc = {
        "_id": pilot_id,
        "aircraft_id": aircraft_id,
        "user_id": pilot_user["_id"],
        "owner_user_id": current_user.id,
        "pilot_label": pilot_label or pilot_user.get("name", "Pilote"),
        "status": "active",
        "created_at": datetime.utcnow()
    }
    
    await db.aircraft_pilots.insert_one(pilot_doc)
    logger.info(f"Pilot {email} added to aircraft {aircraft_id}")
    
    return {
        "id": pilot_id,
        "user_id": pilot_user["_id"],
        "email": email,
        "pilot_label": pilot_doc["pilot_label"],
        "status": "active"
    }


@router.delete("/{aircraft_id}/pilots/{pilot_id}")
async def remove_pilot(
    aircraft_id: str,
    pilot_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(get_database)
):
    """Remove a pilot from the aircraft."""
    # Verify ownership
    aircraft = await db.aircrafts.find_one({
        "_id": aircraft_id,
        "user_id": current_user.id
    })
    
    if not aircraft:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Aéronef non trouvé"
        )
    
    result = await db.aircraft_pilots.update_one(
        {"_id": pilot_id, "aircraft_id": aircraft_id},
        {"$set": {"status": "removed"}}
    )
    
    if result.modified_count == 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Pilote non trouvé"
        )
    
    logger.info(f"Pilot {pilot_id} removed from aircraft {aircraft_id}")
    return {"message": "Pilote retiré"}

