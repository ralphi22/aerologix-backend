"""
RevenueCat Webhook Handler for AeroLogix AI

Synchronizes RevenueCat entitlements with the existing plan_code system.

CONSTRAINTS:
- NO Apple Product IDs dependency
- NO StoreKit calls
- NO price storage or calculation
- NO aviation compliance decisions
- RevenueCat is EVENT-DRIVEN, not DECISION-MAKING

TC-SAFE: This module does NOT affect TC / AD / SB / OCR / ELT logic.
"""

from fastapi import APIRouter, Request, HTTPException, status
from motor.motor_asyncio import AsyncIOMotorDatabase
from datetime import datetime
from typing import Dict, Any, Optional, Tuple
import logging

from database.mongodb import get_database
from models.plans import PlanCode, compute_limits

router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])
logger = logging.getLogger(__name__)


# ============================================================
# ENTITLEMENT MAPPING - IMMUTABLE
# ============================================================

REVENUECAT_ENTITLEMENT_MAPPING: Dict[str, str] = {
    "pilot": "PILOT",
    "pilot_pro": "PILOT_PRO",
    "fleet": "FLEET",
}

# Priority order for conflict resolution (highest first)
ENTITLEMENT_PRIORITY = ["fleet", "pilot_pro", "pilot"]


# ============================================================
# HELPER FUNCTIONS
# ============================================================

