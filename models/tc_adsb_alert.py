"""
TC AD/SB Alert Models

Models for the monthly TC AD/SB detection and alert system.

INFORMATIONAL ONLY:
- Flag means "new TC publication exists"
- NOT missing, NOT overdue, NOT non-compliant
- TC-Safe and auditable

Collections:
- Aircraft extension fields (adsb_has_new_tc_items, etc.)
- tc_adsb_audit_log for audit trail
"""

from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime
from enum import Enum


# ============================================================
# ENUMS
# ============================================================

class AuditEventType(str, Enum):
    """Types of audit events for TC AD/SB detection"""
    DETECTION_STARTED = "DETECTION_STARTED"
    DETECTION_COMPLETED = "DETECTION_COMPLETED"
    NEW_ITEMS_FOUND = "NEW_ITEMS_FOUND"
    NO_NEW_ITEMS = "NO_NEW_ITEMS"
    ALERT_CLEARED = "ALERT_CLEARED"
    DETECTION_SKIPPED = "DETECTION_SKIPPED"
    DETECTION_ERROR = "DETECTION_ERROR"


# ============================================================
# AIRCRAFT EXTENSION FIELDS
# ============================================================

class AircraftADSBAlertFields(BaseModel):
    """
    Fields to extend Aircraft model for AD/SB alerts.
    
    These fields track TC AD/SB alert state per aircraft.
    """
    # Alert flag - true if new TC items exist since last review
    adsb_has_new_tc_items: bool = Field(
        default=False,
        description="True if new TC AD/SB items detected since last review"
    )
    
    # Last TC version used for detection
    last_tc_adsb_version: Optional[str] = Field(
        default=None,
        description="TC AD/SB data version used for last detection (e.g., '2026-06')"
    )
    
    # Count of new items detected
    count_new_adsb: int = Field(
        default=0,
        description="Number of new TC AD/SB items since last review"
    )
    
    # Last time user reviewed the AD/SB module
    last_adsb_reviewed_at: Optional[datetime] = Field(
        default=None,
        description="Timestamp when user last reviewed AD/SB module"
    )
    
    # Previous list of known AD/SB identifiers (for comparison)
    known_tc_adsb_refs: List[str] = Field(
        default_factory=list,
        description="List of TC AD/SB refs known at last detection"
    )


# ============================================================
# AUDIT LOG MODEL
# ============================================================

class TCADSBAuditLogBase(BaseModel):
    """
    Audit log entry for TC AD/SB detection events.
    
    Required for traceability and TC-safety.
    """
    event_type: AuditEventType
    aircraft_id: Optional[str] = Field(
        None, 
        description="Aircraft ID if event is aircraft-specific"
    )
    registration: Optional[str] = Field(
        None,
        description="Aircraft registration for readability"
    )
    tc_adsb_version: Optional[str] = Field(
        None,
        description="TC AD/SB version used"
    )
    
    # Detection results
    new_items_count: int = Field(
        default=0,
        description="Number of new items detected"
    )
    new_items_refs: List[str] = Field(
        default_factory=list,
        description="References of new items (max 50 for storage)"
    )
    
    # Context
    triggered_by: str = Field(
        default="system",
        description="Who/what triggered the event: 'system', 'admin', 'user:{user_id}'"
    )
    notes: Optional[str] = Field(
        None,
        description="Additional context or error message"
    )


class TCADSBAuditLog(TCADSBAuditLogBase):
    """Full audit log document as stored in MongoDB"""
    id: str = Field(alias="_id")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    
    class Config:
        populate_by_name = True


# ============================================================
# DETECTION REQUEST/RESPONSE MODELS
# ============================================================

class DetectionTriggerRequest(BaseModel):
    """Request to trigger TC AD/SB detection"""
    tc_adsb_version: Optional[str] = Field(
        None,
        description="TC version to use (defaults to current)"
    )
    force_all: bool = Field(
        default=False,
        description="Force detection for all aircraft even if recently checked"
    )


class AircraftDetectionResult(BaseModel):
    """Detection result for a single aircraft"""
    aircraft_id: str
    registration: str
    designator: Optional[str] = None
    new_items_found: bool
    new_items_count: int
    new_items_refs: List[str] = []
    previous_version: Optional[str] = None
    current_version: str
    skipped: bool = False
    skip_reason: Optional[str] = None


class DetectionSummaryResponse(BaseModel):
    """Summary response for batch detection"""
    tc_adsb_version: str
    detection_timestamp: str
    total_aircraft_processed: int
    aircraft_with_new_items: int
    aircraft_skipped: int
    total_new_items_found: int
    results: List[AircraftDetectionResult]
    triggered_by: str


# ============================================================
# ALERT STATUS RESPONSE
# ============================================================

class AircraftADSBAlertStatus(BaseModel):
    """AD/SB alert status for an aircraft (API response)"""
    aircraft_id: str
    registration: str
    adsb_has_new_tc_items: bool
    count_new_adsb: int
    last_tc_adsb_version: Optional[str] = None
    last_adsb_reviewed_at: Optional[str] = None


class MarkReviewedResponse(BaseModel):
    """Response when marking AD/SB as reviewed"""
    aircraft_id: str
    registration: str
    alert_cleared: bool
    reviewed_at: str
    previous_new_items_count: int
    message: str


# ============================================================
# INDEX DEFINITIONS
# ============================================================

TC_ADSB_AUDIT_LOG_INDEXES = [
    {
        "keys": [("created_at", -1)],
        "name": "created_at_desc"
    },
    {
        "keys": [("aircraft_id", 1), ("created_at", -1)],
        "name": "aircraft_created_at"
    },
    {
        "keys": [("event_type", 1)],
        "name": "event_type_idx"
    },
    {
        "keys": [("tc_adsb_version", 1)],
        "name": "tc_version_idx"
    },
]
