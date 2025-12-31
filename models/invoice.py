"""Invoice Models for AeroLogix AI"""

from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime


class InvoicePart(BaseModel):
    """Part extracted from invoice"""
    part_number: str
    name: Optional[str] = None
    serial_number: Optional[str] = None
    quantity: int = 1
    unit_price: Optional[float] = None
    total_price: Optional[float] = None
    manufacturer: Optional[str] = None


class InvoiceCreate(BaseModel):
    """Model for creating invoice record"""
    aircraft_id: str
    invoice_number: Optional[str] = None
    invoice_date: Optional[str] = None
    supplier: Optional[str] = None
    parts: List[InvoicePart] = []
    subtotal: Optional[float] = None
    tax: Optional[float] = None
    total: Optional[float] = None
    currency: str = "CAD"
    source: str = "ocr"  # 'ocr' or 'manual'
    ocr_scan_id: Optional[str] = None
    remarks: Optional[str] = None


class InvoiceResponse(BaseModel):
    """Invoice response model"""
    id: str = Field(alias="_id")
    user_id: str
    aircraft_id: str
    invoice_number: Optional[str] = None
    invoice_date: Optional[datetime] = None
    supplier: Optional[str] = None
    parts: List[InvoicePart] = []
    subtotal: Optional[float] = None
    tax: Optional[float] = None
    total: Optional[float] = None
    currency: str = "CAD"
    source: str = "ocr"
    ocr_scan_id: Optional[str] = None
    remarks: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    
    class Config:
        populate_by_name = True
