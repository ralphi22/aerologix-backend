"""
TC_Aeronefs Model - Transport Canada Aircraft Registry

PRIVACY COMPLIANT VERSION
- Open Government Canada License
- Canadian Privacy Act (PIPEDA)
- App Store Privacy Guidelines

This model contains ONLY public, non-personal data from the TC registry.
Personal information (addresses, cities, detailed owner info) is NOT stored.

Collection: tc_aeronefs
"""

from pydantic import BaseModel, Field, field_validator
from typing import Optional, List
from datetime import datetime


# ============================================================
# ALLOWED FIELDS (WHITELIST)
# ============================================================

ALLOWED_FIELDS = frozenset([
    "_id",
    "registration",
    "manufacturer", 
    "model",
    "designator",
    "first_owner_given_name",
    "first_owner_family_name",
    "validity_start",
    "validity_end",
    "tc_version",
    "created_at",
    "updated_at",
])

# Fields that are FORBIDDEN (privacy reasons)
FORBIDDEN_FIELDS = frozenset([
    "first_owner_city",
    "first_owner_province",
    "first_owner_full_name",
    "serial_number",
    "status",
    "category",
    "tc_data",
    "street",
    "postal_code",
    "address",
    "phone",
    "email",
])


# ============================================================
# PYDANTIC MODELS
# ============================================================

class TCAeronefBase(BaseModel):
    """
    Base model for Transport Canada aircraft registry entry.
    
    PRIVACY COMPLIANT: Contains only public registration data.
    NO personal information (addresses, cities, etc.)
    """
    # Primary identifier
    registration: str = Field(..., description="Aircraft registration (e.g., C-GABC)")
    
    # Aircraft info (public)
    manufacturer: Optional[str] = Field(None, description="Manufacturer/Common name")
    model: Optional[str] = Field(None, description="Model name")
    designator: Optional[str] = Field(None, description="Type certificate number")
    
    # Owner info (names only - public record)
    first_owner_given_name: Optional[str] = Field(None, description="Owner first name")
    first_owner_family_name: Optional[str] = Field(None, description="Owner last name or company name")
    
    # Validity dates (public)
    validity_start: Optional[str] = Field(None, description="Registration effective date (YYYY-MM-DD)")
    validity_end: Optional[str] = Field(None, description="Registration expiry date (YYYY-MM-DD)")


class TCAeronefCreate(TCAeronefBase):
    """Model for creating a TC_Aeronefs document"""
    tc_version: str = Field(..., description="TC data version (e.g., 2026Q1)")


class TCAeronef(TCAeronefBase):
    """
    Full TC_Aeronefs document as stored in MongoDB.
    
    Indexes:
    - registration (unique)
    - manufacturer
    - tc_version
    """
    id: str = Field(alias="_id")
    tc_version: str = Field(..., description="TC data version (e.g., 2026Q1)")
    
    # Metadata
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    
    class Config:
        populate_by_name = True


class TCAeronefResponse(TCAeronefBase):
    """API response model for TC_Aeronefs"""
    id: str
    tc_version: str
    created_at: datetime
    updated_at: datetime


# ============================================================
# INDEX DEFINITIONS (Reduced for privacy)
# ============================================================

TC_AERONEFS_INDEXES = [
    {
        "keys": [("registration", 1)],
        "unique": True,
        "name": "registration_unique"
    },
    {
        "keys": [("manufacturer", 1)],
        "name": "manufacturer_idx"
    },
    {
        "keys": [("tc_version", 1)],
        "name": "tc_version_idx"
    },
]


# ============================================================
# PRIVACY ENFORCEMENT FUNCTIONS
# ============================================================

def sanitize_record(record: dict) -> dict:
    """
    Remove all non-allowed fields from a record.
    
    This ensures NO personal/sensitive data is ever stored.
    """
    sanitized = {}
    
    for key, value in record.items():
        if key in ALLOWED_FIELDS:
            sanitized[key] = value
        # Silently drop forbidden fields
    
    return sanitized


def validate_record(record: dict) -> List[str]:
    """
    Validate a record contains only allowed fields.
    
    Returns list of forbidden fields found (empty if valid).
    """
    forbidden_found = []
    
    for key in record.keys():
        if key in FORBIDDEN_FIELDS:
            forbidden_found.append(key)
        elif key not in ALLOWED_FIELDS:
            forbidden_found.append(key)
    
    return forbidden_found
