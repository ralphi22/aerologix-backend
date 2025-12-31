"""
Shares Routes - Owner ↔ TEA/AMO sharing
TC-SAFE: Information only - owner and certified maintenance personnel remain responsible
"""

from fastapi import APIRouter, Depends, HTTPException, status
from motor.motor_asyncio import AsyncIOMotorDatabase
from datetime import datetime
import logging

from database.mongodb import get_database
from services.auth_deps import get_current_user
from models.user import User
from models.share import (
    ShareInviteRequest,
    ShareAcceptRequest,
    ShareRevokeRequest,
    AircraftShare,
    SharedAircraftInfo,
    ShareStatus,
    ShareRole
)

router = APIRouter(prefix="/api/shares", tags=["shares"])
logger = logging.getLogger(__name__)


def generate_id():
    """Generate unique ID"""
    import time
    return str(int(time.time() * 1000000))


@router.post("/invite")
async def invite_mechanic(
    request: ShareInviteRequest,
    db: AsyncIOMotorDatabase = Depends(get_database),
    current_user: User = Depends(get_current_user)
):
    """
    Owner invites a TEA/AMO to access their aircraft.
    The mechanic must accept to gain access.
    """
    # Verify owner owns the aircraft
    aircraft = await db.aircrafts.find_one({
        "_id": request.aircraft_id,
        "user_id": current_user.id
    })
    if not aircraft:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Aircraft not found or not owned by you"
        )
    
    # Check if mechanic user exists (optional - invitation works even if not registered)
    mechanic_user = await db.users.find_one({"email": request.mechanic_email})
    mechanic_user_id = mechanic_user["_id"] if mechanic_user else None
    
    # Check for existing active share
    existing = await db.aircraft_shares.find_one({
        "aircraft_id": request.aircraft_id,
        "mechanic_email": request.mechanic_email,
        "status": {"$in": ["pending", "active"]}
    })
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="An invitation already exists for this mechanic"
        )
    
    share_id = generate_id()
    now = datetime.utcnow()
    
    share_doc = {
        "_id": share_id,
        "aircraft_id": request.aircraft_id,
        "mechanic_email": request.mechanic_email,
        "mechanic_user_id": mechanic_user_id,
        "owner_user_id": current_user.id,
        "role": request.role.value,
        "status": ShareStatus.PENDING.value,
        "created_at": now,
        "updated_at": now,
        "accepted_at": None,
        "revoked_at": None,
        "created_by_user_id": current_user.id,
        "revoked_by_user_id": None
    }
    
    await db.aircraft_shares.insert_one(share_doc)
    
    logger.info(f"Share invitation created: {share_id} - Aircraft {request.aircraft_id} shared with {request.mechanic_email}")
    
    return {
        "message": "Invitation sent",
        "share_id": share_id,
        "status": "pending"
    }


@router.post("/accept")
async def accept_share(
    request: ShareAcceptRequest,
    db: AsyncIOMotorDatabase = Depends(get_database),
    current_user: User = Depends(get_current_user)
):
    """
    Mechanic accepts a share invitation.
    """
    share = await db.aircraft_shares.find_one({
        "_id": request.share_id,
        "mechanic_email": current_user.email,
        "status": ShareStatus.PENDING.value
    })
    
    if not share:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Invitation not found or already processed"
        )
    
    now = datetime.utcnow()
    
    await db.aircraft_shares.update_one(
        {"_id": request.share_id},
        {"$set": {
            "status": ShareStatus.ACTIVE.value,
            "mechanic_user_id": current_user.id,
            "accepted_at": now,
            "updated_at": now
        }}
    )
    
    logger.info(f"Share accepted: {request.share_id} by {current_user.email}")
    
    return {"message": "Access granted", "status": "active"}


@router.post("/revoke")
async def revoke_share(
    request: ShareRevokeRequest,
    db: AsyncIOMotorDatabase = Depends(get_database),
    current_user: User = Depends(get_current_user)
):
    """
    Owner revokes access to their aircraft.
    """
    share = await db.aircraft_shares.find_one({
        "_id": request.share_id,
        "owner_user_id": current_user.id,
        "status": {"$in": [ShareStatus.PENDING.value, ShareStatus.ACTIVE.value]}
    })
    
    if not share:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Share not found or already revoked"
        )
    
    now = datetime.utcnow()
    
    await db.aircraft_shares.update_one(
        {"_id": request.share_id},
        {"$set": {
            "status": ShareStatus.REVOKED.value,
            "revoked_at": now,
            "revoked_by_user_id": current_user.id,
            "updated_at": now
        }}
    )
    
    logger.info(f"Share revoked: {request.share_id} by owner {current_user.email}")
    
    return {"message": "Access revoked", "status": "revoked"}


