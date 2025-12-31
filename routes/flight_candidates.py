"""
Flight Candidates Routes - Proposed flights detection and confirmation
TC-SAFE: Estimated flights only - user must confirm before affecting hours
"""

from fastapi import APIRouter, Depends, HTTPException, status, Query
from motor.motor_asyncio import AsyncIOMotorDatabase
from pydantic import BaseModel, Field
from typing import List, Optional
from datetime import datetime
from enum import Enum
import logging

from database.mongodb import get_database
from services.auth_deps import get_current_user
from models.user import User

router = APIRouter(prefix="/api", tags=["flight-candidates"])
logger = logging.getLogger(__name__)


class FlightStatus(str, Enum):
    PROPOSED = "PROPOSED"
    CONFIRMED = "CONFIRMED"
    IGNORED = "IGNORED"


class FlightCandidateCreate(BaseModel):
    departure_icao: Optional[str] = None
    arrival_icao: Optional[str] = None
    depart_ts: datetime
    arrival_ts: datetime
    duration_est_minutes: int
    source: str = "gps_estimate"


class FlightCandidateEdit(BaseModel):
    departure_icao: Optional[str] = None
    arrival_icao: Optional[str] = None
    depart_ts: Optional[datetime] = None
    arrival_ts: Optional[datetime] = None
    duration_est_minutes: Optional[int] = None


class FlightCandidateResponse(BaseModel):
    id: str
    aircraft_id: str
    status: FlightStatus
    departure_icao: Optional[str]
    arrival_icao: Optional[str]
    depart_ts: datetime
    arrival_ts: datetime
    duration_est_minutes: int
    source: str
    pilot_label: Optional[str] = None
    created_by_user_id: Optional[str]
    confirmed_by_user_id: Optional[str]
    created_at: datetime
    confirmed_at: Optional[datetime]


def generate_id():
    import time
    return str(int(time.time() * 1000000))


async def verify_aircraft_access(db: AsyncIOMotorDatabase, aircraft_id: str, user_id: str) -> dict:
    """Verify user has access to the aircraft (owner or shared)"""
    # Check ownership
    aircraft = await db.aircrafts.find_one({"_id": aircraft_id, "user_id": user_id})
    if aircraft:
        return aircraft
    
    # Check shared access
    share = await db.aircraft_shares.find_one({
        "aircraft_id": aircraft_id,
        "mechanic_user_id": user_id,
        "status": "active"
    })
    if share:
        aircraft = await db.aircrafts.find_one({"_id": aircraft_id})
        return aircraft
    
    return None


async def update_aircraft_hours(db: AsyncIOMotorDatabase, aircraft_id: str, hours_to_add: float):
    """
    Update aircraft hours (airframe, engine, propeller) after flight confirmation.
    TC-SAFE: This only updates recorded hours, not compliance status.
    """
    await db.aircrafts.update_one(
        {"_id": aircraft_id},
        {
            "$inc": {
                "airframe_hours": hours_to_add,
                "engine_hours": hours_to_add,
                "propeller_hours": hours_to_add
            },
            "$set": {"updated_at": datetime.utcnow()}
        }
    )
    logger.info(f"Updated aircraft {aircraft_id} hours: +{hours_to_add:.2f}h")


