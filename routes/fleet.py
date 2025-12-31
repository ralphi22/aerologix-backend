"""
Fleet Routes - Fleet management for Fleet AI plan subscribers
TC-SAFE: Information only - overview of maintenance records
"""

from fastapi import APIRouter, Depends, HTTPException, status
from motor.motor_asyncio import AsyncIOMotorDatabase
from datetime import datetime
from typing import List
import logging

from database.mongodb import get_database
from services.auth_deps import get_current_user
from models.user import User
from models.subscription import PlanType, PLAN_LIMITS, SubscriptionStatus

router = APIRouter(prefix="/api/fleet", tags=["fleet"])
logger = logging.getLogger(__name__)


async def check_fleet_access(
    db: AsyncIOMotorDatabase,
    user: User
) -> bool:
    """
    Check if user has Fleet access.
    Returns True if:
    - User has an active Fleet AI subscription
    - User has aircraft shared with them (mechanic)
    """
    # Check for active Fleet subscription
    subscription = await db.subscriptions.find_one({
        "user_id": user.id,
        "plan_id": PlanType.FLEET.value,
        "status": {"$in": [SubscriptionStatus.ACTIVE.value, SubscriptionStatus.TRIALING.value]}
    })
    
    if subscription:
        return True
    
    # Check for shared aircraft (mechanic role)
    shared_count = await db.aircraft_shares.count_documents({
        "mechanic_user_id": user.id,
        "status": "active"
    })
    
    return shared_count > 0


@router.get("")
async def get_fleet_overview(
    db: AsyncIOMotorDatabase = Depends(get_database),
    current_user: User = Depends(get_current_user)
):
    """
    Get Fleet overview - all aircraft accessible to the user.
    Requires Fleet AI plan OR having aircraft shared with them.
    TC-SAFE: Maintenance records overview only.
    """
    # Check access
    has_access = await check_fleet_access(db, current_user)
    
    if not has_access:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Accès Fleet requis. Passez au plan Fleet AI ou recevez un partage d'un propriétaire."
        )
    
    fleet_data = []
    
    # 1. User's own aircraft
    own_aircraft = await db.aircrafts.find({
        "user_id": current_user.id
    }).to_list(100)
    
    for aircraft in own_aircraft:
        fleet_data.append({
            "aircraft_id": aircraft["_id"],
            "registration": aircraft.get("registration", ""),
            "manufacturer": aircraft.get("manufacturer"),
            "model": aircraft.get("model"),
            "airframe_hours": aircraft.get("airframe_hours", 0),
            "owner_type": "self",
            "owner_name": current_user.name,
            "role": "owner",
            "last_updated": aircraft.get("updated_at")
        })
    
    # 2. Shared aircraft (if mechanic)
    shared = await db.aircraft_shares.find({
        "mechanic_user_id": current_user.id,
        "status": "active"
    }).to_list(100)
    
    for share in shared:
        aircraft = await db.aircrafts.find_one({"_id": share["aircraft_id"]})
        if not aircraft:
            continue
        
        owner = await db.users.find_one({"_id": share["owner_user_id"]})
        
        fleet_data.append({
            "aircraft_id": aircraft["_id"],
            "registration": aircraft.get("registration", ""),
            "manufacturer": aircraft.get("manufacturer"),
            "model": aircraft.get("model"),
            "airframe_hours": aircraft.get("airframe_hours", 0),
            "owner_type": "shared",
            "owner_name": owner.get("name", "") if owner else "Unknown",
            "role": share["role"],
            "share_id": share["_id"],
            "last_updated": aircraft.get("updated_at")
        })
    
    logger.info(f"Fleet overview requested by {current_user.email}: {len(fleet_data)} aircraft")
    
    return {
        "total_aircraft": len(fleet_data),
        "own_aircraft": len([a for a in fleet_data if a["owner_type"] == "self"]),
        "shared_aircraft": len([a for a in fleet_data if a["owner_type"] == "shared"]),
        "aircraft": fleet_data
    }


@router.get("/stats")
async def get_fleet_stats(
    db: AsyncIOMotorDatabase = Depends(get_database),
    current_user: User = Depends(get_current_user)
):
    """
    Get Fleet statistics - maintenance status overview.
    TC-SAFE: Information only.
    """
    # Check access
    has_access = await check_fleet_access(db, current_user)
    
    if not has_access:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Accès Fleet requis"
        )
    
    # Get all accessible aircraft IDs
    aircraft_ids = []
    
    # Own aircraft
    own = await db.aircrafts.find({"user_id": current_user.id}).to_list(100)
    aircraft_ids.extend([a["_id"] for a in own])
    
    # Shared aircraft
    shared = await db.aircraft_shares.find({
        "mechanic_user_id": current_user.id,
        "status": "active"
    }).to_list(100)
    aircraft_ids.extend([s["aircraft_id"] for s in shared])
    
    # Calculate stats
    total_airframe_hours = 0
    total_parts = 0
    total_adsb = 0
    
    for aid in aircraft_ids:
        aircraft = await db.aircrafts.find_one({"_id": aid})
        if aircraft:
            total_airframe_hours += aircraft.get("airframe_hours", 0)
        
        parts_count = await db.parts.count_documents({"aircraft_id": aid})
        total_parts += parts_count
        
        adsb_count = await db.adsb.count_documents({"aircraft_id": aid})
        total_adsb += adsb_count
    
    return {
        "total_aircraft": len(aircraft_ids),
        "total_airframe_hours": total_airframe_hours,
        "total_parts_tracked": total_parts,
        "total_adsb_entries": total_adsb
    }