def get_active_entitlement(entitlements: Dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
    """
    Determine the active entitlement from RevenueCat entitlements dict.
    
    Args:
        entitlements: Dict from subscriber.entitlements
        
    Returns:
        Tuple of (entitlement_id, expires_date) or (None, None) if none active
        
    Rules:
        - Only one entitlement expected
        - If multiple active: fleet > pilot_pro > pilot
        - If none active: return (None, None)
    """
    if not entitlements:
        return None, None
    
    active_entitlements = []
    
    for entitlement_id, entitlement_data in entitlements.items():
        if not isinstance(entitlement_data, dict):
            continue
            
        # Check if entitlement is active
        # RevenueCat marks active entitlements with expires_date in the future
        # or with is_active field
        is_active = entitlement_data.get("is_active", False)
        expires_date = entitlement_data.get("expires_date")
        
        # Also check expires_date if is_active not present
        if not is_active and expires_date:
            try:
                exp_dt = datetime.fromisoformat(expires_date.replace("Z", "+00:00"))
                is_active = exp_dt > datetime.now(exp_dt.tzinfo)
            except:
                pass
        
        if is_active:
            active_entitlements.append({
                "id": entitlement_id.lower(),
                "expires_date": expires_date,
                "data": entitlement_data
            })
    
    if not active_entitlements:
        return None, None
    
    # If multiple active, use priority
    if len(active_entitlements) > 1:
        logger.warning(f"Multiple active entitlements detected: {[e['id'] for e in active_entitlements]}")
        for priority_id in ENTITLEMENT_PRIORITY:
            for ent in active_entitlements:
                if ent["id"] == priority_id:
                    logger.info(f"Selected highest priority entitlement: {priority_id}")
                    return ent["id"], ent["expires_date"]
    
    # Single active entitlement
    active = active_entitlements[0]
    return active["id"], active["expires_date"]


def map_entitlement_to_plan_code(entitlement_id: Optional[str]) -> str:
    """
    Map RevenueCat entitlement to plan_code.
    
    Args:
        entitlement_id: RevenueCat entitlement identifier (lowercase)
        
    Returns:
        PlanCode string (BASIC, PILOT, PILOT_PRO, FLEET)
    """
    if not entitlement_id:
        return PlanCode.BASIC.value
    
    plan_code = REVENUECAT_ENTITLEMENT_MAPPING.get(entitlement_id.lower())
    
    if not plan_code:
        logger.warning(f"Unknown entitlement: {entitlement_id} - defaulting to BASIC")
        return PlanCode.BASIC.value
    
    return plan_code


def parse_expires_date(expires_date: Optional[str]) -> Optional[datetime]:
    """
    Parse RevenueCat expires_date (ISO-8601) to datetime.
    
    Args:
        expires_date: ISO-8601 date string from RevenueCat
        
    Returns:
        datetime object or None
    """
    if not expires_date:
        return None
    
    try:
        # Handle various ISO-8601 formats
        if expires_date.endswith("Z"):
            expires_date = expires_date.replace("Z", "+00:00")
        return datetime.fromisoformat(expires_date)
    except Exception as e:
        logger.error(f"Failed to parse expires_date '{expires_date}': {e}")
        return None


def determine_subscription_status(
    entitlement_id: Optional[str],
    expires_date: Optional[datetime],
    entitlement_data: Optional[Dict[str, Any]] = None
) -> str:
    """
    Determine subscription status from entitlement data.
    
    Returns: active, trialing, canceled, expired, or basic
    """
    if not entitlement_id:
        return "basic"
    
    # Check for trial
    if entitlement_data:
        # RevenueCat indicates trial via period_type or is_sandbox
        period_type = entitlement_data.get("period_type", "").lower()
        if period_type == "trial":
            return "trialing"
    
    # Check expiration
    if expires_date:
        now = datetime.now(expires_date.tzinfo) if expires_date.tzinfo else datetime.utcnow()
        if expires_date < now:
            return "expired"
    
    return "active"


# ============================================================
# WEBHOOK ENDPOINT
# ============================================================

@router.post("/revenuecat")
async def revenuecat_webhook(request: Request):
    """
    RevenueCat Webhook Endpoint
    
    Receives RevenueCat events and synchronizes entitlements with plan_code system.
    
    PUBLIC ENDPOINT - No user authentication required.
    
    Expected payload structure:
    {
        "event": { "type": "...", ... },
        "api_version": "1.0",
        "subscriber": {
            "original_app_user_id": "user_id",
            "entitlements": {
                "pilot_pro": {
                    "expires_date": "2026-02-15T00:00:00Z",
                    "is_active": true,
                    ...
                }
            },
            ...
        }
    }
    """
    # Get database
    from database.mongodb import db as database
    db_instance = database.db
    
    # Parse JSON payload
    try:
        payload = await request.json()
    except Exception as e:
        logger.error(f"RevenueCat webhook: Invalid JSON payload: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid JSON payload"
        )
    
    # Log event type
    event = payload.get("event", {})
    event_type = event.get("type", "unknown")
    logger.info(f"RevenueCat webhook received: event_type={event_type}")
    
    # Extract subscriber data
    subscriber = payload.get("subscriber")
    if not subscriber:
        logger.error("RevenueCat webhook: Missing 'subscriber' in payload")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing 'subscriber' in payload"
        )
    
    # Get user identifier
    user_id = subscriber.get("original_app_user_id")
    if not user_id:
        logger.error("RevenueCat webhook: Missing 'original_app_user_id'")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing 'original_app_user_id'"
        )
    
    # Get entitlements
    entitlements = subscriber.get("entitlements", {})
    
    # Determine active entitlement
    entitlement_id, expires_date_str = get_active_entitlement(entitlements)
    
    # Map to plan_code
    plan_code = map_entitlement_to_plan_code(entitlement_id)
    
    # Parse expiration date
    expires_date = parse_expires_date(expires_date_str)
    
    # Get entitlement data for status determination
    entitlement_data = entitlements.get(entitlement_id, {}) if entitlement_id else None
    
    # Determine status
    subscription_status = determine_subscription_status(
        entitlement_id, 
        expires_date,
        entitlement_data
    )
    
    logger.info(
        f"RevenueCat webhook processing: "
        f"user_id={user_id}, entitlement={entitlement_id}, "
        f"plan_code={plan_code}, status={subscription_status}, "
        f"expires={expires_date_str}"
    )
    
    # Find user in database
    user = await db_instance.users.find_one({"_id": user_id})
    
    if not user:
        # Try by email if user_id looks like an email
        if "@" in user_id:
            user = await db_instance.users.find_one({"email": user_id})
    
    if not user:
        logger.warning(f"RevenueCat webhook: User not found: {user_id}")
        # Do NOT create user automatically - just log and return success
        # RevenueCat may retry, or user may not exist yet
        return {
            "status": "ignored",
            "reason": "user_not_found",
            "user_id": user_id
        }
    
    actual_user_id = user["_id"]
    
    # Compute new limits from plan_code using EXISTING logic
    new_limits = compute_limits(plan_code)
    
    # Build update document
    now = datetime.utcnow()
    update_doc = {
        "subscription.plan_code": plan_code,
        "subscription.status": subscription_status,
        "subscription.current_period_end": expires_date,
        "subscription.updated_at": now,
        "subscription.source": "revenuecat",
        "limits": new_limits,
        "updated_at": now,
    }
    
    # Also update legacy 'plan' field for compatibility
    try:
        from models.user import PlanTier
        legacy_plan = PlanTier(plan_code)
        update_doc["subscription.plan"] = legacy_plan.value
    except:
        pass
    
    # Update user in database
    result = await db_instance.users.update_one(
        {"_id": actual_user_id},
        {"$set": update_doc}
    )
    
    if result.modified_count > 0:
        logger.info(
            f"RevenueCat webhook: Updated user {actual_user_id} - "
            f"plan_code={plan_code}, status={subscription_status}"
        )
    else:
        logger.info(
            f"RevenueCat webhook: No changes for user {actual_user_id} - "
            f"plan_code already {plan_code}"
        )
    
    return {
        "status": "success",
        "user_id": actual_user_id,
        "plan_code": plan_code,
        "subscription_status": subscription_status,
        "expires_date": expires_date_str
    }


@router.get("/revenuecat/health")
async def revenuecat_webhook_health():
    """
    Health check for RevenueCat webhook endpoint.
    Used to verify the endpoint is reachable.
    """
    return {
        "status": "healthy",
        "endpoint": "/api/webhooks/revenuecat",
        "supported_entitlements": list(REVENUECAT_ENTITLEMENT_MAPPING.keys()),
        "mapping": REVENUECAT_ENTITLEMENT_MAPPING
    }
