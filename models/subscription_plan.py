from pydantic import BaseModel
from typing import Dict, Any
from models.user import PlanTier

class SubscriptionPlanFeatures(BaseModel):
    max_aircrafts: int
    ocr_per_month: int  # -1 = unlimited
    logbook_entries_per_month: int  # -1 = unlimited
    has_predictive_maintenance: bool = False
    has_auto_notifications: bool = False
    has_parts_comparator: bool = False
    has_priority_support: bool = False
    has_mechanic_sharing: bool = False
    has_advanced_analytics: bool = False

class SubscriptionPlan(BaseModel):
    tier: PlanTier
    name: str
    description: str
    monthly_price: float  # in USD
    annual_price: float  # in USD
    trial_days: int = 0
    features: SubscriptionPlanFeatures
    stripe_monthly_price_id: str = "price_placeholder_monthly"
    stripe_annual_price_id: str = "price_placeholder_annual"

# Default subscription plans
SUBSCRIPTION_PLANS = {
    PlanTier.BASIC: SubscriptionPlan(
        tier=PlanTier.BASIC,
        name="Free",
        description="Plan gratuit pour démarrer",
        monthly_price=0.0,
        annual_price=0.0,
        trial_days=0,
        features=SubscriptionPlanFeatures(
            max_aircrafts=1,
            ocr_per_month=5,  # Free: 5 scans/month
            logbook_entries_per_month=10,
            has_predictive_maintenance=False,
            has_auto_notifications=False
        )
    ),
    PlanTier.PILOT: SubscriptionPlan(
        tier=PlanTier.PILOT,
        name="Basic",
        description="Recommandé pour pilotes individuels",
        monthly_price=19.0,
        annual_price=190.0,
        trial_days=7,
        features=SubscriptionPlanFeatures(
            max_aircrafts=1,
            ocr_per_month=25,  # Basic: 25 scans/month
            logbook_entries_per_month=-1,  # unlimited
            has_predictive_maintenance=True,
            has_auto_notifications=True,
            has_mechanic_sharing=True
        )
    ),
    PlanTier.MAINTENANCE_PRO: SubscriptionPlan(
        tier=PlanTier.MAINTENANCE_PRO,
        name="Pro",
        description="Pour gérer plusieurs avions",
        monthly_price=39.0,
        annual_price=390.0,
        trial_days=7,
        features=SubscriptionPlanFeatures(
            max_aircrafts=3,
            ocr_per_month=100,  # Pro: 100 scans/month
            logbook_entries_per_month=-1,
            has_predictive_maintenance=True,
            has_auto_notifications=True,
            has_mechanic_sharing=True,
            has_parts_comparator=True
        )
    ),
    PlanTier.FLEET_AI: SubscriptionPlan(
        tier=PlanTier.FLEET_AI,
        name="Premium",
        description="Solution complète pour flottes",
        monthly_price=75.0,
        annual_price=750.0,
        trial_days=7,
        features=SubscriptionPlanFeatures(
            max_aircrafts=-1,  # unlimited
            ocr_per_month=500,  # Premium: 500 scans/month
            logbook_entries_per_month=-1,
            has_predictive_maintenance=True,
            has_auto_notifications=True,
            has_mechanic_sharing=True,
            has_parts_comparator=True,
            has_priority_support=True,
            has_advanced_analytics=True
        )
    )
}
