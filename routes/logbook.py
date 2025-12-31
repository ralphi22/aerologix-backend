"""
Log Book Routes - Official flight log entries (TC-SAFE documentary only)
This is a REGISTER view - shows only what has been recorded by humans.
Never shows how entries were generated.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from motor.motor_asyncio import AsyncIOMotorDatabase
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime
import logging

from database.mongodb import get_database
from services.auth_deps import get_current_user
from models.user import User

router = APIRouter(prefix="/api/aircraft", tags=["logbook"])
logger = logging.getLogger(__name__)


class LogBookEntry(BaseModel):
    id: str
    date: datetime
    tt_airframe: float
    tt_engine: float
    tt_propeller: float
    description: str
    references: Optional[str] = None
    pilot_label: Optional[str] = None  # Informational only, not legal
    author: str
    attachments: Optional[List[str]] = None
    created_at: datetime


class ManualLogEntryCreate(BaseModel):
    date: datetime
    tt_airframe: float
    tt_engine: float
    tt_propeller: float
    description: str
    references: Optional[str] = None
    pilot_label: Optional[str] = None
    attachments: Optional[List[str]] = None


def generate_id():
    import time
    return str(int(time.time() * 1000000))


async def verify_aircraft_access(db: AsyncIOMotorDatabase, aircraft_id: str, user_id: str) -> dict:
    """Verify user has access to the aircraft"""
    aircraft = await db.aircrafts.find_one({"_id": aircraft_id, "user_id": user_id})
    if aircraft:
        return aircraft
    
    share = await db.aircraft_shares.find_one({
        "aircraft_id": aircraft_id,
        "mechanic_user_id": user_id,
        "status": "active"
    })
    if share:
        aircraft = await db.aircrafts.find_one({"_id": aircraft_id})
        return aircraft
    
    return None


@router.get("/{aircraft_id}/logbook", response_model=List[LogBookEntry])
async def get_logbook(
    aircraft_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    current_user: User = Depends(get_current_user)
):
    """
    Get the official logbook for an aircraft.
    TC-SAFE: Documentary view only - shows recorded entries.
    Combines confirmed flights and manual entries in chronological order.
    """
    aircraft = await verify_aircraft_access(db, aircraft_id, current_user.id)
    if not aircraft:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Aéronef non trouvé ou accès refusé"
        )
    
    entries = []
    
    # 1. Get confirmed flight candidates (normalized to log entries)
    confirmed_flights = await db.flight_candidates.find({
        "aircraft_id": aircraft_id,
        "status": "CONFIRMED",
        "soft_deleted_at": None
    }).to_list(500)
    
    # Get running TT at each confirmation point
    # Start with base hours from aircraft
    base_airframe = aircraft.get("airframe_hours", 0)
    base_engine = aircraft.get("engine_hours", 0)
    base_propeller = aircraft.get("propeller_hours", 0)
    
    # Calculate cumulative hours for each confirmed flight
    cumulative_hours = 0
    for flight in sorted(confirmed_flights, key=lambda x: x.get("confirmed_at") or x["created_at"]):
        flight_hours = flight["duration_est_minutes"] / 60.0
        cumulative_hours += flight_hours
        
        # Get confirming user name
        author = "Propriétaire"
        if flight.get("confirmed_by_user_id"):
            user = await db.users.find_one({"_id": flight["confirmed_by_user_id"]})
            if user:
                author = user.get("name", "Propriétaire")
        
        # Build description
        departure = flight.get("departure_icao") or "---"
        arrival = flight.get("arrival_icao") or "---"
        duration_min = flight["duration_est_minutes"]
        hours = duration_min // 60
        mins = duration_min % 60
        duration_str = f"{hours}h{mins:02d}" if hours > 0 else f"{mins}min"
        
        description = f"{departure} → {arrival} ({duration_str})"
        
        entries.append(LogBookEntry(
            id=flight["_id"],
            date=flight.get("confirmed_at") or flight["depart_ts"],
            tt_airframe=round(base_airframe - cumulative_hours + flight_hours + cumulative_hours, 1),
            tt_engine=round(base_engine - cumulative_hours + flight_hours + cumulative_hours, 1),
            tt_propeller=round(base_propeller - cumulative_hours + flight_hours + cumulative_hours, 1),
            description=description,
            references=None,
            pilot_label=None,
            author=author,
            attachments=None,
            created_at=flight.get("confirmed_at") or flight["created_at"]
        ))
    
    # 2. Get manual log entries
    manual_entries = await db.logbook_entries.find({
        "aircraft_id": aircraft_id,
        "soft_deleted_at": None
    }).to_list(500)
    
    for entry in manual_entries:
        author = "Propriétaire"
        if entry.get("user_id"):
            user = await db.users.find_one({"_id": entry["user_id"]})
            if user:
                author = user.get("name", "Propriétaire")
        
        entries.append(LogBookEntry(
            id=entry["_id"],
            date=entry["date"],
            tt_airframe=entry.get("tt_airframe", 0),
            tt_engine=entry.get("tt_engine", 0),
            tt_propeller=entry.get("tt_propeller", 0),
            description=entry.get("description", ""),
            references=entry.get("references"),
            pilot_label=entry.get("pilot_label"),
            author=author,
            attachments=entry.get("attachments"),
            created_at=entry["created_at"]
        ))
    
    # Sort all entries by date (most recent first)
    entries.sort(key=lambda x: x.date, reverse=True)
    
    return entries


@router.post("/{aircraft_id}/logbook", response_model=LogBookEntry)
async def add_manual_logbook_entry(
    aircraft_id: str,
    entry: ManualLogEntryCreate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    current_user: User = Depends(get_current_user)
):
    """
    Add a manual entry to the logbook.
    TC-SAFE: Documentary entry only.
    """
    aircraft = await verify_aircraft_access(db, aircraft_id, current_user.id)
    if not aircraft:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Aéronef non trouvé ou accès refusé"
        )
    
    # Check if user is owner (not just shared access for manual entries)
    if aircraft.get("user_id") != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Seul le propriétaire peut ajouter des entrées manuelles"
        )
    
    entry_id = generate_id()
    now = datetime.utcnow()
    
    entry_doc = {
        "_id": entry_id,
        "aircraft_id": aircraft_id,
        "user_id": current_user.id,
        "date": entry.date,
        "tt_airframe": entry.tt_airframe,
        "tt_engine": entry.tt_engine,
        "tt_propeller": entry.tt_propeller,
        "description": entry.description,
        "references": entry.references,
        "pilot_label": entry.pilot_label,
        "attachments": entry.attachments,
        "soft_deleted_at": None,
        "created_at": now,
        "updated_at": now
    }
    
    await db.logbook_entries.insert_one(entry_doc)
    
    logger.info(f"Manual logbook entry added for aircraft {aircraft_id} by {current_user.email}")
    
    return LogBookEntry(
        id=entry_id,
        date=entry.date,
        tt_airframe=entry.tt_airframe,
        tt_engine=entry.tt_engine,
        tt_propeller=entry.tt_propeller,
        description=entry.description,
        references=entry.references,
        pilot_label=entry.pilot_label,
        author=current_user.name,
        attachments=entry.attachments,
        created_at=now
    )


@router.put("/{aircraft_id}/logbook/{entry_id}", response_model=LogBookEntry)
async def update_logbook_entry(
    aircraft_id: str,
    entry_id: str,
    entry: ManualLogEntryCreate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    current_user: User = Depends(get_current_user)
):
    """
    Update a manual logbook entry.
    Only manual entries can be edited.
    """
    aircraft = await verify_aircraft_access(db, aircraft_id, current_user.id)
    if not aircraft:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Aéronef non trouvé"
        )
    
    if aircraft.get("user_id") != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Seul le propriétaire peut modifier les entrées"
        )
    
    # Check if it's a manual entry
    existing = await db.logbook_entries.find_one({
        "_id": entry_id,
        "aircraft_id": aircraft_id,
        "soft_deleted_at": None
    })
    
    if not existing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Entrée non trouvée ou non modifiable"
        )
    
    await db.logbook_entries.update_one(
        {"_id": entry_id},
        {
            "$set": {
                "date": entry.date,
                "tt_airframe": entry.tt_airframe,
                "tt_engine": entry.tt_engine,
                "tt_propeller": entry.tt_propeller,
                "description": entry.description,
                "references": entry.references,
                "pilot_label": entry.pilot_label,
                "attachments": entry.attachments,
                "updated_at": datetime.utcnow()
            }
        }
    )
    
    return LogBookEntry(
        id=entry_id,
        date=entry.date,
        tt_airframe=entry.tt_airframe,
        tt_engine=entry.tt_engine,
        tt_propeller=entry.tt_propeller,
        description=entry.description,
        references=entry.references,
        pilot_label=entry.pilot_label,
        author=current_user.name,
        attachments=entry.attachments,
        created_at=existing["created_at"]
    )


@router.delete("/{aircraft_id}/logbook/{entry_id}")
async def delete_logbook_entry(
    aircraft_id: str,
    entry_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    current_user: User = Depends(get_current_user)
):
    """
    Soft delete a manual logbook entry.
    """
    aircraft = await verify_aircraft_access(db, aircraft_id, current_user.id)
    if not aircraft:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Aéronef non trouvé"
        )
    
    if aircraft.get("user_id") != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Seul le propriétaire peut supprimer les entrées"
        )
    
    result = await db.logbook_entries.update_one(
        {"_id": entry_id, "aircraft_id": aircraft_id},
        {"$set": {"soft_deleted_at": datetime.utcnow()}}
    )
    
    if result.modified_count == 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Entrée non trouvée"
        )
    
    return {"message": "Entrée supprimée"}
