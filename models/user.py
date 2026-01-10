"""
User Model for AeroLogix AI

Uses unified plan_code system from models/plans.py
"""

from pydantic import BaseModel, EmailStr, Field
from typing import Optional
from datetime import datetime
from enum import Enum


# ============================================================
# DEPRECATED: Old PlanTier enum - kept for backward compatibility
# Use PlanCode from models/plans.py instead
# ============================================================

class PlanTier(str, Enum):
    """DEPRECATED: Use PlanCode from models/plans.py"""
    BASIC = "BASIC"
    PILOT = "PILOT"
    MAINTENANCE_PRO = "MAINTENANCE_PRO"
    FLEET_AI = "FLEET_AI"


# DEPRECATED: Legacy OCR limits mapping
# Use compute_limits() from models/plans.py instead
OCR_LIMITS_BY_PLAN = {
    PlanTier.BASIC: 5,
    PlanTier.PILOT: 10,
    PlanTier.MAINTENANCE_PRO: -1,  # Unlimited
    PlanTier.FLEET_AI: -1,         # Unlimited
}


# ============================================================
# USER SUBSCRIPTION MODEL (Updated with plan_code)
# ============================================================

class UserSubscription(BaseModel):
    """
    User subscription embedded document.
    
    NEW: Uses plan_code as source of truth.
    LEGACY: 'plan' field kept for backward compatibility.
    """
    # NEW: Unified plan code (BASIC, PILOT, PILOT_PRO, FLEET)
    plan_code: str = "BASIC"
    
    # LEGACY: Old plan field - kept for backward compatibility
    plan: PlanTier = PlanTier.BASIC
    
    # Status
    status: str = "active"  # active, trial, expired, canceled, past_due
    
    # Stripe IDs
    stripe_customer_id: Optional[str] = None
    stripe_subscription_id: Optional[str] = None
    
    # Dates
    trial_end: Optional[datetime] = None
    current_period_end: Optional[datetime] = None


# ============================================================
# USER LIMITS MODEL (Updated with new features)
# ============================================================

class UserLimits(BaseModel):
    """
    User limits based on subscription plan.
    
    Computed from plan_code using compute_limits() from models/plans.py.
    -1 means unlimited.
    """
    # Core limits
    max_aircrafts: int = 1
    ocr_per_month: int = 5
    logbook_entries_per_month: int = 10  # Legacy - kept for compatibility
    
    # Feature flags
    gps_logbook: bool = False
    tea_amo_sharing: bool = False
    invoices: bool = False
    cost_per_hour: bool = False
    prebuy: bool = False
    
    # Legacy feature flags (for compatibility)
    has_fleet_access: bool = False
    has_mechanic_sharing: bool = False


# ============================================================
# USER OCR USAGE MODEL
# ============================================================

class UserOCRUsage(BaseModel):
    """Track OCR usage per user"""
    scans_used: int = 0
    reset_date: Optional[datetime] = None


# ============================================================
# USER MODELS
# ============================================================

class UserBase(BaseModel):
    email: EmailStr
    name: str


class UserCreate(UserBase):
    password: str


class UserInDB(UserBase):
    """User document stored in MongoDB"""
    id: str = Field(alias="_id")
    hashed_password: str
    created_at: datetime = Field(default_factory=datetime.utcnow)
    subscription: UserSubscription = Field(default_factory=UserSubscription)
    limits: UserLimits = Field(default_factory=UserLimits)
    ocr_usage: UserOCRUsage = Field(default_factory=UserOCRUsage)
    stripe_customer_id: Optional[str] = None  # Also at root level for easy access
    
    class Config:
        populate_by_name = True


class User(UserBase):
    """User model returned by API (no sensitive data)"""
    id: str
    created_at: datetime
    subscription: UserSubscription
    limits: UserLimits


# ============================================================
# AUTH MODELS
# ============================================================

class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: User