async def recalculate_maintenance_alerts(db: AsyncIOMotorDatabase, aircraft_id: str):
    """
    Recalculate 50/100 hour maintenance reminders.
    TC-SAFE: Internal reminders only, not compliance indicators.
    """
    aircraft = await db.aircrafts.find_one({"_id": aircraft_id})
    if not aircraft:
        return
    
    airframe_hours = aircraft.get("airframe_hours", 0)
    engine_hours = aircraft.get("engine_hours", 0)
    
    # Calculate hours until next 50/100 intervals
    hours_to_next_50 = 50 - (airframe_hours % 50)
    hours_to_next_100 = 100 - (airframe_hours % 100)
    
    # Update or create maintenance reminders
    await db.maintenance_reminders.update_one(
        {"aircraft_id": aircraft_id, "type": "50_hour"},
        {
            "$set": {
                "aircraft_id": aircraft_id,
                "type": "50_hour",
                "current_hours": airframe_hours,
                "hours_remaining": hours_to_next_50,
                "updated_at": datetime.utcnow()
            }
        },
        upsert=True
    )
    
    await db.maintenance_reminders.update_one(
        {"aircraft_id": aircraft_id, "type": "100_hour"},
        {
            "$set": {
                "aircraft_id": aircraft_id,
                "type": "100_hour",
                "current_hours": airframe_hours,
                "hours_remaining": hours_to_next_100,
                "updated_at": datetime.utcnow()
            }
        },
        upsert=True
    )
    
    logger.info(f"Recalculated maintenance alerts for aircraft {aircraft_id}: 50h in {hours_to_next_50:.1f}h, 100h in {hours_to_next_100:.1f}h")


# ==================== ENDPOINTS ====================

@router.post("/aircraft/{aircraft_id}/flight-candidates", response_model=FlightCandidateResponse)
async def create_flight_candidate(
    aircraft_id: str,
    flight: FlightCandidateCreate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    current_user: User = Depends(get_current_user)
):
    """
    Create a PROPOSED flight candidate.
    TC-SAFE: Estimated from sensors, requires user confirmation.
    """
    # Verify access
    aircraft = await verify_aircraft_access(db, aircraft_id, current_user.id)
    if not aircraft:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Aéronef non trouvé ou accès refusé"
        )
    
    flight_id = generate_id()
    now = datetime.utcnow()
    
    flight_doc = {
        "_id": flight_id,
        "aircraft_id": aircraft_id,
        "status": FlightStatus.PROPOSED.value,
        "departure_icao": flight.departure_icao,
        "arrival_icao": flight.arrival_icao,
        "depart_ts": flight.depart_ts,
        "arrival_ts": flight.arrival_ts,
        "duration_est_minutes": flight.duration_est_minutes,
        "source": flight.source,
        "created_by_user_id": current_user.id,
        "confirmed_by_user_id": None,
        "confirmed_at": None,
        "soft_deleted_at": None,
        "created_at": now,
        "updated_at": now
    }
    
    await db.flight_candidates.insert_one(flight_doc)
    logger.info(f"Flight candidate {flight_id} created for aircraft {aircraft_id}")
    
    return FlightCandidateResponse(
        id=flight_id,
        aircraft_id=aircraft_id,
        status=FlightStatus.PROPOSED,
        departure_icao=flight.departure_icao,
        arrival_icao=flight.arrival_icao,
        depart_ts=flight.depart_ts,
        arrival_ts=flight.arrival_ts,
        duration_est_minutes=flight.duration_est_minutes,
        source=flight.source,
        created_by_user_id=current_user.id,
        confirmed_by_user_id=None,
        created_at=now,
        confirmed_at=None
    )


@router.get("/aircraft/{aircraft_id}/flight-candidates", response_model=List[FlightCandidateResponse])
async def get_flight_candidates(
    aircraft_id: str,
    status_filter: Optional[FlightStatus] = Query(None, alias="status"),
    db: AsyncIOMotorDatabase = Depends(get_database),
    current_user: User = Depends(get_current_user)
):
    """
    Get flight candidates for an aircraft.
    """
    # Verify access
    aircraft = await verify_aircraft_access(db, aircraft_id, current_user.id)
    if not aircraft:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Aéronef non trouvé ou accès refusé"
        )
    
    query = {
        "aircraft_id": aircraft_id,
        "soft_deleted_at": None
    }
    
    if status_filter:
        query["status"] = status_filter.value
    
    flights = await db.flight_candidates.find(query).sort("depart_ts", -1).to_list(100)
    
    return [
        FlightCandidateResponse(
            id=f["_id"],
            aircraft_id=f["aircraft_id"],
            status=FlightStatus(f["status"]),
            departure_icao=f.get("departure_icao"),
            arrival_icao=f.get("arrival_icao"),
            depart_ts=f["depart_ts"],
            arrival_ts=f["arrival_ts"],
            duration_est_minutes=f["duration_est_minutes"],
            source=f["source"],
            pilot_label=f.get("pilot_label"),
            created_by_user_id=f.get("created_by_user_id"),
            confirmed_by_user_id=f.get("confirmed_by_user_id"),
            created_at=f["created_at"],
            confirmed_at=f.get("confirmed_at")
        )
        for f in flights
    ]


