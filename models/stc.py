from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime

class STCRecordBase(BaseModel):
    aircraft_id: str
    stc_number: str  # e.g., "SA02345NY"
    
    # Details
    title: Optional[str] = None
    description: Optional[str] = None
    holder: Optional[str] = None  # STC holder company
    
    # Applicability
    applicable_models: List[str] = []
    
    # Installation
    installation_date: Optional[datetime] = None
    installation_airframe_hours: Optional[float] = None
    installed_by: Optional[str] = None  # AME/AMO
    
    # Documentation
    work_order_reference: Optional[str] = None
    remarks: Optional[str] = None
    
    # Source
    source: str = "manual"  # manual, ocr
    ocr_scan_id: Optional[str] = None

class STCRecordCreate(STCRecordBase):
    pass

class STCRecordUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    holder: Optional[str] = None
    applicable_models: Optional[List[str]] = None
    installation_date: Optional[datetime] = None
    installation_airframe_hours: Optional[float] = None
    installed_by: Optional[str] = None
    work_order_reference: Optional[str] = None
    remarks: Optional[str] = None

class STCRecord(STCRecordBase):
    id: str = Field(alias="_id")
    user_id: str
    created_at: datetime
    updated_at: datetime
    
    class Config:
        populate_by_name = True
