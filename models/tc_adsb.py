"""
TC AD/SB Models - Transport Canada Airworthiness Directives & Service Bulletins

These collections store the official TC regulatory requirements.
Used for comparison against aircraft maintenance records.

IMPORTANT: TC-SAFE
- Never return "compliant" / "non-compliant"
- Only return "found" / "missing" / "status: info_only"
- All compliance decisions are made by licensed AME/TEA

Collections:
- tc_ad: Airworthiness Directives
- tc_sb: Service Bulletins
"""

from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime
from enum import Enum


# ============================================================
# ENUMS
# ============================================================

class ADSBType(str, Enum):
    """Type of regulatory document"""
    AD = "AD"  # Airworthiness Directive (mandatory)
    SB = "SB"  # Service Bulletin (may be mandatory or recommended)


class RecurrenceType(str, Enum):
    """Recurrence type for inspections"""
    ONCE = "ONCE"           # One-time compliance
    YEARS = "YEARS"         # Every X years
    HOURS = "HOURS"         # Every X flight hours
    CYCLES = "CYCLES"       # Every X cycles (landings)
    CALENDAR = "CALENDAR"   # Calendar-based (months)


class ImportSource(str, Enum):
    """Source of AD/SB data - for traceability"""
    TC_SEED = "TC_SEED"           # Initial seed/test data
    TC_PDF_IMPORT = "TC_PDF_IMPORT"  # Manual PDF import by user
    TC_CAWIS = "TC_CAWIS"         # CAWIS web import (future)
    TC_OFFICIAL = "TC_OFFICIAL"   # Official TC database (authoritative)


class ADSBScope(str, Enum):
    """Scope of AD/SB applicability"""
    AIRFRAME = "airframe"
    ENGINE = "engine"
    PROPELLER = "propeller"
    APPLIANCE = "appliance"
    UNSPECIFIED = "unspecified"


class ComparisonStatus(str, Enum):
    """Status of comparison (TC-SAFE: never compliance)"""
    OK = "OK"                       # Item found, not due
    DUE_SOON = "DUE_SOON"           # Item found, recurrence coming up (< 90 days or < 50 hours)
    MISSING = "MISSING"             # Item not found in aircraft records
    NEW_REGULATORY = "NEW"          # New TC item since last logbook entry
    INFO_ONLY = "INFO_ONLY"         # Informational only (OCR item not in TC)


# ============================================================
# TC_AD MODEL
# ============================================================

class TCADBase(BaseModel):
    """
    Transport Canada Airworthiness Directive
    
    ADs are mandatory regulatory requirements.
    """
    ref: str = Field(..., description="AD reference number (e.g., CF-2024-01)")
    type: ADSBType = Field(default=ADSBType.AD)
    
    # Applicability
    designator: Optional[str] = Field(None, description="Type certificate designator")
    manufacturer: Optional[str] = Field(None, description="Manufacturer name")
    model: Optional[str] = Field(None, description="Aircraft model(s) affected")
    serial_range: Optional[str] = Field(None, description="Serial number range if applicable")
    
    # Dates
    effective_date: Optional[datetime] = Field(None, description="Date AD became effective")
    
    # Recurrence
    recurrence_type: RecurrenceType = Field(default=RecurrenceType.ONCE)
    recurrence_value: Optional[int] = Field(None, description="Value for recurrence (years/hours/cycles)")
    
    # Content
    title: Optional[str] = Field(None, description="AD title/subject")
    compliance_text: Optional[str] = Field(None, description="Compliance requirements (informational)")
    source_url: Optional[str] = Field(None, description="URL to official TC document")
    
    # Status
    is_active: bool = Field(default=True, description="Whether AD is currently active")
    superseded_by: Optional[str] = Field(None, description="Reference of superseding AD")


class TCAD(TCADBase):
    """Full TC_AD document as stored in MongoDB"""
    id: str = Field(alias="_id")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    
    class Config:
        populate_by_name = True


# ============================================================
# TC_SB MODEL
# ============================================================