@router.get("/my-aircraft")
async def get_shared_aircraft(
    db: AsyncIOMotorDatabase = Depends(get_database),
    current_user: User = Depends(get_current_user)
):
    """
    Get list of aircraft shared with the current user (mechanic view).
    Returns only active shares.
    TC-SAFE: Maintenance records overview only.
    """
    shares = await db.aircraft_shares.find({
        "mechanic_user_id": current_user.id,
        "status": ShareStatus.ACTIVE.value
    }).to_list(100)
    
    result = []
    for share in shares:
        # Get aircraft info
        aircraft = await db.aircrafts.find_one({"_id": share["aircraft_id"]})
        if not aircraft:
            continue
        
        # Get owner info
        owner = await db.users.find_one({"_id": share["owner_user_id"]})
        if not owner:
            continue
        
        result.append({
            "aircraft_id": share["aircraft_id"],
            "registration": aircraft.get("registration", ""),
            "manufacturer": aircraft.get("manufacturer"),
            "model": aircraft.get("model"),
            "owner_name": owner.get("name", ""),
            "owner_email": owner.get("email", ""),
            "role": share["role"],
            "share_id": share["_id"],
            "shared_since": share.get("accepted_at") or share.get("created_at")
        })
    
    return result


@router.get("/pending")
async def get_pending_invitations(
    db: AsyncIOMotorDatabase = Depends(get_database),
    current_user: User = Depends(get_current_user)
):
    """
    Get pending invitations for the current user (mechanic).
    """
    shares = await db.aircraft_shares.find({
        "mechanic_email": current_user.email,
        "status": ShareStatus.PENDING.value
    }).to_list(100)
    
    result = []
    for share in shares:
        aircraft = await db.aircrafts.find_one({"_id": share["aircraft_id"]})
        owner = await db.users.find_one({"_id": share["owner_user_id"]})
        
        if aircraft and owner:
            result.append({
                "share_id": share["_id"],
                "aircraft_registration": aircraft.get("registration", ""),
                "aircraft_model": f"{aircraft.get('manufacturer', '')} {aircraft.get('model', '')}".strip(),
                "owner_name": owner.get("name", ""),
                "owner_email": owner.get("email", ""),
                "role": share["role"],
                "invited_at": share.get("created_at")
            })
    
    return result


@router.get("/aircraft/{aircraft_id}")
async def get_aircraft_shares(
    aircraft_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    current_user: User = Depends(get_current_user)
):
    """
    Get all shares for an aircraft (owner view).
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
    
    shares = await db.aircraft_shares.find({
        "aircraft_id": aircraft_id,
        "status": {"$in": [ShareStatus.PENDING.value, ShareStatus.ACTIVE.value]}
    }).to_list(100)
    
    result = []
    for share in shares:
        result.append({
            "share_id": share["_id"],
            "mechanic_email": share["mechanic_email"],
            "role": share["role"],
            "status": share["status"],
            "created_at": share.get("created_at"),
            "accepted_at": share.get("accepted_at")
        })
    
    return result


async def check_aircraft_access(
    db: AsyncIOMotorDatabase,
    aircraft_id: str,
    user_id: str,
    required_role: str = None
) -> dict:
    """
    Helper function to check if user has access to aircraft.
    Returns access info or None.
    """
    # Check if owner
    aircraft = await db.aircrafts.find_one({"_id": aircraft_id})
    if not aircraft:
        return None
    
    if aircraft.get("user_id") == user_id:
        return {"is_owner": True, "role": "owner", "aircraft": aircraft}
    
    # Check for active share
    share = await db.aircraft_shares.find_one({
        "aircraft_id": aircraft_id,
        "mechanic_user_id": user_id,
        "status": ShareStatus.ACTIVE.value
    })
    
    if share:
        role = share.get("role", ShareRole.VIEWER.value)
        if required_role and required_role == ShareRole.CONTRIBUTOR.value and role != ShareRole.CONTRIBUTOR.value:
            return None
        return {"is_owner": False, "role": role, "aircraft": aircraft, "share": share}
    
    return None
