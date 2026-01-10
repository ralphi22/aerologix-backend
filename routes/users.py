"""
User Management Routes for AeroLogix AI
Includes account deletion for Apple Guideline 5.1.1(v) compliance
"""

from fastapi import APIRouter, Depends, HTTPException, status
from motor.motor_asyncio import AsyncIOMotorDatabase
from database.mongodb import get_database
from services.auth_deps import get_current_user
from models.user import User
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/users", tags=["users"])


@router.delete("/me", status_code=status.HTTP_200_OK)
async def delete_my_account(
    current_user: User = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(get_database)
):
    """
    Delete current user account and ALL associated data.
    
    Apple Guideline 5.1.1(v) Compliance:
    - Irréversible
    - Suppression immédiate
    - Aucun soft delete
    - Toutes les données utilisateur supprimées
    
    Collections supprimées:
    - users (compte utilisateur)
    - aircrafts (aéronefs)
    - ocr_scans (scans OCR)
    - maintenance_records (enregistrements maintenance)
    - part_records (pièces)
    - invoices (factures)
    - adsb_records (AD/SB)
    - stc_records (STC)
    - elt_records (ELT)
    - component_settings (paramètres composants)
    - eko_conversations (conversations EKO)
    - flight_candidates (candidats vol)
    - logbook_entries (entrées carnet de vol)
    - aircraft_shares (partages)
    - aircraft_pilots (pilotes)
    - pilot_invites (invitations pilotes)
    - subscriptions (abonnements)
    - maintenance_reminders (rappels maintenance)
    """
    
    user_id = current_user.id
    logger.info(f"ACCOUNT DELETION STARTED | user_id={user_id} | email={current_user.email}")
    
    deletion_results = {}
    
    try:
        # ============================================================
        # 1. Get all aircraft IDs owned by user (needed for cascade)
        # ============================================================
        aircraft_cursor = db.aircrafts.find({"user_id": user_id}, {"_id": 1})
        aircraft_ids = [doc["_id"] async for doc in aircraft_cursor]
        logger.info(f"Found {len(aircraft_ids)} aircrafts to delete for user {user_id}")
        
        # ============================================================
        # 2. Delete all data linked to user's aircrafts
        # ============================================================
        
        # OCR Scans
        result = await db.ocr_scans.delete_many({"user_id": user_id})
        deletion_results["ocr_scans"] = result.deleted_count
        
        # Maintenance Records
        result = await db.maintenance_records.delete_many({"user_id": user_id})
        deletion_results["maintenance_records"] = result.deleted_count
        
        # Part Records
        result = await db.part_records.delete_many({"user_id": user_id})
        deletion_results["part_records"] = result.deleted_count
        
        # Invoices
        result = await db.invoices.delete_many({"user_id": user_id})
        deletion_results["invoices"] = result.deleted_count
        
        # AD/SB Records
        result = await db.adsb_records.delete_many({"user_id": user_id})
        deletion_results["adsb_records"] = result.deleted_count
        
        # STC Records
        result = await db.stc_records.delete_many({"user_id": user_id})
        deletion_results["stc_records"] = result.deleted_count
        
        # ELT Records
        result = await db.elt_records.delete_many({"user_id": user_id})
        deletion_results["elt_records"] = result.deleted_count
        
        # Component Settings
        result = await db.component_settings.delete_many({"user_id": user_id})
        deletion_results["component_settings"] = result.deleted_count
        
        # EKO Conversations
        result = await db.eko_conversations.delete_many({"user_id": user_id})
        deletion_results["eko_conversations"] = result.deleted_count
        
        # Flight Candidates (by aircraft_id)
        if aircraft_ids:
            result = await db.flight_candidates.delete_many({"aircraft_id": {"$in": aircraft_ids}})
            deletion_results["flight_candidates"] = result.deleted_count
        else:
            deletion_results["flight_candidates"] = 0
        
        # Logbook Entries (by aircraft_id)
        if aircraft_ids:
            result = await db.logbook_entries.delete_many({"aircraft_id": {"$in": aircraft_ids}})
            deletion_results["logbook_entries"] = result.deleted_count
        else:
            deletion_results["logbook_entries"] = 0
        
        # Maintenance Reminders
        result = await db.maintenance_reminders.delete_many({"user_id": user_id})
        deletion_results["maintenance_reminders"] = result.deleted_count
        
        # ============================================================
        # 3. Delete sharing data
        # ============================================================
        
        # Aircraft Shares (where user is owner)
        result = await db.aircraft_shares.delete_many({"owner_user_id": user_id})
        deletion_results["aircraft_shares_owned"] = result.deleted_count
        
        # Aircraft Shares (where user is shared_with)
        result = await db.aircraft_shares.delete_many({"shared_with_user_id": user_id})
        deletion_results["aircraft_shares_received"] = result.deleted_count
        
        # Aircraft Pilots (where user is pilot)
        result = await db.aircraft_pilots.delete_many({"user_id": user_id})
        deletion_results["aircraft_pilots"] = result.deleted_count
        
        # Pilot Invites (sent by user)
        result = await db.pilot_invites.delete_many({"invited_by_user_id": user_id})
        deletion_results["pilot_invites_sent"] = result.deleted_count
        
        # Pilot Invites (received by user email)
        result = await db.pilot_invites.delete_many({"email": current_user.email})
        deletion_results["pilot_invites_received"] = result.deleted_count
        
        # ============================================================
        # 4. Delete subscription data
        # ============================================================
        result = await db.subscriptions.delete_many({"user_id": user_id})
        deletion_results["subscriptions"] = result.deleted_count
        
        # ============================================================
        # 5. Delete all aircrafts
        # ============================================================
        result = await db.aircrafts.delete_many({"user_id": user_id})
        deletion_results["aircrafts"] = result.deleted_count
        
        # ============================================================
        # 6. Delete user account (LAST)
        # ============================================================
        result = await db.users.delete_one({"_id": user_id})
        deletion_results["users"] = result.deleted_count
        
        if result.deleted_count == 0:
            logger.error(f"ACCOUNT DELETION FAILED | user_id={user_id} | user not found in final step")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Account deletion failed"
            )
        
        # ============================================================
        # 7. Log final summary
        # ============================================================
        total_deleted = sum(deletion_results.values())
        logger.info(
            f"ACCOUNT DELETION COMPLETED | user_id={user_id} | "
            f"email={current_user.email} | total_records={total_deleted} | "
            f"details={deletion_results}"
        )
        
        return {
            "status": "deleted",
            "message": "Account and all associated data have been permanently deleted",
            "deleted_at": datetime.utcnow().isoformat(),
            "summary": deletion_results
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"ACCOUNT DELETION ERROR | user_id={user_id} | error={str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Account deletion failed: {str(e)}"
        )
