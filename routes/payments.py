"""
Payments Routes - Stripe subscription management (UNIFIED PLAN_CODE)

This module uses the unified plan_code system:
- BASIC: Free tier
- PILOT: $24/mo
- PILOT_PRO: $39/mo
- FLEET: $65/mo
"""

from fastapi import APIRouter, Depends, HTTPException, status, Request, Header
from pydantic import BaseModel
from motor.motor_asyncio import AsyncIOMotorDatabase
from datetime import datetime
from typing import Optional
import logging

from database.mongodb import get_database
from services.auth_deps import get_current_user
from models.user import User
from models.plans import (
    PlanCode, BillingCycle, PlanLimits, PlanDefinition,
    PLAN_DEFINITIONS, get_plan_definition, get_plan_limits,
    compute_limits, get_basic_limits, normalize_plan_code
)
from services import stripe_service
from config import get_settings

router = APIRouter(prefix="/api/payments", tags=["payments"])
logger = logging.getLogger(__name__)
settings = get_settings()


# ============================================================
# REQUEST/RESPONSE MODELS
# ============================================================

class CheckoutSessionRequest(BaseModel):
    """Request for creating checkout session"""
    plan_code: PlanCode  # NEW: Uses PlanCode enum
    billing_cycle: BillingCycle


class CheckoutSessionResponse(BaseModel):
    """Response with checkout URL"""
    checkout_url: str
    session_id: str


class PortalSessionResponse(BaseModel):
    """Response with customer portal URL"""
    portal_url: str


class SubscriptionStatus(str):
    ACTIVE = "active"
    CANCELED = "canceled"
    PAST_DUE = "past_due"
    TRIALING = "trialing"
    INCOMPLETE = "incomplete"


class SubscriptionInfo(BaseModel):
    """Subscription info for API response"""
    id: str
    user_id: str
    plan_code: PlanCode
    billing_cycle: BillingCycle
    status: str
    stripe_customer_id: Optional[str] = None
    stripe_subscription_id: Optional[str] = None
    current_period_start: Optional[datetime] = None
    current_period_end: Optional[datetime] = None
    cancel_at_period_end: bool = False
    created_at: datetime
    updated_at: datetime


class SubscriptionResponse(BaseModel):
    """Response for subscription endpoint"""
    has_subscription: bool
    subscription: Optional[SubscriptionInfo] = None
    plan_limits: dict
    plan_definition: Optional[dict] = None


# ============================================================
# HELPERS
# ============================================================

def generate_id():
    """Generate unique ID"""
    import time
    return str(int(time.time() * 1000000))


async def update_user_plan_and_limits(db, user_id: str, plan_code: str):
    """
    Update user's plan_code and limits in MongoDB.
    This is the SINGLE function to use for all plan changes.
    """
    limits = compute_limits(plan_code)
    now = datetime.utcnow()
    
    await db.users.update_one(
        {"_id": user_id},
        {"$set": {
            "subscription.plan_code": plan_code,
            "subscription.status": "active",
            "limits.max_aircrafts": limits["max_aircrafts"],
            "limits.ocr_per_month": limits["ocr_per_month"],
            "limits.gps_logbook": limits["gps_logbook"],
            "limits.tea_amo_sharing": limits["tea_amo_sharing"],
            "limits.invoices": limits["invoices"],
            "limits.cost_per_hour": limits["cost_per_hour"],
            "limits.prebuy": limits.get("prebuy", False),
            "updated_at": now
        }}
    )
    
    logger.info(f"Updated user {user_id} to plan_code={plan_code} with limits={limits}")


async def reset_user_to_basic(db, user_id: str):
    """Reset user to BASIC plan with BASIC limits"""
    await update_user_plan_and_limits(db, user_id, PlanCode.BASIC.value)
    logger.info(f"Reset user {user_id} to BASIC plan")


# ============================================================
# ENDPOINTS
# ============================================================

