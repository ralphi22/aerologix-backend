from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime
from enum import Enum

class ADSBType(str, Enum):
    AD = "AD"  # Airworthiness Directive
    SB = "SB"  # Service Bulletin

class ADSBStatus(str, Enum):
    COMPLIED = "COMPLIED"
    PENDING = "PENDING"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    UNKNOWN = "UNKNOWN"

class ADSBRecordBase(BaseModel):
    aircraft_id: str
    adsb_type: ADSBType
    reference_number: str  # e.g., "AD 2024-05-12" or "SB 72-0034"
    
    # Details
    title: Optional[str] = None
    description: Optional[str] = None
    effective_date: Optional[datetime] = None
    
    # Compliance status
    status: ADSBStatus = ADSBStatus.UNKNOWN
    compliance_date: Optional[datetime] = None
    
    # Hours at compliance
    compliance_airframe_hours: Optional[float] = None
    compliance_engine_hours: Optional[float] = None
    compliance_propeller_hours: Optional[float] = None
    
    # Recurring
    is_recurring: bool = False
    recurring_interval_hours: Optional[float] = None
    recurring_interval_months: Optional[int] = None
    next_due_hours: Optional[float] = None
    next_due_date: Optional[datetime] = None
    
    # Documentation
    work_order_reference: Optional[str] = None
    ame_name: Optional[str] = None
    remarks: Optional[str] = None
    
    # Source
    source: str = "manual"  # manual, ocr
    ocr_scan_id: Optional[str] = None

class ADSBRecordCreate(ADSBRecordBase):
    pass

class ADSBRecordUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    effective_date: Optional[datetime] = None
    status: Optional[ADSBStatus] = None
    compliance_date: Optional[datetime] = None
    compliance_airframe_hours: Optional[float] = None
    compliance_engine_hours: Optional[float] = None
    compliance_propeller_hours: Optional[float] = None
    is_recurring: Optional[bool] = None
    recurring_interval_hours: Optional[float] = None
    recurring_interval_months: Optional[int] = None
    next_due_hours: Optional[float] = None
    next_due_date: Optional[datetime] = None
    work_order_reference: Optional[str] = None
    ame_name: Optional[str] = None
    remarks: Optional[str] = None

class ADSBRecord(ADSBRecordBase):
    id: str = Field(alias="_id")
    user_id: str
    created_at: datetime
    updated_at: datetime
    
    class Config:
        populate_by_name = True
