"""
Payments Routes - Stripe subscription management
"""

from fastapi import APIRouter, Depends, HTTPException, status, Request, Header
from motor.motor_asyncio import AsyncIOMotorDatabase
from datetime import datetime
import logging

from database.mongodb import get_database
from services.auth_deps import get_current_user
from models.user import User
from models.subscription import (
    CheckoutSessionRequest,
    CheckoutSessionResponse,
    PortalSessionResponse,
    SubscriptionResponse,
    Subscription,
    PlanType,
    SubscriptionStatus,
    BillingCycle,
    PLAN_LIMITS
)
from services import stripe_service
from config import get_settings

router = APIRouter(prefix="/api/payments", tags=["payments"])
logger = logging.getLogger(__name__)
settings = get_settings()


def generate_id():
    """Generate unique ID"""
    import time
    return str(int(time.time() * 1000000))


@router.post("/create-checkout-session", response_model=CheckoutSessionResponse)
async def create_checkout_session(
    request: CheckoutSessionRequest,
    db: AsyncIOMotorDatabase = Depends(get_database),
    current_user: User = Depends(get_current_user)
):
    """
    Create a Stripe Checkout session for subscription.
    """
    # Check if user already has an active subscription
    existing = await db.subscriptions.find_one({
        "user_id": current_user.id,
        "status": {"$in": [SubscriptionStatus.ACTIVE.value, SubscriptionStatus.TRIALING.value]}
    })
    
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Vous avez déjà un abonnement actif. Gérez-le depuis le portail client."
        )
    
    # Get price ID
    price_id = settings.get_stripe_price_id(request.plan_id.value, request.billing_cycle.value)
    if not price_id or price_id.startswith("price_"):
        # Placeholder price - use test mode
        logger.warning(f"Using placeholder price ID for {request.plan_id}/{request.billing_cycle}")
    
    # Get or create Stripe customer
    stripe_customer_id = await stripe_service.get_or_create_customer(
        user_id=current_user.id,
        email=current_user.email,
        name=current_user.name
    )
    
    # Update user with Stripe customer ID if not set
    if not current_user.subscription.stripe_customer_id:
        await db.users.update_one(
            {"_id": current_user.id},
            {"$set": {"stripe_customer_id": stripe_customer_id}}
        )
    
    # Create checkout session
    success_url = f"{settings.frontend_url}/subscription?success=true&session_id={{CHECKOUT_SESSION_ID}}"
    cancel_url = f"{settings.frontend_url}/subscription?canceled=true"
    
    result = await stripe_service.create_checkout_session(
        customer_id=stripe_customer_id,
        price_id=price_id,
        success_url=success_url,
        cancel_url=cancel_url,
        user_id=current_user.id,
        plan_id=request.plan_id.value,
        billing_cycle=request.billing_cycle.value
    )
    
    logger.info(f"Checkout session created for user {current_user.email}, plan {request.plan_id}")
    
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
    """Handle successful checkout session."""
    user_id = session.get("metadata", {}).get("user_id")
    plan_id = session.get("metadata", {}).get("plan_id")
    billing_cycle = session.get("metadata", {}).get("billing_cycle")
    subscription_id = session.get("subscription")
    customer_id = session.get("customer")
    
    if not all([user_id, plan_id, subscription_id]):
        logger.error(f"Missing data in checkout session: {session}")
        return
    
    # Get subscription details from Stripe
    stripe_sub = await stripe_service.get_subscription(subscription_id)
    
    now = datetime.utcnow()
    sub_id = generate_id()
    
    # Create subscription record
    subscription_doc = {
        "_id": sub_id,
        "user_id": user_id,
        "stripe_customer_id": customer_id,
        "stripe_subscription_id": subscription_id,
        "plan_id": plan_id,
        "billing_cycle": billing_cycle or "monthly",
        "status": SubscriptionStatus.ACTIVE.value,
        "current_period_start": stripe_sub["current_period_start"] if stripe_sub else now,
        "current_period_end": stripe_sub["current_period_end"] if stripe_sub else now,
        "cancel_at_period_end": False,
        "created_at": now,
        "updated_at": now
    }
    
    # Deactivate any existing subscriptions
    await db.subscriptions.update_many(
        {"user_id": user_id, "status": SubscriptionStatus.ACTIVE.value},
        {"$set": {"status": SubscriptionStatus.CANCELED.value, "updated_at": now}}
    )
    
    # Insert new subscription
    await db.subscriptions.insert_one(subscription_doc)
    
    # Update user limits based on plan
    plan_limits = PLAN_LIMITS.get(PlanType(plan_id), PLAN_LIMITS[PlanType.SOLO])
    await db.users.update_one(
        {"_id": user_id},
        {"$set": {
            "stripe_customer_id": customer_id,
            "plan_id": plan_id,
            "limits.max_aircrafts": plan_limits["max_aircrafts"],
            "limits.has_fleet_access": plan_limits["has_fleet_access"],
            "limits.has_mechanic_sharing": plan_limits["has_mechanic_sharing"],
            "limits.ocr_per_month": plan_limits["ocr_per_month"],
            "updated_at": now
        }}
    )
    
    logger.info(f"Subscription created for user {user_id}: {plan_id}")


