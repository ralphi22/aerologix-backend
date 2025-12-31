from fastapi import APIRouter
from models.subscription_plan import SUBSCRIPTION_PLANS, SubscriptionPlan
from typing import List

router = APIRouter(prefix="/api/plans", tags=["subscription_plans"])

@router.get("", response_model=List[SubscriptionPlan])
async def get_all_plans():
    """Get all available subscription plans"""
    return list(SUBSCRIPTION_PLANS.values())

@router.get("/{tier}", response_model=SubscriptionPlan)
async def get_plan(tier: str):
    """Get a specific subscription plan"""
    from models.user import PlanTier
    try:
        plan_tier = PlanTier(tier.upper())
        return SUBSCRIPTION_PLANS[plan_tier]
    except (ValueError, KeyError):
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Plan not found")