@router.post("/create-checkout-session", response_model=CheckoutSessionResponse)
async def create_checkout_session(
    request: CheckoutSessionRequest,
    db: AsyncIOMotorDatabase = Depends(get_database),
    current_user: User = Depends(get_current_user)
):
    """
    Create a Stripe Checkout session for subscription.
    
    Uses plan_code (PILOT, PILOT_PRO, FLEET) - not legacy solo/pro/fleet.
    """
    # Validate plan_code is not BASIC (free plan can't be purchased)
    if request.plan_code == PlanCode.BASIC:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Le plan BASIC est gratuit et ne nécessite pas d'abonnement."
        )
    
    # Check if user already has an active subscription
    existing = await db.subscriptions.find_one({
        "user_id": current_user.id,
        "status": {"$in": ["active", "trialing"]}
    })
    
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Vous avez déjà un abonnement actif. Gérez-le depuis le portail client."
        )
    
    # Get price ID from config
    price_id = settings.get_stripe_price_id(
        request.plan_code.value, 
        request.billing_cycle.value
    )
    
    if not price_id:
        logger.error(f"No Stripe price ID configured for {request.plan_code}/{request.billing_cycle}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Prix Stripe non configuré pour ce plan. Contactez le support."
        )
    
    # Get or create Stripe customer
    stripe_customer_id = await stripe_service.get_or_create_customer(
        user_id=current_user.id,
        email=current_user.email,
        name=current_user.name
    )
    
    # Update user with Stripe customer ID if not set
    user_doc = await db.users.find_one({"_id": current_user.id})
    if not user_doc.get("stripe_customer_id"):
        await db.users.update_one(
            {"_id": current_user.id},
            {"$set": {"stripe_customer_id": stripe_customer_id}}
        )
    
    # Create checkout session with plan_code in metadata
    success_url = f"{settings.frontend_url}/subscription?success=true&session_id={{CHECKOUT_SESSION_ID}}"
    cancel_url = f"{settings.frontend_url}/subscription?canceled=true"
    
    result = await stripe_service.create_checkout_session(
        customer_id=stripe_customer_id,
        price_id=price_id,
        success_url=success_url,
        cancel_url=cancel_url,
        user_id=current_user.id,
        plan_id=request.plan_code.value,  # NEW: Uses plan_code, passed as plan_id for Stripe metadata
        billing_cycle=request.billing_cycle.value
    )
    
    logger.info(f"Checkout session created for user {current_user.email}, plan_code={request.plan_code.value}")
    
    return CheckoutSessionResponse(
        checkout_url=result["checkout_url"],
        session_id=result["session_id"]
    )


@router.post("/webhook")
async def stripe_webhook(
    request: Request,
    stripe_signature: str = Header(None, alias="Stripe-Signature"),
    db: AsyncIOMotorDatabase = Depends(get_database)
):
    """
    Handle Stripe webhook events.
    
    CRITICAL: Always return 200 OK to Stripe - never let exceptions propagate.
    
    Events handled:
    - checkout.session.completed: New subscription
    - customer.subscription.updated: Plan change, renewal
    - customer.subscription.deleted: Cancellation
    - invoice.payment_failed: Failed payment -> reset to BASIC
    """
    try:
        if not stripe_signature:
            logger.warning("Webhook received without Stripe signature")
            return {"received": True}
        
        payload = await request.body()
        
        try:
            event = stripe_service.verify_webhook_signature(payload, stripe_signature)
        except Exception as e:
            logger.error(f"Webhook signature verification failed: {e}")
            return {"received": True}
        
        event_type = event["type"]
        data = event["data"]["object"]
        
        logger.info(f"Processing webhook event: {event_type}")
        
        try:
            if event_type == "checkout.session.completed":
                await handle_checkout_completed(db, data)
            
            elif event_type == "customer.subscription.updated":
                await handle_subscription_updated(db, data)
            
            elif event_type == "customer.subscription.deleted":
                await handle_subscription_deleted(db, data)
            
            elif event_type == "invoice.payment_failed":
                await handle_payment_failed(db, data)
                
        except Exception as e:
            logger.error(f"Webhook handler error for {event_type}: {e}")
    
    except Exception as e:
        logger.error(f"Webhook global error: {e}")
    
    return {"received": True}


