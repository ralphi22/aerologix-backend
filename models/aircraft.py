from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime

class AircraftBase(BaseModel):
    registration: str  # Format: C-GABC (toujours en MAJUSCULES)
    aircraft_type: Optional[str] = None
    manufacturer: Optional[str] = None
    model: Optional[str] = None
    year: Optional[int] = None
    serial_number: Optional[str] = None
    
    # Heures
    airframe_hours: float = 0.0  # Heures cellule
    engine_hours: float = 0.0    # Heures moteur
    propeller_hours: float = 0.0 # Heures hélice
    
    # Photo
    photo_url: Optional[str] = None  # Base64 ou URL
    
    # Description optionnelle
    description: Optional[str] = None

class AircraftCreate(AircraftBase):
    pass

class AircraftUpdate(BaseModel):
    registration: Optional[str] = None
    aircraft_type: Optional[str] = None
    manufacturer: Optional[str] = None
    model: Optional[str] = None
    year: Optional[int] = None
    serial_number: Optional[str] = None
    airframe_hours: Optional[float] = None
    engine_hours: Optional[float] = None
    propeller_hours: Optional[float] = None
    photo_url: Optional[str] = None
    description: Optional[str] = None

class Aircraft(AircraftBase):
    id: str = Field(alias="_id")
    user_id: str
    created_at: datetime
    updated_at: datetime
    
    class Config:
        populate_by_name = True
