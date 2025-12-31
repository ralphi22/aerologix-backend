"""
Pilot Invites Routes - Aircraft sharing with pilots (non-owners)
TC-SAFE: Information contribution only - owner remains solely responsible

GARDE-FOU #1: Token lié à UN avion + UN rôle
GARDE-FOU #2: Vue strictement lecture seule
GARDE-FOU #3: Micro-compteur local UI seulement
GARDE-FOU #4: Validation stricte des flight-candidates
"""

from fastapi import APIRouter, Depends, HTTPException, status
from motor.motor_asyncio import AsyncIOMotorDatabase
from datetime import datetime, timedelta
from pydantic import BaseModel, Field
from typing import Optional
import secrets
import logging

from database.mongodb import get_database
from services.auth_deps import get_current_user
from models.user import User

router = APIRouter(prefix="/api/pilot-invites", tags=["pilot-invites"])
logger = logging.getLogger(__name__)


# ============ MODELS ============

class CreatePilotInviteRequest(BaseModel):
    """Request to create a pilot invite - GARDE-FOU #1"""
    pilot_label: str = Field(..., min_length=1, max_length=50, description="Label for the pilot (e.g., 'Alex')")
    expires_days: int = Field(default=30, ge=1, le=365, description="Token validity in days")


class PilotInviteResponse(BaseModel):
    """Response with invite details"""
    invite_id: str
    token: str
    invite_url: str
    pilot_label: str
    aircraft_id: str
    aircraft_registration: str
    expires_at: datetime
    created_at: datetime


class PilotFlightSubmission(BaseModel):
    """Flight data submitted by pilot - GARDE-FOU #4"""
    duration_est_minutes: int = Field(..., ge=1, description="Estimated flight duration in minutes")


# ============ HELPER FUNCTIONS ============

def generate_secure_token() -> str:
    """Generate a secure random token"""
    return secrets.token_urlsafe(32)


def generate_id() -> str:
    """Generate unique ID"""
    import time
    return str(int(time.time() * 1000000))


# ============ OWNER ENDPOINTS ============

@router.post("/aircraft/{aircraft_id}/create")
async def create_pilot_invite(
    aircraft_id: str,
    request: CreatePilotInviteRequest,
    db: AsyncIOMotorDatabase = Depends(get_database),
    current_user: User = Depends(get_current_user)
):
    """
    Owner creates a secure invite link for a pilot.
    GARDE-FOU #1: Token tied to ONE aircraft + ONE role + pilot_label
    """
    # Verify owner owns the aircraft
    aircraft = await db.aircrafts.find_one({
        "_id": aircraft_id,
        "user_id": current_user.id
    })
    if not aircraft:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Aircraft not found or not owned by you"
        )
    
    invite_id = generate_id()
    token = generate_secure_token()
    now = datetime.utcnow()
    expires_at = now + timedelta(days=request.expires_days)
    
    invite_doc = {
        "_id": invite_id,
        "token": token,
        "aircraft_id": aircraft_id,
        "owner_user_id": current_user.id,
        "pilot_label": request.pilot_label,
        "role": "PILOT",  # Fixed role - data contributor only
        "status": "active",  # active | revoked
        "created_at": now,
        "expires_at": expires_at,
        "revoked_at": None,
        "last_used_at": None,
        "use_count": 0
    }
    
    await db.pilot_invites.insert_one(invite_doc)
    
    logger.info(f"Pilot invite created: {invite_id} for aircraft {aircraft_id}, pilot: {request.pilot_label}")
    
    # Build invite URL (frontend will handle the deep link)
    invite_url = f"/invite/aircraft/{token}"
    
    return PilotInviteResponse(
        invite_id=invite_id,
        token=token,
        invite_url=invite_url,
        pilot_label=request.pilot_label,
        aircraft_id=aircraft_id,
        aircraft_registration=aircraft.get("registration", ""),
        expires_at=expires_at,
        created_at=now
    )


@router.get("/aircraft/{aircraft_id}/list")
async def list_pilot_invites(
    aircraft_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    current_user: User = Depends(get_current_user)
):
    """
    Owner lists all pilot invites for an aircraft.
    """
    # Verify owner
    aircraft = await db.aircrafts.find_one({
        "_id": aircraft_id,
        "user_id": current_user.id
    })
    if not aircraft:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Aircraft not found or not owned by you"
        )
    
    invites = await db.pilot_invites.find({
        "aircraft_id": aircraft_id,
        "status": "active"
    }).to_list(100)
    
    result = []
    for invite in invites:
        is_expired = datetime.utcnow() > invite["expires_at"]
        result.append({
            "invite_id": invite["_id"],
            "pilot_label": invite["pilot_label"],
            "created_at": invite["created_at"],
            "expires_at": invite["expires_at"],
            "is_expired": is_expired,
            "use_count": invite.get("use_count", 0),
            "last_used_at": invite.get("last_used_at")
        })
    
    return result