async def handle_checkout_completed(db: AsyncIOMotorDatabase, session: dict):
    """
    Handle successful checkout session.
    
    Creates subscription record and updates user plan_code + limits.
    """
    metadata = session.get("metadata", {})
    user_id = metadata.get("user_id")
    plan_code = metadata.get("plan_id")  # This is plan_code, named plan_id in Stripe metadata
    billing_cycle = metadata.get("billing_cycle")
    subscription_id = session.get("subscription")
    customer_id = session.get("customer")
    
    if not all([user_id, plan_code, subscription_id]):
        logger.error(f"Missing data in checkout session: user_id={user_id}, plan_code={plan_code}, sub={subscription_id}")
        return
    
    # Normalize plan_code (handle legacy values)
    normalized_plan_code = normalize_plan_code(plan_code)
    
    # Get subscription details from Stripe
    stripe_sub = await stripe_service.get_subscription(subscription_id)
    
    now = datetime.utcnow()
    sub_id = generate_id()
    
    # Create subscription record with plan_code
    subscription_doc = {
        "_id": sub_id,
        "user_id": user_id,
        "stripe_customer_id": customer_id,
        "stripe_subscription_id": subscription_id,
        "plan_code": normalized_plan_code.value,  # NEW: Uses plan_code
        "billing_cycle": billing_cycle or "monthly",
        "status": "active",
        "current_period_start": stripe_sub["current_period_start"] if stripe_sub else now,
        "current_period_end": stripe_sub["current_period_end"] if stripe_sub else now,
        "cancel_at_period_end": False,
        "created_at": now,
        "updated_at": now
    }
    
    # Deactivate any existing subscriptions
    await db.subscriptions.update_many(
        {"user_id": user_id, "status": "active"},
        {"$set": {"status": "canceled", "updated_at": now}}
    )
    
    # Insert new subscription
    await db.subscriptions.insert_one(subscription_doc)
    
    # Update user plan_code and limits
    await update_user_plan_and_limits(db, user_id, normalized_plan_code.value)
    
    # Also update stripe_customer_id on user
    await db.users.update_one(
        {"_id": user_id},
        {"$set": {
            "stripe_customer_id": customer_id,
            "subscription.stripe_customer_id": customer_id,
            "subscription.stripe_subscription_id": subscription_id
        }}
    )
    
    logger.info(f"CHECKOUT COMPLETED | user={user_id} | plan_code={normalized_plan_code.value} | sub_id={sub_id}")


async def handle_subscription_updated(db: AsyncIOMotorDatabase, subscription: dict):
    """
    Handle subscription update (plan change, renewal, etc.).
    
    Updates subscription status and user limits.
    """
    stripe_subscription_id = subscription["id"]
    stripe_status = subscription["status"]
    cancel_at_period_end = subscription.get("cancel_at_period_end", False)
    
    # Map Stripe status to our status
    status_map = {
        "active": "active",
        "canceled": "canceled",
        "past_due": "past_due",
        "trialing": "trialing",
        "incomplete": "incomplete",
    }
    
    new_status = status_map.get(stripe_status, "active")
    now = datetime.utcnow()
    
    # Get our subscription record
    our_sub = await db.subscriptions.find_one({
        "stripe_subscription_id": stripe_subscription_id
    })
    
    if not our_sub:
        logger.warning(f"Subscription not found for Stripe ID: {stripe_subscription_id}")
        return
    
    # Update subscription record
    await db.subscriptions.update_one(
        {"stripe_subscription_id": stripe_subscription_id},
        {"$set": {
            "status": new_status,
            "cancel_at_period_end": cancel_at_period_end,
            "current_period_start": datetime.fromtimestamp(subscription["current_period_start"]),
            "current_period_end": datetime.fromtimestamp(subscription["current_period_end"]),
            "updated_at": now
        }}
    )
    
    # Update user subscription status
    user_id = our_sub.get("user_id")
    if user_id:
        await db.users.update_one(
            {"_id": user_id},
            {"$set": {
                "subscription.status": new_status,
                "updated_at": now
            }}
        )
    
    logger.info(f"SUBSCRIPTION UPDATED | stripe_sub={stripe_subscription_id} | status={new_status}")


