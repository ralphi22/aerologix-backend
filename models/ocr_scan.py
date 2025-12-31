from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from datetime import datetime
from enum import Enum

class DocumentType(str, Enum):
    MAINTENANCE_REPORT = "maintenance_report"
    STC = "stc"
    INVOICE = "invoice"
    LOGBOOK = "logbook"
    OTHER = "other"

class OCRStatus(str, Enum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    APPLIED = "APPLIED"  # Results applied to system

class ExtractedADSB(BaseModel):
    """AD/SB detected in OCR scan"""
    adsb_type: str  # AD or SB
    reference_number: str
    status: str = "UNKNOWN"  # COMPLIED, PENDING, UNKNOWN
    compliance_date: Optional[str] = None
    airframe_hours: Optional[float] = None
    engine_hours: Optional[float] = None
    propeller_hours: Optional[float] = None
    description: Optional[str] = None

class ExtractedPart(BaseModel):
    """Part detected in OCR scan"""
    part_number: str
    name: Optional[str] = None
    serial_number: Optional[str] = None
    quantity: int = 1
    price: Optional[float] = None
    supplier: Optional[str] = None

class ExtractedSTC(BaseModel):
    """STC detected in OCR scan"""
    stc_number: str
    title: Optional[str] = None
    description: Optional[str] = None
    installation_date: Optional[str] = None

class ExtractedELTData(BaseModel):
    """ELT data extracted from OCR"""
    detected: bool = False
    brand: Optional[str] = None
    model: Optional[str] = None
    serial_number: Optional[str] = None
    installation_date: Optional[str] = None
    certification_date: Optional[str] = None
    battery_expiry_date: Optional[str] = None
    battery_install_date: Optional[str] = None
    battery_interval_months: Optional[int] = None
    beacon_hex_id: Optional[str] = None


class ExtractedMaintenanceData(BaseModel):
    """Structured data extracted from maintenance report"""
    date: Optional[str] = None
    ame_name: Optional[str] = None
    amo_name: Optional[str] = None
    ame_license: Optional[str] = None
    work_order_number: Optional[str] = None
    description: Optional[str] = None
    airframe_hours: Optional[float] = None
    engine_hours: Optional[float] = None
    propeller_hours: Optional[float] = None
    remarks: Optional[str] = None
    labor_cost: Optional[float] = None
    parts_cost: Optional[float] = None
    total_cost: Optional[float] = None
    
    # Detected items
    ad_sb_references: List[ExtractedADSB] = []
    parts_replaced: List[ExtractedPart] = []
    stc_references: List[ExtractedSTC] = []
    
    # ELT data
    elt_data: Optional[ExtractedELTData] = None

class OCRScanBase(BaseModel):
    aircraft_id: str
    document_type: DocumentType
    
    # Raw data
    raw_text: Optional[str] = None
    
    # Extracted structured data
    extracted_data: Optional[ExtractedMaintenanceData] = None
    
    # Processing status
    status: OCRStatus = OCRStatus.PENDING
    error_message: Optional[str] = None
    
    # Applied records IDs
    applied_maintenance_id: Optional[str] = None
    applied_adsb_ids: List[str] = []
    applied_part_ids: List[str] = []
    applied_stc_ids: List[str] = []

class OCRScanCreate(BaseModel):
    aircraft_id: str
    document_type: DocumentType
    image_base64: str

class OCRScan(OCRScanBase):
    id: str = Field(alias="_id")
    user_id: str
    created_at: datetime
    updated_at: datetime
    
    class Config:
        populate_by_name = True

class OCRScanResponse(BaseModel):
    """Response for OCR scan endpoint"""
    id: str
    status: OCRStatus
    document_type: DocumentType
    raw_text: Optional[str] = None
    extracted_data: Optional[ExtractedMaintenanceData] = None
    error_message: Optional[str] = None
    created_at: datetime


# ============== DEDUPLICATION MODELS ==============

class MatchType(str, Enum):
    """Type of match found during deduplication"""
    EXACT = "exact"      # All key fields match
    PARTIAL = "partial"  # Some key fields match
    NONE = "none"        # No match

class DuplicateMatch(BaseModel):
    """A potential duplicate match"""
    index: int                          # Index in extracted array
    extracted: Dict[str, Any]           # Extracted data from OCR
    existing: Optional[Dict[str, Any]]  # Existing record if found
    existing_id: Optional[str] = None   # ID of existing record
    match_type: MatchType               # Type of match

class DuplicateCheckResponse(BaseModel):
    """Response for duplicate check endpoint"""
    scan_id: str
    duplicates: Dict[str, List[DuplicateMatch]]  # ad_sb, parts, invoices, stc
    new_items: Dict[str, List[Dict[str, Any]]]   # Items with no matches
    summary: Dict[str, Dict[str, int]]           # Count summary per type

class ItemAction(str, Enum):
    """Action to take for an item"""
    CREATE = "create"  # Create new record
    LINK = "link"      # Link to existing record (update)
    SKIP = "skip"      # Do nothing

class ItemSelection(BaseModel):
    """User selection for a single item"""
    index: int
    action: ItemAction
    existing_id: Optional[str] = None  # Required if action is LINK

class ApplySelections(BaseModel):
    """User selections for applying OCR results"""
    ad_sb: Optional[List[ItemSelection]] = None
    parts: Optional[List[ItemSelection]] = None
    invoices: Optional[List[ItemSelection]] = None
    stc: Optional[List[ItemSelection]] = None
