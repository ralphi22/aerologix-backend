from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime

class PartRecordBase(BaseModel):
    aircraft_id: Optional[str] = None  # Can be null for inventory parts
    
    # Part identification
    part_number: str  # P/N
    serial_number: Optional[str] = None  # S/N
    name: str
    description: Optional[str] = None
    
    # Manufacturer
    manufacturer: Optional[str] = None
    
    # Purchase info
    supplier: Optional[str] = None
    purchase_date: Optional[datetime] = None
    purchase_price: Optional[float] = None
    currency: str = "USD"
    quantity: int = 1
    
    # Installation
    installation_date: Optional[datetime] = None
    installation_airframe_hours: Optional[float] = None
    installed_on_aircraft: bool = False
    
    # Life limits
    life_limit_hours: Optional[float] = None
    life_limit_cycles: Optional[int] = None
    life_limit_date: Optional[datetime] = None
    
    # Traceability
    invoice_number: Optional[str] = None
    work_order_reference: Optional[str] = None
    remarks: Optional[str] = None
    
    # Source
    source: str = "manual"  # manual, ocr
    ocr_scan_id: Optional[str] = None
    confirmed: bool = True  # OCR parts start as False, manual as True

class PartRecordCreate(PartRecordBase):
    pass

class PartRecordUpdate(BaseModel):
    part_number: Optional[str] = None
    serial_number: Optional[str] = None
    name: Optional[str] = None
    description: Optional[str] = None
    manufacturer: Optional[str] = None
    supplier: Optional[str] = None
    purchase_date: Optional[datetime] = None
    purchase_price: Optional[float] = None
    currency: Optional[str] = None
    quantity: Optional[int] = None
    installation_date: Optional[datetime] = None
    installation_airframe_hours: Optional[float] = None
    installed_on_aircraft: Optional[bool] = None
    life_limit_hours: Optional[float] = None
    life_limit_cycles: Optional[int] = None
    life_limit_date: Optional[datetime] = None
    invoice_number: Optional[str] = None
    work_order_reference: Optional[str] = None
    remarks: Optional[str] = None
    confirmed: Optional[bool] = None  # For confirming OCR parts

class PartRecord(PartRecordBase):
    id: str = Field(alias="_id")
    user_id: str
    created_at: datetime
    updated_at: datetime
    
    class Config:
        populate_by_name = True