async def handle_subscription_deleted(db: AsyncIOMotorDatabase, subscription: dict):
    """
    Handle subscription cancellation/deletion.
    
    Resets user to BASIC plan with BASIC limits.
    """
    stripe_subscription_id = subscription["id"]
    
    # Get our subscription record
    our_sub = await db.subscriptions.find_one({
        "stripe_subscription_id": stripe_subscription_id
    })
    
    now = datetime.utcnow()
    
    # Update subscription status
    await db.subscriptions.update_one(
        {"stripe_subscription_id": stripe_subscription_id},
        {"$set": {
            "status": "canceled",
            "updated_at": now
        }}
    )
    
    # Reset user to BASIC plan
    user_id = our_sub.get("user_id") if our_sub else subscription.get("metadata", {}).get("user_id")
    
    if user_id:
        await reset_user_to_basic(db, user_id)
    
    logger.info(f"SUBSCRIPTION DELETED | stripe_sub={stripe_subscription_id} | user={user_id} | reset to BASIC")


async def handle_payment_failed(db: AsyncIOMotorDatabase, invoice: dict):
    """
    Handle failed payment.
    
    POLICY: Reset user to BASIC immediately (Apple-safe, simple).
    User keeps their data but loses premium features until payment resolves.
    """
    subscription_id = invoice.get("subscription")
    
    if not subscription_id:
        return
    
    # Get our subscription record
    our_sub = await db.subscriptions.find_one({
        "stripe_subscription_id": subscription_id
    })
    
    now = datetime.utcnow()
    
    # Update subscription status to past_due
    await db.subscriptions.update_one(
        {"stripe_subscription_id": subscription_id},
        {"$set": {
            "status": "past_due",
            "updated_at": now
        }}
    )
    
    # POLICY: Reset user to BASIC immediately
    user_id = our_sub.get("user_id") if our_sub else None
    
    if user_id:
        await reset_user_to_basic(db, user_id)
        
        # Also update subscription status on user
        await db.users.update_one(
            {"_id": user_id},
            {"$set": {"subscription.status": "past_due"}}
        )
    
    logger.warning(f"PAYMENT FAILED | stripe_sub={subscription_id} | user={user_id} | reset to BASIC")


@router.get("/subscription", response_model=SubscriptionResponse)
async def get_subscription(
    db: AsyncIOMotorDatabase = Depends(get_database),
    current_user: User = Depends(get_current_user)
):
    """
    Get current user's active subscription.
    
    Returns plan_code, limits, and subscription details.
    """
    # Get active subscription
    subscription = await db.subscriptions.find_one({
        "user_id": current_user.id,
        "status": {"$in": ["active", "trialing", "past_due"]}
    })
    
    # Get user document for current limits
    user_doc = await db.users.find_one({"_id": current_user.id})
    
    if not subscription:
        # No subscription - return BASIC
        basic_limits = get_basic_limits()
        basic_def = get_plan_definition(PlanCode.BASIC)
        
        return SubscriptionResponse(
            has_subscription=False,
            subscription=None,
            plan_limits=basic_limits,
            plan_definition={
                "code": basic_def.code.value,
                "name": basic_def.name,
                "monthly_price_cad": basic_def.monthly_price_cad,
                "yearly_price_cad": basic_def.yearly_price_cad
            }
        )
    
    # Get plan_code from subscription
    plan_code_str = subscription.get("plan_code") or subscription.get("plan_id") or "BASIC"
    plan_code = normalize_plan_code(plan_code_str)
    plan_def = get_plan_definition(plan_code)
    plan_limits = compute_limits(plan_code.value)
    
    return SubscriptionResponse(
        has_subscription=True,
        subscription=SubscriptionInfo(
            id=subscription["_id"],
            user_id=subscription["user_id"],
            plan_code=plan_code,
            billing_cycle=BillingCycle(subscription.get("billing_cycle", "monthly")),
            status=subscription["status"],
            stripe_customer_id=subscription.get("stripe_customer_id"),
            stripe_subscription_id=subscription.get("stripe_subscription_id"),
            current_period_start=subscription.get("current_period_start"),
            current_period_end=subscription.get("current_period_end"),
            cancel_at_period_end=subscription.get("cancel_at_period_end", False),
            created_at=subscription["created_at"],
            updated_at=subscription["updated_at"]
        ),
        plan_limits=plan_limits,
        plan_definition={
            "code": plan_def.code.value,
            "name": plan_def.name,
            "monthly_price_cad": plan_def.monthly_price_cad,
            "yearly_price_cad": plan_def.yearly_price_cad
        }
    )