@router.post("/flight-candidates/{flight_id}/confirm")
async def confirm_flight_candidate(
    flight_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    current_user: User = Depends(get_current_user)
):
    """
    Confirm a PROPOSED flight. Applies hours to time ledger.
    TC-SAFE: User explicitly confirms estimated flight data.
    """
    flight = await db.flight_candidates.find_one({"_id": flight_id, "soft_deleted_at": None})
    if not flight:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Vol proposé non trouvé"
        )
    
    # Verify access
    aircraft = await verify_aircraft_access(db, flight["aircraft_id"], current_user.id)
    if not aircraft:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Accès refusé"
        )
    
    if flight["status"] != FlightStatus.PROPOSED.value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Ce vol a déjà été traité"
        )
    
    now = datetime.utcnow()
    
    # Update flight status
    await db.flight_candidates.update_one(
        {"_id": flight_id},
        {
            "$set": {
                "status": FlightStatus.CONFIRMED.value,
                "confirmed_by_user_id": current_user.id,
                "confirmed_at": now,
                "updated_at": now
            }
        }
    )
    
    # Apply hours to aircraft
    hours_to_add = flight["duration_est_minutes"] / 60.0
    await update_aircraft_hours(db, flight["aircraft_id"], hours_to_add)
    
    # Recalculate maintenance alerts
    await recalculate_maintenance_alerts(db, flight["aircraft_id"])
    
    logger.info(f"Flight {flight_id} confirmed by {current_user.email}, +{hours_to_add:.2f}h added")
    
    return {
        "message": "Vol confirmé",
        "hours_added": round(hours_to_add, 2),
        "status": "CONFIRMED"
    }


@router.post("/flight-candidates/{flight_id}/ignore")
async def ignore_flight_candidate(
    flight_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    current_user: User = Depends(get_current_user)
):
    """
    Ignore a PROPOSED flight. Does not affect hours.
    """
    flight = await db.flight_candidates.find_one({"_id": flight_id, "soft_deleted_at": None})
    if not flight:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Vol proposé non trouvé"
        )
    
    # Verify access
    aircraft = await verify_aircraft_access(db, flight["aircraft_id"], current_user.id)
    if not aircraft:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Accès refusé"
        )
    
    if flight["status"] != FlightStatus.PROPOSED.value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Ce vol a déjà été traité"
        )
    
    await db.flight_candidates.update_one(
        {"_id": flight_id},
        {
            "$set": {
                "status": FlightStatus.IGNORED.value,
                "updated_at": datetime.utcnow()
            }
        }
    )
    
    logger.info(f"Flight {flight_id} ignored by {current_user.email}")
    
    return {"message": "Vol ignoré", "status": "IGNORED"}


