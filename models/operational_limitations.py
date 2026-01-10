"""
Operational Limitations Model

Stores TEA operational limitations detected from OCR reports.
These are raw text extractions - NOT calculated statuses.

Collection: operational_limitations

RULES:
- Always store raw limitation text as-is
- Never transform to status or compliance
- Never deduce rules from text
- Always reference the source report
"""

from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime
from enum import Enum


class LimitationCategory(str, Enum):
    """Categories of operational limitations"""
    ELT = "ELT"
    AVIONICS = "AVIONICS"
    PROPELLER = "PROPELLER"
    ENGINE = "ENGINE"
    AIRFRAME = "AIRFRAME"
    GENERAL = "GENERAL"


class OperationalLimitationBase(BaseModel):
    """Base model for operational limitation"""
    aircraft_id: str = Field(..., description="Aircraft ID")
    report_id: str = Field(..., description="Source OCR scan ID")
    limitation_text: str = Field(..., description="Raw limitation text as written by TEA")
    detected_keywords: List[str] = Field(default_factory=list, description="Keywords that triggered detection")
    category: LimitationCategory = Field(default=LimitationCategory.GENERAL, description="Limitation category")
    confidence: float = Field(default=0.5, ge=0.0, le=1.0, description="Detection confidence")
    source: str = Field(default="OCR", description="Source of detection")


class OperationalLimitationCreate(OperationalLimitationBase):
    """Model for creating an operational limitation"""
    pass


class OperationalLimitation(OperationalLimitationBase):
    """Full operational limitation document"""
    id: str = Field(alias="_id")
    user_id: str
    report_date: Optional[datetime] = Field(None, description="Date of the source report")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    
    class Config:
        populate_by_name = True


class OperationalLimitationResponse(BaseModel):
    """Response model for a single limitation"""
    id: str
    limitation_text: str
    detected_keywords: List[str]
    category: LimitationCategory
    confidence: float
    report_id: str
    report_date: Optional[str] = None
    created_at: str


class AircraftLimitationsResponse(BaseModel):
    """Response for all limitations of an aircraft"""
    aircraft_id: str
    registration: Optional[str] = None
    limitations: List[OperationalLimitationResponse]
    total_count: int
    categories: dict  # Count per category


# ============================================================
# INDEX DEFINITION
# ============================================================

OPERATIONAL_LIMITATIONS_INDEXES = [
    {
        "keys": [
            ("aircraft_id", 1),
            ("report_id", 1),
            ("limitation_text", 1)
        ],
        "unique": True,
        "name": "aircraft_report_limitation_unique"
    },
    {
        "keys": [("aircraft_id", 1)],
        "name": "aircraft_id_idx"
    },
    {
        "keys": [("category", 1)],
        "name": "category_idx"
    },
    {
        "keys": [("report_id", 1)],
        "name": "report_id_idx"
    },
    {
        "keys": [("created_at", -1)],
        "name": "created_at_desc_idx"
    },
]
