"""
Plans Model - Unified plan system for AeroLogix AI
This is the SINGLE SOURCE OF TRUTH for all plan definitions.

OFFICIAL PRICING (CAD):
- BASIC: $0 (free)
- PILOT: $24/month or $240/year
- PILOT_PRO: $39/month or $390/year  
- FLEET: $65/month or $650/year
"""

from pydantic import BaseModel
from typing import Optional, Dict, Any
from enum import Enum


class PlanCode(str, Enum):
    """
    Unified plan codes - SINGLE SOURCE OF TRUTH.
    Replaces: PlanTier, PlanType, solo/pro/fleet
    """
    BASIC = "BASIC"           # Free tier
    PILOT = "PILOT"           # $24/mo
    PILOT_PRO = "PILOT_PRO"   # $39/mo
    FLEET = "FLEET"           # $65/mo


class BillingCycle(str, Enum):
    """Billing cycle options"""
    MONTHLY = "monthly"
    YEARLY = "yearly"


class PlanLimits(BaseModel):
    """
    Plan limits configuration.
    -1 means unlimited.
    """
    max_aircrafts: int = 1
    ocr_per_month: int = 5
    gps_logbook: bool = False
    tea_amo_sharing: bool = False
    invoices: bool = False
    cost_per_hour: bool = False
    prebuy: bool = False


class PlanDefinition(BaseModel):
    """Full plan definition with pricing"""
    code: PlanCode
    name: str
    description: str
    monthly_price_cad: float
    yearly_price_cad: float
    limits: PlanLimits
    stripe_price_monthly: Optional[str] = None  # env var name
    stripe_price_yearly: Optional[str] = None   # env var name


# ============================================================
# OFFICIAL PLAN DEFINITIONS - SOURCE OF TRUTH
# ============================================================

PLAN_DEFINITIONS: Dict[PlanCode, PlanDefinition] = {
    PlanCode.BASIC: PlanDefinition(
        code=PlanCode.BASIC,
        name="Basic",
        description="Plan gratuit pour découvrir AeroLogix",
        monthly_price_cad=0.0,
        yearly_price_cad=0.0,
        limits=PlanLimits(
            max_aircrafts=1,
            ocr_per_month=5,
            gps_logbook=False,
            tea_amo_sharing=False,
            invoices=False,
            cost_per_hour=False,
            prebuy=False
        ),
        stripe_price_monthly=None,  # Free - no Stripe
        stripe_price_yearly=None
    ),
    
    PlanCode.PILOT: PlanDefinition(
        code=PlanCode.PILOT,
        name="Pilot",
        description="Pour pilotes propriétaires - 1 aéronef",
        monthly_price_cad=24.0,
        yearly_price_cad=240.0,
        limits=PlanLimits(
            max_aircrafts=1,
            ocr_per_month=10,
            gps_logbook=True,
            tea_amo_sharing=True,
            invoices=True,
            cost_per_hour=True,
            prebuy=False
        ),
        stripe_price_monthly="STRIPE_PRICE_PILOT_MONTHLY",
        stripe_price_yearly="STRIPE_PRICE_PILOT_YEARLY"
    ),
    
    PlanCode.PILOT_PRO: PlanDefinition(
        code=PlanCode.PILOT_PRO,
        name="Pilot Pro",
        description="Pour pilotes avec plusieurs aéronefs",
        monthly_price_cad=39.0,
        yearly_price_cad=390.0,
        limits=PlanLimits(
            max_aircrafts=3,
            ocr_per_month=-1,  # Unlimited
            gps_logbook=True,
            tea_amo_sharing=True,
            invoices=True,
            cost_per_hour=True,
            prebuy=False
        ),
        stripe_price_monthly="STRIPE_PRICE_PILOT_PRO_MONTHLY",
        stripe_price_yearly="STRIPE_PRICE_PILOT_PRO_YEARLY"
    ),
    
    PlanCode.FLEET: PlanDefinition(
        code=PlanCode.FLEET,
        name="Fleet",
        description="Pour gestionnaires de flotte",
        monthly_price_cad=65.0,
        yearly_price_cad=650.0,
        limits=PlanLimits(
            max_aircrafts=-1,  # Unlimited
            ocr_per_month=-1,  # Unlimited
            gps_logbook=True,
            tea_amo_sharing=True,
            invoices=True,
            cost_per_hour=True,
            prebuy=True
        ),
        stripe_price_monthly="STRIPE_PRICE_FLEET_MONTHLY",
        stripe_price_yearly="STRIPE_PRICE_FLEET_YEARLY"
    ),
}


# ============================================================
# HELPER FUNCTIONS
# ============================================================

def get_plan_definition(plan_code: PlanCode) -> PlanDefinition:
    """Get plan definition by code"""
    return PLAN_DEFINITIONS.get(plan_code, PLAN_DEFINITIONS[PlanCode.BASIC])


def get_plan_limits(plan_code: PlanCode) -> PlanLimits:
    """Get plan limits by code"""
    definition = get_plan_definition(plan_code)
    return definition.limits


def compute_limits(plan_code: str) -> Dict[str, Any]:
    """
    Compute limits dict from plan_code string.
    Returns dict suitable for MongoDB update.
    """
    try:
        code = PlanCode(plan_code)
    except ValueError:
        code = PlanCode.BASIC
    
    limits = get_plan_limits(code)
    
    return {
        "max_aircrafts": limits.max_aircrafts,
        "ocr_per_month": limits.ocr_per_month,
        "gps_logbook": limits.gps_logbook,
        "tea_amo_sharing": limits.tea_amo_sharing,
        "invoices": limits.invoices,
        "cost_per_hour": limits.cost_per_hour,
        "prebuy": limits.prebuy,
    }


def get_basic_limits() -> Dict[str, Any]:
    """Get BASIC plan limits as dict"""
    return compute_limits(PlanCode.BASIC.value)


# ============================================================
# LEGACY MAPPING (for migration)
# ============================================================

LEGACY_PLAN_MAPPING = {
    # Old PlanTier values
    "BASIC": PlanCode.BASIC,
    "PILOT": PlanCode.PILOT,
    "MAINTENANCE_PRO": PlanCode.PILOT_PRO,
    "FLEET_AI": PlanCode.FLEET,
    
    # Old PlanType values (lowercase)
    "solo": PlanCode.PILOT,
    "pro": PlanCode.PILOT_PRO,
    "fleet": PlanCode.FLEET,
}


def normalize_plan_code(legacy_value: str) -> PlanCode:
    """
    Convert legacy plan values to new PlanCode.
    Handles: PlanTier, PlanType, solo/pro/fleet
    """
    if not legacy_value:
        return PlanCode.BASIC
    
    # Try direct match
    try:
        return PlanCode(legacy_value)
    except ValueError:
        pass
    
    # Try legacy mapping
    if legacy_value in LEGACY_PLAN_MAPPING:
        return LEGACY_PLAN_MAPPING[legacy_value]
    
    # Default to BASIC
    return PlanCode.BASIC