@router.post("/flight-candidates/{flight_id}/edit", response_model=FlightCandidateResponse)
async def edit_flight_candidate(
    flight_id: str,
    edits: FlightCandidateEdit,
    db: AsyncIOMotorDatabase = Depends(get_database),
    current_user: User = Depends(get_current_user)
):
    """
    Edit a PROPOSED flight before confirmation.
    """
    flight = await db.flight_candidates.find_one({"_id": flight_id, "soft_deleted_at": None})
    if not flight:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Vol proposé non trouvé"
        )
    
    # Verify access
    aircraft = await verify_aircraft_access(db, flight["aircraft_id"], current_user.id)
    if not aircraft:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Accès refusé"
        )
    
    if flight["status"] != FlightStatus.PROPOSED.value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Seuls les vols proposés peuvent être modifiés"
        )
    
    # Build update
    update_fields = {"updated_at": datetime.utcnow()}
    
    if edits.departure_icao is not None:
        update_fields["departure_icao"] = edits.departure_icao
    if edits.arrival_icao is not None:
        update_fields["arrival_icao"] = edits.arrival_icao
    if edits.depart_ts is not None:
        update_fields["depart_ts"] = edits.depart_ts
    if edits.arrival_ts is not None:
        update_fields["arrival_ts"] = edits.arrival_ts
    if edits.duration_est_minutes is not None:
        update_fields["duration_est_minutes"] = edits.duration_est_minutes
    
    await db.flight_candidates.update_one(
        {"_id": flight_id},
        {"$set": update_fields}
    )
    
    # Get updated document
    updated = await db.flight_candidates.find_one({"_id": flight_id})
    
    logger.info(f"Flight {flight_id} edited by {current_user.email}")
    
    return FlightCandidateResponse(
        id=updated["_id"],
        aircraft_id=updated["aircraft_id"],
        status=FlightStatus(updated["status"]),
        departure_icao=updated.get("departure_icao"),
        arrival_icao=updated.get("arrival_icao"),
        depart_ts=updated["depart_ts"],
        arrival_ts=updated["arrival_ts"],
        duration_est_minutes=updated["duration_est_minutes"],
        source=updated["source"],
        created_by_user_id=updated.get("created_by_user_id"),
        confirmed_by_user_id=updated.get("confirmed_by_user_id"),
        created_at=updated["created_at"],
        confirmed_at=updated.get("confirmed_at")
    )


@router.delete("/flight-candidates/{flight_id}")
async def soft_delete_flight_candidate(
    flight_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    current_user: User = Depends(get_current_user)
):
    """
    Soft delete a flight candidate.
    """
    flight = await db.flight_candidates.find_one({"_id": flight_id, "soft_deleted_at": None})
    if not flight:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Vol non trouvé"
        )
    
    # Verify access
    aircraft = await verify_aircraft_access(db, flight["aircraft_id"], current_user.id)
    if not aircraft:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Accès refusé"
        )
    
    await db.flight_candidates.update_one(
        {"_id": flight_id},
        {"$set": {"soft_deleted_at": datetime.utcnow()}}
    )
    
    logger.info(f"Flight {flight_id} soft deleted by {current_user.email}")
    
    return {"message": "Vol supprimé"}


@router.get("/aircraft/{aircraft_id}/maintenance-reminders")
async def get_maintenance_reminders(
    aircraft_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    current_user: User = Depends(get_current_user)
):
    """
    Get 50/100 hour maintenance reminders.
    TC-SAFE: Informational reminders only, not compliance status.
    """
    # Verify access
    aircraft = await verify_aircraft_access(db, aircraft_id, current_user.id)
    if not aircraft:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Aéronef non trouvé ou accès refusé"
        )
    
    reminders = await db.maintenance_reminders.find({
        "aircraft_id": aircraft_id
    }).to_list(10)
    
    # Ensure reminders exist
    if not reminders:
        await recalculate_maintenance_alerts(db, aircraft_id)
        reminders = await db.maintenance_reminders.find({
            "aircraft_id": aircraft_id
        }).to_list(10)
    
    return [
        {
            "type": r["type"],
            "current_hours": r.get("current_hours", 0),
            "hours_remaining": r.get("hours_remaining", 0),
            "updated_at": r.get("updated_at")
        }
        for r in reminders
    ]
