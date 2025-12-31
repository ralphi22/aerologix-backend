"""
Subscription Model - Stripe subscription management
"""

from pydantic import BaseModel, Field
from typing import Optional, Literal
from datetime import datetime
from enum import Enum


class PlanType(str, Enum):
    SOLO = "solo"
    PRO = "pro"
    FLEET = "fleet"


class SubscriptionStatus(str, Enum):
    ACTIVE = "active"
    CANCELED = "canceled"
    PAST_DUE = "past_due"
    TRIALING = "trialing"
    INCOMPLETE = "incomplete"


class BillingCycle(str, Enum):
    MONTHLY = "monthly"
    YEARLY = "yearly"


# Plan limits configuration
PLAN_LIMITS = {
    PlanType.SOLO: {
        "max_aircrafts": 1,
        "has_fleet_access": False,
        "has_mechanic_sharing": False,
        "ocr_per_month": 10,
    },
    PlanType.PRO: {
        "max_aircrafts": 3,
        "has_fleet_access": False,
        "has_mechanic_sharing": True,
        "ocr_per_month": 50,
    },
    PlanType.FLEET: {
        "max_aircrafts": -1,  # Unlimited
        "has_fleet_access": True,
        "has_mechanic_sharing": True,
        "ocr_per_month": -1,  # Unlimited
    },
}


class SubscriptionBase(BaseModel):
    user_id: str
    plan_id: PlanType
    billing_cycle: BillingCycle = BillingCycle.MONTHLY


class SubscriptionCreate(SubscriptionBase):
    stripe_customer_id: str
    stripe_subscription_id: str


class SubscriptionInDB(SubscriptionBase):
    id: str = Field(alias="_id")
    stripe_customer_id: str
    stripe_subscription_id: str
    status: SubscriptionStatus = SubscriptionStatus.ACTIVE
    current_period_start: Optional[datetime] = None
    current_period_end: Optional[datetime] = None
    cancel_at_period_end: bool = False
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    class Config:
        populate_by_name = True


class Subscription(SubscriptionBase):
    id: str
    stripe_customer_id: str
    stripe_subscription_id: str
    status: SubscriptionStatus
    current_period_start: Optional[datetime] = None
    current_period_end: Optional[datetime] = None
    cancel_at_period_end: bool = False
    created_at: datetime
    updated_at: datetime


class SubscriptionResponse(BaseModel):
    """Response for subscription endpoint"""
    has_subscription: bool
    subscription: Optional[Subscription] = None
    plan_limits: Optional[dict] = None


class CheckoutSessionRequest(BaseModel):
    plan_id: PlanType
    billing_cycle: BillingCycle  # OBLIGATOIRE - pas de fallback


class CheckoutSessionResponse(BaseModel):
    checkout_url: str
    session_id: str


class PortalSessionResponse(BaseModel):
    portal_url: str