async def handle_subscription_updated(db: AsyncIOMotorDatabase, subscription: dict):
    """Handle subscription update (e.g., plan change, renewal)."""
    stripe_subscription_id = subscription["id"]
    status_value = subscription["status"]
    cancel_at_period_end = subscription.get("cancel_at_period_end", False)
    
    # Map Stripe status to our status
    status_map = {
        "active": SubscriptionStatus.ACTIVE.value,
        "canceled": SubscriptionStatus.CANCELED.value,
        "past_due": SubscriptionStatus.PAST_DUE.value,
        "trialing": SubscriptionStatus.TRIALING.value,
        "incomplete": SubscriptionStatus.INCOMPLETE.value,
    }
    
    new_status = status_map.get(status_value, SubscriptionStatus.ACTIVE.value)
    
    now = datetime.utcnow()
    
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
    
    logger.info(f"Subscription {stripe_subscription_id} updated to status: {new_status}")


async def handle_subscription_deleted(db: AsyncIOMotorDatabase, subscription: dict):
    """Handle subscription cancellation/deletion."""
    stripe_subscription_id = subscription["id"]
    user_id = subscription.get("metadata", {}).get("user_id")
    
    now = datetime.utcnow()
    
    # Update subscription status
    await db.subscriptions.update_one(
        {"stripe_subscription_id": stripe_subscription_id},
        {"$set": {
            "status": SubscriptionStatus.CANCELED.value,
            "updated_at": now
        }}
    )
    
    # Reset user to free tier limits
    if user_id:
        await db.users.update_one(
            {"_id": user_id},
            {"$set": {
                "plan_id": None,
                "limits.max_aircrafts": 1,
                "limits.has_fleet_access": False,
                "limits.has_mechanic_sharing": False,
                "limits.ocr_per_month": 5,
                "updated_at": now
            }}
        )
    
    logger.info(f"Subscription {stripe_subscription_id} deleted")


async def handle_payment_failed(db: AsyncIOMotorDatabase, invoice: dict):
    """Handle failed payment."""
    subscription_id = invoice.get("subscription")
    
    if subscription_id:
        await db.subscriptions.update_one(
            {"stripe_subscription_id": subscription_id},
            {"$set": {
                "status": SubscriptionStatus.PAST_DUE.value,
                "updated_at": datetime.utcnow()
            }}
        )
    
    logger.warning(f"Payment failed for subscription {subscription_id}")


@router.get("/subscription", response_model=SubscriptionResponse)
async def get_subscription(
    db: AsyncIOMotorDatabase = Depends(get_database),
    current_user: User = Depends(get_current_user)
):
    """
    Get current user's active subscription.
    """
    subscription = await db.subscriptions.find_one({
        "user_id": current_user.id,
        "status": {"$in": [
            SubscriptionStatus.ACTIVE.value,
            SubscriptionStatus.TRIALING.value,
            SubscriptionStatus.PAST_DUE.value
        ]}
    })
    
    if not subscription:
        return SubscriptionResponse(
            has_subscription=False,
            subscription=None,
            plan_limits=PLAN_LIMITS[PlanType.SOLO]  # Free tier
        )
    
    plan_id = PlanType(subscription["plan_id"])
    
    return SubscriptionResponse(
        has_subscription=True,
        subscription=Subscription(
            id=subscription["_id"],
            user_id=subscription["user_id"],
            stripe_customer_id=subscription["stripe_customer_id"],
            stripe_subscription_id=subscription["stripe_subscription_id"],
            plan_id=plan_id,
            billing_cycle=BillingCycle(subscription.get("billing_cycle", "monthly")),
            status=SubscriptionStatus(subscription["status"]),
            current_period_start=subscription.get("current_period_start"),
            current_period_end=subscription.get("current_period_end"),
            cancel_at_period_end=subscription.get("cancel_at_period_end", False),
            created_at=subscription["created_at"],
            updated_at=subscription["updated_at"]
        ),
        plan_limits=PLAN_LIMITS[plan_id]
    )


@router.post("/cancel")
async def cancel_subscription(
    db: AsyncIOMotorDatabase = Depends(get_database),
    current_user: User = Depends(get_current_user)
):
    """
    Cancel the current user's subscription (at period end).
    """
    subscription = await db.subscriptions.find_one({
        "user_id": current_user.id,
        "status": SubscriptionStatus.ACTIVE.value
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
    
    logger.info(f"Subscription cancelled for user {current_user.email}")
    
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
