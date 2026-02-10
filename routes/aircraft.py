from fastapi import APIRouter, Depends, HTTPException, status
from motor.motor_asyncio import AsyncIOMotorDatabase
from database.mongodb import get_database
from models.aircraft import Aircraft, AircraftCreate, AircraftUpdate
from models.user import User
from routes.auth import get_current_user
from datetime import datetime
from typing import List, Optional
import logging
import re

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/aircraft", tags=["aircraft"])

# Default values for TC fields
DEFAULT_PURPOSE = "Non spécifié"
DEFAULT_BASE_CITY = "Non spécifié"


def apply_default_values(aircraft_doc: dict) -> dict:
    """
    Apply default values for TC fields if they are null or missing.
    
    Ensures purpose and city_airport are ALWAYS present in the response.
    """
    if aircraft_doc is None:
        return aircraft_doc
    
    # Ensure purpose has a value
    if not aircraft_doc.get("purpose"):
        aircraft_doc["purpose"] = DEFAULT_PURPOSE
    
    # Ensure city_airport has a value
    if not aircraft_doc.get("city_airport"):
        aircraft_doc["city_airport"] = DEFAULT_BASE_CITY
    
    return aircraft_doc


def format_registration(registration: str) -> str:
    """Format registration to uppercase"""
    return registration.upper().strip()


def normalize_registration_for_tc(registration: str) -> str:
    """
    Normalize registration for TC lookup.
    Input: C-FGSO or CFGSO or FGSO
    Output: CFGSO
    """
    reg = registration.strip().upper().replace("-", "")
    if not reg.startswith("C") and reg.isalpha():
        reg = "C" + reg
    return reg


async def fetch_tc_data(db, registration: str) -> dict:
    """
    Fetch TC data for a registration.
    
    Returns dict with purpose and city_airport from TC database.
    """
    try:
        reg_norm = normalize_registration_for_tc(registration)
        
        # Query TC aircraft collection
        tc_doc = await db.tc_aircraft.find_one({"registration_norm": reg_norm})
        
        if not tc_doc:
            # Try with registration field directly
            tc_doc = await db.tc_aircraft.find_one({"registration": registration.upper()})
        
        if not tc_doc:
            # Try alternative field names (French)
            tc_doc = await db.tc_aircraft.find_one({"immatriculation": reg_norm})
        
        if not tc_doc:
            logger.info(f"[TC LOOKUP] No TC data found for {reg_norm}")
            return {}
        
        # Log what we found for debugging
        logger.info(f"[TC LOOKUP] Found document for {reg_norm}: purpose={tc_doc.get('purpose')}, city_airport={tc_doc.get('city_airport')}")
        
        # Map fields - check English names FIRST, then French fallbacks
        def get_field(*keys):
            for key in keys:
                if key in tc_doc and tc_doc[key]:
                    return tc_doc[key]
            return None
        
        result = {
            # English first, French fallback
            "purpose": get_field("purpose", "but"),
            "city_airport": get_field("city_airport", "aéroport de la ville"),
            "manufacturer": get_field("manufacturer", "constructeur"),
            "model": get_field("model", "modèle"),
            "serial_number": get_field("serial_number", "numéro_de_série"),
            "year": get_field("date_manufacture", "date_fabrication", "year"),
        }
        
        logger.info(f"[TC LOOKUP] Mapped TC data for {reg_norm}: purpose={result.get('purpose')}, city_airport={result.get('city_airport')}")
        
        return {k: v for k, v in result.items() if v is not None}
        
    except Exception as e:
        logger.warning(f"[TC LOOKUP] Error fetching TC data: {e}")
        return {}

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
        # TC-sourced fields
        "purpose": aircraft.purpose,
        "base_city": aircraft.base_city,
        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow()
    }
    
    await db.aircrafts.insert_one(aircraft_dict)
    logger.info(f"Aircraft {registration} created for user {current_user.email}")
    
    # Apply default values before returning
    aircraft_dict = apply_default_values(aircraft_dict)
    return Aircraft(**aircraft_dict)

