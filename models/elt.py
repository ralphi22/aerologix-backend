"""ELT (Emergency Locator Transmitter) Models for AeroLogix AI"""

from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime, date
from enum import Enum


class ELTStatus(str, Enum):
    """ELT operational status"""
    ACTIVE = "active"
    EXPIRED = "expired"
    PENDING_INSPECTION = "pending_inspection"
    INACTIVE = "inactive"


class ELTAlertLevel(str, Enum):
    """Alert level for ELT maintenance"""
    OK = "ok"
    WARNING = "warning"  # 15 days before expiry
    CRITICAL = "critical"  # Expired


class ELTBase(BaseModel):
    """Base ELT model"""
    aircraft_id: str
    
    # ELT Information
    brand: Optional[str] = None  # Artex, Kannad, ACK, etc.
    model: Optional[str] = None
    serial_number: Optional[str] = None
    
    # Dates
    installation_date: Optional[datetime] = None
    certification_date: Optional[datetime] = None
    last_test_date: Optional[datetime] = None
    battery_expiry_date: Optional[datetime] = None
    battery_install_date: Optional[datetime] = None
    battery_interval_months: Optional[int] = None  # Usually 24, 48 or 60 months
    
    # Additional info
    beacon_hex_id: Optional[str] = None  # 15-character hex ID
    registration_number: Optional[str] = None
    remarks: Optional[str] = None
    
    # Source tracking
    source: Optional[str] = None  # 'manual', 'ocr'
    ocr_scan_id: Optional[str] = None


class ELTCreate(BaseModel):
    """Model for creating ELT record"""
    aircraft_id: str
    brand: Optional[str] = None
    model: Optional[str] = None
    serial_number: Optional[str] = None
    installation_date: Optional[str] = None  # Accept string, convert in route
    certification_date: Optional[str] = None
    last_test_date: Optional[str] = None
    battery_expiry_date: Optional[str] = None
    battery_install_date: Optional[str] = None
    battery_interval_months: Optional[int] = None
    beacon_hex_id: Optional[str] = None
    registration_number: Optional[str] = None
    remarks: Optional[str] = None
    source: str = "manual"
    ocr_scan_id: Optional[str] = None


class ELTUpdate(BaseModel):
    """Model for updating ELT record"""
    brand: Optional[str] = None
    model: Optional[str] = None
    serial_number: Optional[str] = None
    installation_date: Optional[str] = None  # Accept string, convert in route
    certification_date: Optional[str] = None
    last_test_date: Optional[str] = None
    battery_expiry_date: Optional[str] = None
    battery_install_date: Optional[str] = None
    battery_interval_months: Optional[int] = None
    beacon_hex_id: Optional[str] = None
    registration_number: Optional[str] = None
    remarks: Optional[str] = None


class ELTAlert(BaseModel):
    """ELT alert model"""
    type: str  # 'test', 'battery'
    level: ELTAlertLevel
    message: str
    due_date: Optional[datetime] = None
    days_remaining: Optional[int] = None


class ELTResponse(ELTBase):
    """ELT response with computed alerts"""
    id: str = Field(alias="_id")
    user_id: str
    status: ELTStatus = ELTStatus.ACTIVE
    alerts: List[ELTAlert] = []
    created_at: datetime
    updated_at: datetime
    
    class Config:
        populate_by_name = True


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
