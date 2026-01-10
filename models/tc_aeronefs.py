"""
TC_Aeronefs Model - Transport Canada Aircraft Registry

Official Canadian Civil Aircraft Register data from Transport Canada.
This is READ-ONLY reference data - no airworthiness or compliance logic.

Collection: tc_aeronefs
"""

from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime
from enum import Enum


class AircraftCategory(str, Enum):
    """Aircraft category from TC registry"""
    AEROPLANE = "Aeroplane"
    HELICOPTER = "Helicopter"
    GLIDER = "Glider"
    BALLOON = "Balloon"
    GYROPLANE = "Gyroplane"
    UNKNOWN = "Unknown"


class TCAeronefBase(BaseModel):
    """
    Base model for Transport Canada aircraft registry entry.
    
    Source: Transport Canada CARS (carscurr.txt + carsownr.txt)
    """
    # Primary identifier
    registration: str = Field(..., description="Aircraft registration (e.g., C-GABC)")
    
    # Aircraft info
    manufacturer: Optional[str] = Field(None, description="Manufacturer/Common name")
    model: Optional[str] = Field(None, description="Model name")
    designator: Optional[str] = Field(None, description="Type certificate number")
    serial_number: Optional[str] = Field(None, description="Manufacturer serial number")
    category: Optional[str] = Field(None, description="Aircraft category (Aeroplane, Helicopter, etc.)")
    
    # First owner info (from carsownr.txt)
    first_owner_given_name: Optional[str] = Field(None, description="Owner first name (empty for companies)")
    first_owner_family_name: Optional[str] = Field(None, description="Owner last name or company name")
    first_owner_full_name: Optional[str] = Field(None, description="Full owner name")
    first_owner_city: Optional[str] = Field(None, description="Owner city")
    first_owner_province: Optional[str] = Field(None, description="Owner province")
    
    # Validity dates
    validity_start: Optional[str] = Field(None, description="Registration effective date (YYYY-MM-DD)")
    validity_end: Optional[str] = Field(None, description="Registration expiry date (YYYY-MM-DD)")
    
    # Status
    status: Optional[str] = Field(None, description="Registration status (Registered, etc.)")


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
    
    # Extended TC data (optional, for reference)
    tc_data: Optional[dict] = Field(None, description="Additional TC fields")
    
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
# INDEX DEFINITIONS
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
    {
        "keys": [("first_owner_province", 1)],
        "name": "province_idx"
    },
    {
        "keys": [("category", 1)],
        "name": "category_idx"
    },
]
