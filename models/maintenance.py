from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime
from enum import Enum

class MaintenanceType(str, Enum):
    INSPECTION = "INSPECTION"
    REPAIR = "REPAIR"
    OVERHAUL = "OVERHAUL"
    MODIFICATION = "MODIFICATION"
    AD_COMPLIANCE = "AD_COMPLIANCE"
    SB_COMPLIANCE = "SB_COMPLIANCE"
    ROUTINE = "ROUTINE"
    UNSCHEDULED = "UNSCHEDULED"

class MaintenanceRecordBase(BaseModel):
    aircraft_id: str
    maintenance_type: MaintenanceType = MaintenanceType.ROUTINE
    date: datetime
    description: str
    
    # AME/AMO Information
    ame_name: Optional[str] = None  # Aircraft Maintenance Engineer
    amo_name: Optional[str] = None  # Approved Maintenance Organization
    ame_license: Optional[str] = None
    
    # Work Order
    work_order_number: Optional[str] = None
    
    # Hours at time of maintenance
    airframe_hours: Optional[float] = None
    engine_hours: Optional[float] = None
    propeller_hours: Optional[float] = None
    
    # Parts replaced
    parts_replaced: List[str] = []
    
    # Regulatory references
    regulatory_references: List[str] = []  # AD/SB numbers
    
    # Notes
    remarks: Optional[str] = None
    
    # Cost
    labor_cost: Optional[float] = None
    parts_cost: Optional[float] = None
    total_cost: Optional[float] = None
    
    # Source
    source: str = "manual"  # manual, ocr
    ocr_scan_id: Optional[str] = None

class MaintenanceRecordCreate(MaintenanceRecordBase):
    pass

class MaintenanceRecordUpdate(BaseModel):
    maintenance_type: Optional[MaintenanceType] = None
    date: Optional[datetime] = None
    description: Optional[str] = None
    ame_name: Optional[str] = None
    amo_name: Optional[str] = None
    ame_license: Optional[str] = None
    work_order_number: Optional[str] = None
    airframe_hours: Optional[float] = None
    engine_hours: Optional[float] = None
    propeller_hours: Optional[float] = None
    parts_replaced: Optional[List[str]] = None
    regulatory_references: Optional[List[str]] = None
    remarks: Optional[str] = None
    labor_cost: Optional[float] = None
    parts_cost: Optional[float] = None
    total_cost: Optional[float] = None

class MaintenanceRecord(MaintenanceRecordBase):
    id: str = Field(alias="_id")
    user_id: str
    created_at: datetime
    updated_at: datetime
    
    class Config:
        populate_by_name = True