@router.post("/revoke/{invite_id}")
async def revoke_pilot_invite(
    invite_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    current_user: User = Depends(get_current_user)
):
    """
    Owner revokes a pilot invite.
    """
    invite = await db.pilot_invites.find_one({
        "_id": invite_id,
        "owner_user_id": current_user.id,
        "status": "active"
    })
    
    if not invite:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Invite not found or already revoked"
        )
    
    await db.pilot_invites.update_one(
        {"_id": invite_id},
        {"$set": {
            "status": "revoked",
            "revoked_at": datetime.utcnow()
        }}
    )
    
    logger.info(f"Pilot invite revoked: {invite_id}")
    
    return {"message": "Invite revoked", "status": "revoked"}


# ============ PILOT ENDPOINTS (PUBLIC - Token-based auth) ============

@router.get("/view/{token}")
async def get_aircraft_by_token(
    token: str,
    db: AsyncIOMotorDatabase = Depends(get_database)
):
    """
    Pilot accesses aircraft via secure token.
    GARDE-FOU #2: Strictly read-only view - no other routes accessible
    Returns ONLY the data needed for the card display.
    """
    # Find and validate invite
    invite = await db.pilot_invites.find_one({
        "token": token,
        "status": "active"
    })
    
    if not invite:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Accès expiré ou révoqué"
        )
    
    # Check expiration
    if datetime.utcnow() > invite["expires_at"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Accès expiré"
        )
    
    # Get aircraft data
    aircraft = await db.aircrafts.find_one({"_id": invite["aircraft_id"]})
    if not aircraft:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Aéronef non trouvé"
        )
    
    # Update usage stats
    await db.pilot_invites.update_one(
        {"_id": invite["_id"]},
        {"$set": {"last_used_at": datetime.utcnow()}, "$inc": {"use_count": 1}}
    )
    
    # Return ONLY necessary data for card display
    # GARDE-FOU #2: No maintenance, no logs, no alerts
    return {
        "aircraft_id": aircraft["_id"],
        "registration": aircraft.get("registration", ""),
        "manufacturer": aircraft.get("manufacturer", ""),
        "model": aircraft.get("model", ""),
        "aircraft_type": aircraft.get("aircraft_type", ""),
        "photo_url": aircraft.get("photo_url"),
        "airframe_hours": aircraft.get("airframe_hours", 0),
        "engine_hours": aircraft.get("engine_hours", 0),
        "propeller_hours": aircraft.get("propeller_hours", 0),
        "pilot_label": invite["pilot_label"],
        "invite_id": invite["_id"]
    }


@router.post("/submit-flight/{token}")
async def submit_pilot_flight(
    token: str,
    submission: PilotFlightSubmission,
    db: AsyncIOMotorDatabase = Depends(get_database)
):
    """
    Pilot submits flight data via token.
    GARDE-FOU #3: Receives duration ONCE at stop - no continuous counting
    GARDE-FOU #4: Strict validation - all fields required
    
    Creates a FlightCandidate with source=pilot_share
    """
    # Validate token
    invite = await db.pilot_invites.find_one({
        "token": token,
        "status": "active"
    })
    
    if not invite:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Accès expiré ou révoqué"
        )
    
    if datetime.utcnow() > invite["expires_at"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Accès expiré"
        )
    
    # Verify aircraft still exists
    aircraft = await db.aircrafts.find_one({"_id": invite["aircraft_id"]})
    if not aircraft:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Aéronef non trouvé"
        )
    
    # GARDE-FOU #4: All required fields present
    if not submission.duration_est_minutes or submission.duration_est_minutes < 1:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="duration_est_minutes is required and must be >= 1"
        )
    
    # Create FlightCandidate
    flight_id = generate_id()
    now = datetime.utcnow()
    
    # Calculate timestamps based on duration
    arrival_ts = now
    depart_ts = now - timedelta(minutes=submission.duration_est_minutes)
    
    flight_doc = {
        "_id": flight_id,
        "aircraft_id": invite["aircraft_id"],
        "status": "PROPOSED",
        "source": "pilot_share",  # Identifies pilot-contributed flights
        "pilot_label": invite["pilot_label"],  # GARDE-FOU #5: Shows in owner's view
        "invite_id": invite["_id"],
        "depart_ts": depart_ts,
        "arrival_ts": arrival_ts,
        "duration_est_minutes": submission.duration_est_minutes,
        "created_at": now,
        "updated_at": now,
        "confirmed_at": None,
        "confirmed_by": None
    }
    
    await db.flight_candidates.insert_one(flight_doc)
    
    logger.info(f"Pilot flight submitted: {flight_id} for aircraft {invite['aircraft_id']}, pilot: {invite['pilot_label']}, duration: {submission.duration_est_minutes}min")
    
    return {
        "message": "Vol enregistré",
        "flight_id": flight_id,
        "duration_minutes": submission.duration_est_minutes,
        "status": "PROPOSED"
    }