class TCSBBase(BaseModel):
    """
    Transport Canada / Manufacturer Service Bulletin
    
    SBs may be mandatory (if referenced by AD) or recommended.
    """
    ref: str = Field(..., description="SB reference number (e.g., SB-172-001)")
    type: ADSBType = Field(default=ADSBType.SB)
    
    # Applicability
    designator: Optional[str] = Field(None, description="Type certificate designator")
    manufacturer: Optional[str] = Field(None, description="Manufacturer name")
    model: Optional[str] = Field(None, description="Aircraft model(s) affected")
    serial_range: Optional[str] = Field(None, description="Serial number range if applicable")
    
    # Dates
    effective_date: Optional[datetime] = Field(None, description="Date SB was issued")
    
    # Recurrence
    recurrence_type: RecurrenceType = Field(default=RecurrenceType.ONCE)
    recurrence_value: Optional[int] = Field(None, description="Value for recurrence")
    
    # Content
    title: Optional[str] = Field(None, description="SB title/subject")
    compliance_text: Optional[str] = Field(None, description="Compliance requirements (informational)")
    source_url: Optional[str] = Field(None, description="URL to official document")
    
    # Relationship
    related_ad: Optional[str] = Field(None, description="Related AD reference if mandatory")
    is_mandatory: bool = Field(default=False, description="Whether SB is mandatory (via AD)")
    
    # Status
    is_active: bool = Field(default=True)
    superseded_by: Optional[str] = Field(None)


class TCSB(TCSBBase):
    """Full TC_SB document as stored in MongoDB"""
    id: str = Field(alias="_id")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    
    class Config:
        populate_by_name = True


# ============================================================
# COMPARISON RESULT MODELS
# ============================================================

class ADSBComparisonItem(BaseModel):
    """Single item in the comparison result"""
    ref: str
    type: ADSBType
    title: Optional[str] = None
    found: bool
    last_recorded_date: Optional[str] = None
    recurrence_type: RecurrenceType
    recurrence_value: Optional[int] = None
    next_due: Optional[str] = None
    status: ComparisonStatus
    source: Optional[str] = None  # "tc" or "ocr"


class NewTCItem(BaseModel):
    """New TC regulatory item since last logbook"""
    ref: str
    type: ADSBType
    title: Optional[str] = None
    effective_date: str
    source_url: Optional[str] = None


class ADSBComparisonResponse(BaseModel):
    """
    Full comparison response
    
    TC-SAFE: Contains only factual comparison data.
    Does NOT include compliance status or recommendations.
    """
    aircraft_id: str
    registration: Optional[str] = None
    designator: Optional[str] = None
    manufacturer: Optional[str] = None
    model: Optional[str] = None
    last_logbook_date: Optional[str] = None
    total_tc_items: int = 0
    found_count: int = 0
    missing_count: int = 0
    new_tc_items: List[NewTCItem] = []
    comparison: List[ADSBComparisonItem] = []
    disclaimer: str = Field(
        default="This comparison is for informational purposes only. "
                "All airworthiness decisions must be made by a licensed AME/TEA. "
                "AeroLogix AI does not determine compliance status."
    )


# ============================================================
# INDEX DEFINITIONS
# ============================================================

TC_AD_INDEXES = [
    {"keys": [("ref", 1)], "unique": True, "name": "ref_unique"},
    {"keys": [("designator", 1)], "name": "designator_idx"},
    {"keys": [("manufacturer", 1)], "name": "manufacturer_idx"},
    {"keys": [("effective_date", -1)], "name": "effective_date_idx"},
    {"keys": [("is_active", 1)], "name": "is_active_idx"},
]

TC_SB_INDEXES = [
    {"keys": [("ref", 1)], "unique": True, "name": "ref_unique"},
    {"keys": [("designator", 1)], "name": "designator_idx"},
    {"keys": [("manufacturer", 1)], "name": "manufacturer_idx"},
    {"keys": [("related_ad", 1)], "name": "related_ad_idx"},
    {"keys": [("is_active", 1)], "name": "is_active_idx"},
]