@router.get("", response_model=List[Aircraft])
async def get_user_aircraft(
    current_user: User = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(get_database)
):
    """Get all aircraft for the current user"""
    cursor = db.aircrafts.find({"user_id": current_user.id}).sort("created_at", -1)
    aircraft_list = await cursor.to_list(length=100)
    
    # Apply default values to each aircraft
    return [Aircraft(**apply_default_values(aircraft)) for aircraft in aircraft_list]

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
    
    # Apply default values before returning
    aircraft_doc = apply_default_values(aircraft_doc)
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
    
    # ============================================================
    # COUNTER GUARD: AIRFRAME is the master counter
    # - engine_hours and propeller_hours cannot exceed airframe_hours
    # - Silently normalize without error
    # ============================================================
    if update_data:
        # Determine the effective airframe_hours (new value or existing)
        if "airframe_hours" in update_data and update_data["airframe_hours"] is not None:
            master_airframe = update_data["airframe_hours"]
        else:
            master_airframe = existing.get("airframe_hours", 0.0)
        
        # GUARD: Engine hours cannot exceed airframe
        if "engine_hours" in update_data and update_data["engine_hours"] is not None:
            if update_data["engine_hours"] > master_airframe:
                logger.warning(
                    f"[COUNTER_GUARD] aircraft={aircraft_id} | "
                    f"engine_hours ({update_data['engine_hours']}) > airframe_hours ({master_airframe}) — normalized"
                )
                update_data["engine_hours"] = master_airframe
        
        # GUARD: Propeller hours cannot exceed airframe
        if "propeller_hours" in update_data and update_data["propeller_hours"] is not None:
            if update_data["propeller_hours"] > master_airframe:
                logger.warning(
                    f"[COUNTER_GUARD] aircraft={aircraft_id} | "
                    f"propeller_hours ({update_data['propeller_hours']}) > airframe_hours ({master_airframe}) — normalized"
                )
                update_data["propeller_hours"] = master_airframe
        
        update_data["updated_at"] = datetime.utcnow()
        await db.aircrafts.update_one(
            {"_id": aircraft_id},
            {"$set": update_data}
        )
    
    # Fetch updated aircraft
    updated_aircraft = await db.aircrafts.find_one({"_id": aircraft_id})
    logger.info(f"Aircraft {aircraft_id} updated for user {current_user.email}")
    
    # Apply default values before returning
    updated_aircraft = apply_default_values(updated_aircraft)
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


# ==================== TC DATA SYNC ====================

@router.post("/{aircraft_id}/sync-tc-data")
async def sync_tc_data(
    aircraft_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(get_database)
):
    """
    Synchronize aircraft data with Transport Canada database.
    
    Updates purpose, base_city, and other TC fields if available.
    This does NOT overwrite user-entered data unless specified.
    """
    # Check if aircraft exists and belongs to user
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
            detail="Aircraft has no registration"
        )
    
    # Fetch TC data
    tc_data = await fetch_tc_data(db, registration)
    
    if not tc_data:
        return {
            "ok": True,
            "synced": False,
            "message": f"No TC data found for {registration}",
            "fields_updated": []
        }
    
    # Only update fields that are currently empty/null
    update_data = {}
    fields_updated = []
    
    # purpose
    if tc_data.get("purpose") and not aircraft.get("purpose"):
        update_data["purpose"] = tc_data["purpose"]
        fields_updated.append("purpose")
    
    # base_city
    if tc_data.get("base_city") and not aircraft.get("base_city"):
        update_data["base_city"] = tc_data["base_city"]
        fields_updated.append("base_city")
    
    # manufacturer
    if tc_data.get("manufacturer") and not aircraft.get("manufacturer"):
        update_data["manufacturer"] = tc_data["manufacturer"]
        fields_updated.append("manufacturer")
    
    # model
    if tc_data.get("model") and not aircraft.get("model"):
        update_data["model"] = tc_data["model"]
        fields_updated.append("model")
    
    # serial_number
    if tc_data.get("serial_number") and not aircraft.get("serial_number"):
        update_data["serial_number"] = tc_data["serial_number"]
        fields_updated.append("serial_number")
    
    # Apply updates
    if update_data:
        update_data["updated_at"] = datetime.utcnow()
        await db.aircrafts.update_one(
            {"_id": aircraft_id},
            {"$set": update_data}
        )
        logger.info(f"[TC SYNC] Aircraft {aircraft_id} updated with TC data: {fields_updated}")
    
    return {
        "ok": True,
        "synced": bool(update_data),
        "message": f"TC data synced for {registration}" if update_data else "No new data to sync",
        "fields_updated": fields_updated,
        "tc_data": tc_data
    }


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

