"""
Stripe Service - Handle Stripe API interactions
"""

import stripe
import logging
from typing import Optional
from datetime import datetime

from config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

# Initialize Stripe
stripe.api_key = settings.stripe_secret_key


async def get_or_create_customer(user_id: str, email: str, name: str = None) -> str:
    """
    Get existing Stripe customer or create a new one.
    Returns the Stripe customer ID.
    """
    try:
        # Search for existing customer by metadata
        customers = stripe.Customer.search(
            query=f"metadata['user_id']:'{user_id}'"
        )
        
        if customers.data:
            return customers.data[0].id
        
        # Create new customer
        customer = stripe.Customer.create(
            email=email,
            name=name,
            metadata={"user_id": user_id}
        )
        
        logger.info(f"Created Stripe customer {customer.id} for user {user_id}")
        return customer.id
        
    except stripe.error.StripeError as e:
        logger.error(f"Stripe error creating customer: {e}")
        raise


async def create_checkout_session(
    customer_id: str,
    price_id: str,
    success_url: str,
    cancel_url: str,
    user_id: str,
    plan_id: str,
    billing_cycle: str
) -> dict:
    """
    Create a Stripe Checkout session for subscription.
    """
    try:
        session = stripe.checkout.Session.create(
            customer=customer_id,
            payment_method_types=["card"],
            mode="subscription",
            line_items=[{
                "price": price_id,
                "quantity": 1
            }],
            success_url=success_url,
            cancel_url=cancel_url,
            metadata={
                "user_id": user_id,
                "plan_id": plan_id,
                "billing_cycle": billing_cycle
            },
            subscription_data={
                "metadata": {
                    "user_id": user_id,
                    "plan_id": plan_id,
                    "billing_cycle": billing_cycle
                }
            },
            allow_promotion_codes=True,
        )
        
        logger.info(f"Created checkout session {session.id} for user {user_id}, plan {plan_id}")
        
        return {
            "session_id": session.id,
            "checkout_url": session.url
        }
        
    except stripe.error.StripeError as e:
        logger.error(f"Stripe error creating checkout session: {e}")
        raise


async def cancel_subscription(stripe_subscription_id: str, cancel_at_period_end: bool = True) -> dict:
    """
    Cancel a Stripe subscription.
    By default, cancels at end of current period.
    """
    try:
        if cancel_at_period_end:
            # Cancel at period end (user keeps access until then)
            subscription = stripe.Subscription.modify(
                stripe_subscription_id,
                cancel_at_period_end=True
            )
        else:
            # Cancel immediately
            subscription = stripe.Subscription.delete(stripe_subscription_id)
        
        logger.info(f"Cancelled subscription {stripe_subscription_id}")
        
        return {
            "status": subscription.status,
            "cancel_at_period_end": subscription.cancel_at_period_end,
            "current_period_end": datetime.fromtimestamp(subscription.current_period_end)
        }
        
    except stripe.error.StripeError as e:
        logger.error(f"Stripe error cancelling subscription: {e}")
        raise


async def create_portal_session(customer_id: str, return_url: str) -> str:
    """
    Create a Stripe Customer Portal session.
    Returns the portal URL.
    """
    try:
        session = stripe.billing_portal.Session.create(
            customer=customer_id,
            return_url=return_url
        )
        
        logger.info(f"Created portal session for customer {customer_id}")
        return session.url
        
    except stripe.error.StripeError as e:
        logger.error(f"Stripe error creating portal session: {e}")
        raise


async def get_subscription(stripe_subscription_id: str) -> Optional[dict]:
    """
    Get subscription details from Stripe.
    """
    try:
        subscription = stripe.Subscription.retrieve(stripe_subscription_id)
        
        return {
            "id": subscription.id,
            "status": subscription.status,
            "current_period_start": datetime.fromtimestamp(getattr(subscription, 'current_period_start', 0)) if getattr(subscription, 'current_period_start', None) else None,
            "current_period_end": datetime.fromtimestamp(getattr(subscription, 'current_period_end', 0)) if getattr(subscription, 'current_period_end', None) else None,
            "cancel_at_period_end": getattr(subscription, 'cancel_at_period_end', False),
            "plan_id": subscription.metadata.get("plan_id") if hasattr(subscription, 'metadata') else None,
            "billing_cycle": subscription.metadata.get("billing_cycle") if hasattr(subscription, 'metadata') else None
        }
        
    except Exception as e:
        logger.error(f"Error getting subscription {stripe_subscription_id}: {e}")
        return None


def verify_webhook_signature(payload: bytes, sig_header: str) -> dict:
    """
    Verify Stripe webhook signature and return the event.
    """
    try:
        event = stripe.Webhook.construct_event(
            payload,
            sig_header,
            settings.stripe_webhook_secret
        )
        return event
        
    except stripe.error.SignatureVerificationError as e:
        logger.error(f"Webhook signature verification failed: {e}")
        raise
    except ValueError as e:
        logger.error(f"Invalid webhook payload: {e}")
        raise
