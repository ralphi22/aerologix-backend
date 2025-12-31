"""
Component Settings Model for AeroLogix AI
Stores maintenance work timestamps (hours/dates at last work)
Compliant with Transport Canada RAC 605 / Standard 625
ALL VALUES ARE INFORMATIONAL ONLY - NO AIRWORTHINESS DETERMINATION
"""

from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime

class ComponentSettingsCreate(BaseModel):
    """Create/Update component settings - stores hours AT LAST WORK"""
    # ENGINE
    engine_model: Optional[str] = None
    engine_tbo_hours: Optional[float] = 2000.0  # TBO limit
    engine_last_overhaul_hours: Optional[float] = None  # Engine hours when overhaul was done
    engine_last_overhaul_date: Optional[str] = None
    
    # PROPELLER
    propeller_type: Optional[str] = "fixed"  # fixed or variable
    propeller_model: Optional[str] = None
    propeller_manufacturer_interval_years: Optional[float] = None
    propeller_last_inspection_hours: Optional[float] = None  # Prop hours when inspected
    propeller_last_inspection_date: Optional[str] = None
    
    # AVIONICS (24 months - date only)
    avionics_last_certification_date: Optional[str] = None
    avionics_certification_interval_months: Optional[int] = 24
    
    # MAGNETOS
    magnetos_model: Optional[str] = None
    magnetos_interval_hours: Optional[float] = 500.0
    magnetos_last_inspection_hours: Optional[float] = None  # Engine hours when inspected
    magnetos_last_inspection_date: Optional[str] = None
    
    # VACUUM PUMP
    vacuum_pump_model: Optional[str] = None
    vacuum_pump_interval_hours: Optional[float] = 400.0
    vacuum_pump_last_replacement_hours: Optional[float] = None  # Engine hours when replaced
    vacuum_pump_last_replacement_date: Optional[str] = None
    
    # AIRFRAME
    airframe_last_annual_date: Optional[str] = None
    airframe_last_annual_hours: Optional[float] = None  # Airframe hours at annual
    
    # ELT (intervals stored here, dates come from ELT module)
    elt_test_interval_months: Optional[int] = 12
    elt_battery_interval_months: Optional[int] = 24

class ComponentSettingsUpdate(ComponentSettingsCreate):
    """Update - same as create, all optional"""
    pass

CANADIAN_REGULATIONS = {
    "propeller_fixed_max_years": 5,
    "propeller_variable_fallback_years": 10,
    "avionics_certification_months": 24,
    "magnetos_default_hours": 500,
    "vacuum_pump_default_hours": 400,
    "engine_default_tbo": 2000,
}
