"""
Installed Components Model

Tracks critical components installed on aircraft, extracted from OCR reports.

Collection: installed_components
"""

from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime
from enum import Enum


class ComponentType(str, Enum):
    """Types of critical aircraft components"""
    ENGINE = "ENGINE"
    PROP = "PROP"
    MAGNETO = "MAGNETO"
    VACUUM_PUMP = "VACUUM_PUMP"
    LLP = "LLP"  # Life Limited Part
    STARTER = "STARTER"
    ALTERNATOR = "ALTERNATOR"
    TURBO = "TURBO"
    FUEL_PUMP = "FUEL_PUMP"
    OIL_PUMP = "OIL_PUMP"


# Default TBO values by component type (hours)
# These are typical values - actual TBO depends on specific part
DEFAULT_TBO = {
    ComponentType.ENGINE: 2000,
    ComponentType.PROP: 2400,
    ComponentType.MAGNETO: 500,
    ComponentType.VACUUM_PUMP: 500,
    ComponentType.STARTER: 1000,
    ComponentType.ALTERNATOR: 1000,
    ComponentType.TURBO: 1800,
    ComponentType.FUEL_PUMP: 500,
    ComponentType.OIL_PUMP: None,  # Usually on-condition
    ComponentType.LLP: None,  # Varies by part
}


class InstalledComponentBase(BaseModel):
    """Base model for installed component"""
    aircraft_id: str = Field(..., description="Aircraft ID")
    component_type: ComponentType = Field(..., description="Type of component")
    part_no: str = Field(default="UNKNOWN", description="Part number")
    serial_no: Optional[str] = Field(None, description="Serial number if available")
    description: Optional[str] = Field(None, description="Component description")
    installed_at_hours: float = Field(..., description="Airframe hours at installation")
    installed_date: Optional[datetime] = Field(None, description="Installation date")
    tbo: Optional[int] = Field(None, description="Time Between Overhaul (hours)")
    source_report_id: Optional[str] = Field(None, description="OCR scan ID that detected this")
    confidence: float = Field(default=0.5, ge=0.0, le=1.0, description="Detection confidence")


class InstalledComponentCreate(InstalledComponentBase):
    """Model for creating an installed component"""
    pass


class InstalledComponent(InstalledComponentBase):
    """Full installed component document"""
    id: str = Field(alias="_id")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    
    class Config:
        populate_by_name = True


class CriticalComponentResponse(BaseModel):
    """Response model for critical component with time calculations"""
    component_type: ComponentType
    part_no: str
    serial_no: Optional[str] = None
    description: Optional[str] = None
    installed_at_hours: float
    installed_date: Optional[str] = None
    current_airframe_hours: float
    time_since_install: float
    tbo: Optional[int] = None
    remaining: Optional[float] = None
    status: str = "OK"  # OK, WARNING, CRITICAL, UNKNOWN
    confidence: float = 0.5


class CriticalComponentsResponse(BaseModel):
    """Response for all critical components of an aircraft"""
    aircraft_id: str
    registration: Optional[str] = None
    current_airframe_hours: float
    components: List[CriticalComponentResponse]
    last_updated: Optional[datetime] = None


# ============================================================
# INDEX DEFINITION
# ============================================================

INSTALLED_COMPONENTS_INDEXES = [
    {
        "keys": [
            ("aircraft_id", 1),
            ("component_type", 1),
            ("part_no", 1),
            ("installed_at_hours", 1)
        ],
        "unique": True,
        "name": "aircraft_component_unique"
    },
    {
        "keys": [("aircraft_id", 1)],
        "name": "aircraft_id_idx"
    },
    {
        "keys": [("component_type", 1)],
        "name": "component_type_idx"
    },
    {
        "keys": [("source_report_id", 1)],
        "name": "source_report_idx"
    },
]
