"""
Component Settings Routes for AeroLogix AI
Manages aircraft component maintenance intervals
Transport Canada RAC 605 / Standard 625 compliant (INFORMATIONAL ONLY)

Also provides Critical Components API for tracking component lifecycle.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from datetime import datetime
from typing import List, Optional
import logging

from models.component_settings import (
    ComponentSettingsCreate,
    ComponentSettingsUpdate,
    CANADIAN_REGULATIONS
)
from models.installed_components import (
    ComponentType,
    DEFAULT_TBO,
    CriticalComponentResponse,
    CriticalComponentsResponse,
)
from models.user import User
from services.auth_deps import get_current_user
from database.mongodb import get_database

router = APIRouter(prefix="/api/components", tags=["components"])
logger = logging.getLogger(__name__)

# DEFAULT_SETTINGS - Industry standard default values
# These provide sensible starting points for common aircraft
DEFAULT_SETTINGS = {
    "engine_model": None,
    "engine_tbo_hours": 2000.0,  # Typical TBO for Lycoming/Continental
    "engine_last_overhaul_hours": None,
    "engine_last_overhaul_date": None,
    "propeller_type": "fixed",  # Most common for light aircraft
    "propeller_model": None,
    "propeller_manufacturer_interval_years": None,
    "propeller_last_inspection_hours": None,
    "propeller_last_inspection_date": None,
    "avionics_last_certification_date": None,
    "avionics_certification_interval_months": 24,  # Canada requirement
    "magnetos_model": None,
    "magnetos_interval_hours": 500.0,  # Typical interval
    "magnetos_last_inspection_hours": None,
    "magnetos_last_inspection_date": None,
    "vacuum_pump_model": None,
    "vacuum_pump_interval_hours": 400.0,  # Typical interval
    "vacuum_pump_last_replacement_hours": None,
    "vacuum_pump_last_replacement_date": None,
    "airframe_last_annual_date": None,
    "airframe_last_annual_hours": None,
}

@router.get("/aircraft/{aircraft_id}")
async def get_component_settings(
    aircraft_id: str,
    current_user: User = Depends(get_current_user),
    db = Depends(get_database)
):
    """Get component settings for an aircraft"""
    logger.info(f"Getting components for aircraft_id={aircraft_id}, user_id={current_user.id}")
    
    # Verify aircraft belongs to user
    aircraft = await db.aircrafts.find_one({
        "_id": aircraft_id,
        "user_id": current_user.id
    })
    
    if not aircraft:
        logger.warning(f"Aircraft {aircraft_id} not found for user {current_user.id}")
        # Try without user_id filter to debug
        any_aircraft = await db.aircrafts.find_one({"_id": aircraft_id})
        if any_aircraft:
            logger.warning(f"Aircraft exists but belongs to user {any_aircraft.get('user_id')}")
        raise HTTPException(status_code=404, detail="Aircraft not found")
    
    settings = await db.component_settings.find_one({
        "aircraft_id": aircraft_id,
        "user_id": current_user.id
    })
    
    if not settings:
        # Return defaults
        logger.info(f"No settings found, returning defaults for {aircraft_id}")
        return {
            "aircraft_id": aircraft_id,
            **DEFAULT_SETTINGS,
            "regulations": CANADIAN_REGULATIONS
        }
    
    # Merge with defaults for any missing fields
    result = {"aircraft_id": aircraft_id}
    for key, default_value in DEFAULT_SETTINGS.items():
        result[key] = settings.get(key, default_value)
    result["regulations"] = CANADIAN_REGULATIONS
    
    if "_id" in settings:
        result["_id"] = str(settings["_id"])
    
    return result

@router.post("/aircraft/{aircraft_id}")
async def create_component_settings(
    aircraft_id: str,
    data: ComponentSettingsCreate,
    current_user: User = Depends(get_current_user),
    db = Depends(get_database)
):
    """Create or update component settings"""
    # Verify aircraft belongs to user
    aircraft = await db.aircrafts.find_one({
        "_id": aircraft_id,
        "user_id": current_user.id
    })
    if not aircraft:
        raise HTTPException(status_code=404, detail="Aircraft not found")
    
    now = datetime.utcnow()
    settings_doc = {
        "aircraft_id": aircraft_id,
        "user_id": current_user.id,
        **data.model_dump(exclude_none=True),
        "updated_at": now
    }
    
    # Upsert
    result = await db.component_settings.update_one(
        {"aircraft_id": aircraft_id, "user_id": current_user.id},
        {"$set": settings_doc, "$setOnInsert": {"created_at": now}},
        upsert=True
    )
    
    logger.info(f"Component settings saved for aircraft {aircraft_id}")
    return {"message": "Settings saved", "aircraft_id": aircraft_id}

@router.put("/aircraft/{aircraft_id}")
async def update_component_settings(
    aircraft_id: str,
    data: ComponentSettingsUpdate,
    current_user: User = Depends(get_current_user),
    db = Depends(get_database)
):
    """Update component settings"""
    # Verify aircraft belongs to user
    aircraft = await db.aircrafts.find_one({
        "_id": aircraft_id,
        "user_id": current_user.id
    })
    if not aircraft:
        raise HTTPException(status_code=404, detail="Aircraft not found")
    
    # Only update non-None values
    update_data = {k: v for k, v in data.model_dump().items() if v is not None}
    update_data["updated_at"] = datetime.utcnow()
    
    result = await db.component_settings.update_one(
        {"aircraft_id": aircraft_id, "user_id": current_user.id},
        {"$set": update_data, "$setOnInsert": {"created_at": datetime.utcnow()}},
        upsert=True
    )
    
    logger.info(f"Component settings updated for aircraft {aircraft_id}")
    return {"message": "Settings updated", "aircraft_id": aircraft_id}

@router.get("/regulations")
async def get_regulations():
    """Get Canadian regulations reference values (INFORMATIONAL ONLY)"""
    return {
        "disclaimer": "Ces valeurs sont INFORMATIVES uniquement. Consultez toujours les documents officiels et un AME certifié.",
        "regulations": CANADIAN_REGULATIONS,
        "sources": [
            "Transport Canada RAC 605",
            "Transport Canada Standard 625"
        ]
    }


# ============================================================
# CRITICAL COMPONENTS API - OCR-detected component lifecycle
# ============================================================

@router.get("/critical/{aircraft_id}", response_model=CriticalComponentsResponse)
async def get_critical_components(
    aircraft_id: str,
    current_user: User = Depends(get_current_user),
    db = Depends(get_database)
):
    """
    Get critical components for an aircraft with time-since-install calculations.
    
    Returns installed components detected from OCR scans with:
    - installed_at_hours: When the component was installed
    - current_airframe_hours: Current aircraft hours
    - time_since_install: Hours since installation
    - tbo: Time Between Overhaul (if known)
    - remaining: Hours remaining until TBO
    - status: OK, WARNING (< 100h remaining), CRITICAL (< 50h), or UNKNOWN
    
    INFORMATIONAL ONLY - Always verify with AME and official records.
    """
    logger.info(f"Getting critical components for aircraft_id={aircraft_id}, user_id={current_user.id}")
    
    # Verify aircraft belongs to user
    aircraft = await db.aircrafts.find_one({
        "_id": aircraft_id,
        "user_id": current_user.id
    })
    
    if not aircraft:
        logger.warning(f"Aircraft {aircraft_id} not found for user {current_user.id}")
        raise HTTPException(status_code=404, detail="Aircraft not found")
    
    # Get current airframe hours
    current_airframe_hours = aircraft.get("airframe_hours", 0.0)
    registration = aircraft.get("registration")
    
    # Fetch installed components from DB
    components_cursor = db.installed_components.find({
        "aircraft_id": aircraft_id,
        "user_id": current_user.id
    }).sort("installed_at_hours", -1)  # Most recent first
    
    components_list: List[CriticalComponentResponse] = []
    last_updated: Optional[datetime] = None
    
    async for comp in components_cursor:
        comp_type_str = comp.get("component_type", "")
        part_no = comp.get("part_no", "UNKNOWN")
        installed_at_hours = comp.get("installed_at_hours", 0.0)
        installed_date = comp.get("installed_date")
        tbo = comp.get("tbo")
        confidence = comp.get("confidence", 0.5)
        
        # Track last updated
        comp_updated = comp.get("updated_at") or comp.get("created_at")
        if comp_updated and (last_updated is None or comp_updated > last_updated):
            last_updated = comp_updated
        
        # Calculate time since install
        time_since_install = max(0.0, current_airframe_hours - installed_at_hours)
        
        # Calculate remaining hours until TBO
        remaining: Optional[float] = None
        comp_status = "UNKNOWN"
        
        if tbo is not None and tbo > 0:
            remaining = max(0.0, tbo - time_since_install)
            
            # Determine status based on remaining hours
            if remaining <= 0:
                comp_status = "OVERDUE"
            elif remaining < 50:
                comp_status = "CRITICAL"
            elif remaining < 100:
                comp_status = "WARNING"
            else:
                comp_status = "OK"
        elif tbo is None:
            # No TBO defined - component is likely on-condition
            comp_status = "ON_CONDITION"
        
        # Parse component type
        try:
            comp_type = ComponentType(comp_type_str)
        except ValueError:
            comp_type = ComponentType.LLP  # Default to LLP for unknown types
        
        # Format installed_date as string
        installed_date_str: Optional[str] = None
        if installed_date:
            if isinstance(installed_date, datetime):
                installed_date_str = installed_date.strftime("%Y-%m-%d")
            elif isinstance(installed_date, str):
                installed_date_str = installed_date
        
        components_list.append(CriticalComponentResponse(
            component_type=comp_type,
            part_no=part_no,
            serial_no=comp.get("serial_no"),
            description=comp.get("description"),
            installed_at_hours=round(installed_at_hours, 1),
            installed_date=installed_date_str,
            current_airframe_hours=round(current_airframe_hours, 1),
            time_since_install=round(time_since_install, 1),
            tbo=tbo,
            remaining=round(remaining, 1) if remaining is not None else None,
            status=comp_status,
            confidence=confidence
        ))
    
    logger.info(f"Found {len(components_list)} critical components for aircraft {aircraft_id}")
    
    return CriticalComponentsResponse(
        aircraft_id=aircraft_id,
        registration=registration,
        current_airframe_hours=round(current_airframe_hours, 1),
        components=components_list,
        last_updated=last_updated
    )


@router.post("/critical/{aircraft_id}/reprocess")
async def reprocess_aircraft_components(
    aircraft_id: str,
    current_user: User = Depends(get_current_user),
    db = Depends(get_database)
):
    """
    Reprocess all OCR history for an aircraft to extract/update critical components.
    
    Use this when:
    - OCR intelligence was added after scans were already processed
    - You want to refresh component data from existing OCR history
    
    Returns count of components created/updated.
    """
    logger.info(f"Reprocessing components for aircraft_id={aircraft_id}, user_id={current_user.id}")
    
    # Verify aircraft belongs to user
    aircraft = await db.aircrafts.find_one({
        "_id": aircraft_id,
        "user_id": current_user.id
    })
    
    if not aircraft:
        raise HTTPException(status_code=404, detail="Aircraft not found")
    
    # Import the intelligence service
    from services.ocr_intelligence import OCRIntelligenceService
    
    intelligence = OCRIntelligenceService(db)
    
    # Reprocess all OCR history
    total_created = await intelligence.reprocess_aircraft_history(
        aircraft_id=aircraft_id,
        user_id=current_user.id
    )
    
    logger.info(f"Reprocessed components for aircraft {aircraft_id}: {total_created} components created")
    
    return {
        "message": f"Reprocessed OCR history for aircraft {aircraft_id}",
        "components_created": total_created
    }
