"""
Aircraft Share Model - Owner ↔ TEA/AMO sharing
TC-SAFE: Information only - owner and certified maintenance personnel remain responsible
"""

from pydantic import BaseModel, EmailStr, Field
from typing import Optional, Literal
from datetime import datetime
from enum import Enum


class ShareRole(str, Enum):
    VIEWER = "viewer"  # Read-only access
    CONTRIBUTOR = "contributor"  # Can add reports, parts, invoices


class ShareStatus(str, Enum):
    PENDING = "pending"  # Invitation sent, not accepted
    ACTIVE = "active"  # Accepted and active
    REVOKED = "revoked"  # Owner revoked access


class AircraftShareBase(BaseModel):
    aircraft_id: str
    mechanic_email: EmailStr
    role: ShareRole = ShareRole.VIEWER


class AircraftShareCreate(AircraftShareBase):
    pass


class AircraftShareInDB(AircraftShareBase):
    id: str = Field(alias="_id")
    owner_user_id: str
    mechanic_user_id: Optional[str] = None  # Set when mechanic accepts
    status: ShareStatus = ShareStatus.PENDING
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    accepted_at: Optional[datetime] = None
    revoked_at: Optional[datetime] = None
    # Audit fields
    created_by_user_id: str
    revoked_by_user_id: Optional[str] = None

    class Config:
        populate_by_name = True


class AircraftShare(AircraftShareBase):
    id: str
    owner_user_id: str
    mechanic_user_id: Optional[str] = None
    status: ShareStatus
    created_at: datetime
    updated_at: datetime
    accepted_at: Optional[datetime] = None
    revoked_at: Optional[datetime] = None


class ShareInviteRequest(BaseModel):
    aircraft_id: str
    mechanic_email: EmailStr
    role: ShareRole = ShareRole.VIEWER


class ShareAcceptRequest(BaseModel):
    share_id: str


class ShareRevokeRequest(BaseModel):
    share_id: str


class SharedAircraftInfo(BaseModel):
    """Aircraft info as seen by a mechanic"""
    aircraft_id: str
    registration: str
    manufacturer: Optional[str] = None
    model: Optional[str] = None
    owner_name: str
    owner_email: str
    role: ShareRole
    share_id: str
    shared_since: datetime