@router.post("/cancel")
async def cancel_subscription(
    db: AsyncIOMotorDatabase = Depends(get_database),
    current_user: User = Depends(get_current_user)
):
    """
    Cancel the current user's subscription (at period end).
    
    User keeps access until current_period_end.
    """
    subscription = await db.subscriptions.find_one({
        "user_id": current_user.id,
        "status": "active"
    })
    
    if not subscription:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Aucun abonnement actif trouvé"
        )
    
    # Cancel in Stripe (at period end)
    result = await stripe_service.cancel_subscription(
        subscription["stripe_subscription_id"],
        cancel_at_period_end=True
    )
    
    # Update local record
    await db.subscriptions.update_one(
        {"_id": subscription["_id"]},
        {"$set": {
            "cancel_at_period_end": True,
            "updated_at": datetime.utcnow()
        }}
    )
    
    logger.info(f"SUBSCRIPTION CANCEL SCHEDULED | user={current_user.email} | ends={result['current_period_end']}")
    
    return {
        "message": "Abonnement annulé. Vous aurez accès jusqu'à la fin de la période en cours.",
        "current_period_end": result["current_period_end"]
    }


@router.post("/portal", response_model=PortalSessionResponse)
async def create_portal_session(
    db: AsyncIOMotorDatabase = Depends(get_database),
    current_user: User = Depends(get_current_user)
):
    """
    Create a Stripe Customer Portal session.
    """
    # Get user's Stripe customer ID
    user = await db.users.find_one({"_id": current_user.id})
    stripe_customer_id = user.get("stripe_customer_id")
    
    if not stripe_customer_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Aucun compte de facturation trouvé"
        )
    
    return_url = f"{settings.frontend_url}/subscription"
    
    portal_url = await stripe_service.create_portal_session(
        stripe_customer_id,
        return_url
    )
    
    return PortalSessionResponse(portal_url=portal_url)


# ============================================================
# PLAN INFO ENDPOINT
# ============================================================

@router.get("/plans")
async def get_available_plans():
    """
    Get all available plans with pricing and limits.
    
    Useful for frontend to display plan comparison.
    """
    plans = []
    
    for plan_code, plan_def in PLAN_DEFINITIONS.items():
        plans.append({
            "code": plan_def.code.value,
            "name": plan_def.name,
            "description": plan_def.description,
            "monthly_price_cad": plan_def.monthly_price_cad,
            "yearly_price_cad": plan_def.yearly_price_cad,
            "limits": {
                "max_aircrafts": plan_def.limits.max_aircrafts,
                "ocr_per_month": plan_def.limits.ocr_per_month,
                "gps_logbook": plan_def.limits.gps_logbook,
                "tea_amo_sharing": plan_def.limits.tea_amo_sharing,
                "invoices": plan_def.limits.invoices,
                "cost_per_hour": plan_def.limits.cost_per_hour,
                "prebuy": plan_def.limits.prebuy,
            },
            "is_free": plan_def.monthly_price_cad == 0
        })
    
    return {"plans": plans}
