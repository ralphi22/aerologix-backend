from pydantic import BaseModel, EmailStr, Field
from typing import Optional
from datetime import datetime
from enum import Enum

class PlanTier(str, Enum):
    BASIC = "BASIC"
    PILOT = "PILOT"
    MAINTENANCE_PRO = "MAINTENANCE_PRO"
    FLEET_AI = "FLEET_AI"

# OCR limits per plan (monthly)
OCR_LIMITS_BY_PLAN = {
    PlanTier.BASIC: 5,            # Free: 5 scans/month
    PlanTier.PILOT: 25,           # Basic: 25 scans/month
    PlanTier.MAINTENANCE_PRO: 100, # Pro: 100 scans/month
    PlanTier.FLEET_AI: 500,       # Premium: 500 scans/month
}

class UserSubscription(BaseModel):
    plan: PlanTier = PlanTier.BASIC
    status: str = "active"  # active, trial, expired, canceled
    stripe_customer_id: Optional[str] = None
    stripe_subscription_id: Optional[str] = None
    trial_end: Optional[datetime] = None
    current_period_end: Optional[datetime] = None

class UserLimits(BaseModel):
    max_aircrafts: int = 1
    ocr_per_month: int = 5  # Default to Free plan limit
    logbook_entries_per_month: int = 10  # -1 = unlimited

class UserOCRUsage(BaseModel):
    """Track OCR usage per user"""
    scans_used: int = 0
    reset_date: Optional[datetime] = None

class UserBase(BaseModel):
    email: EmailStr
    name: str

class UserCreate(UserBase):
    password: str

class UserInDB(UserBase):
    id: str = Field(alias="_id")
    hashed_password: str
    created_at: datetime = Field(default_factory=datetime.utcnow)
    subscription: UserSubscription = Field(default_factory=UserSubscription)
    limits: UserLimits = Field(default_factory=UserLimits)
    ocr_usage: UserOCRUsage = Field(default_factory=UserOCRUsage)
    
    class Config:
        populate_by_name = True

class User(UserBase):
    id: str
    created_at: datetime
    subscription: UserSubscription
    limits: UserLimits

class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: User
