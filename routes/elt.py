"""ELT Routes for AeroLogix AI"""

from fastapi import APIRouter, Depends, HTTPException, status
from typing import Optional
from datetime import datetime, timedelta
from database.mongodb import get_database
from services.auth_deps import get_current_user
from models.elt import (
    ELTCreate, ELTUpdate, ELTResponse, ELTAlert,
    ELTStatus, ELTAlertLevel
)
from models.user import User
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/elt", tags=["elt"])


def parse_date_string(date_str: Optional[str]) -> Optional[datetime]:
    """Parse date string to datetime object"""
    if not date_str or date_str == '':
        return None
    try:
        # Handle ISO format with timezone
        if 'T' in date_str:
            return datetime.fromisoformat(date_str.replace('Z', '+00:00'))
        # Handle simple date format YYYY-MM-DD
        return datetime.strptime(date_str, '%Y-%m-%d')
    except (ValueError, TypeError):
        return None


def compute_elt_alerts(elt_data: dict) -> tuple[ELTStatus, list]:
    """Compute ELT status and alerts based on dates"""
    alerts = []
    status = ELTStatus.ACTIVE
    today = datetime.utcnow()
    warning_threshold = timedelta(days=15)
    
    # Check battery expiry
    if elt_data.get("battery_expiry_date"):
        battery_date = elt_data["battery_expiry_date"]
        if isinstance(battery_date, str):
            try:
                battery_date = datetime.fromisoformat(battery_date.replace('Z', '+00:00'))
            except:
                battery_date = None
        
        if battery_date:
            days_remaining = (battery_date - today).days
            
            if days_remaining < 0:
                alerts.append(ELTAlert(
                    type="battery",
                    level=ELTAlertLevel.CRITICAL,
                    message=f"Batterie ELT expirée depuis {abs(days_remaining)} jours",
                    due_date=battery_date,
                    days_remaining=days_remaining
                ))
                status = ELTStatus.EXPIRED
            elif days_remaining <= 15:
                alerts.append(ELTAlert(
                    type="battery",
                    level=ELTAlertLevel.WARNING,
                    message=f"Batterie ELT expire dans {days_remaining} jours",
                    due_date=battery_date,
                    days_remaining=days_remaining
                ))
                if status != ELTStatus.EXPIRED:
                    status = ELTStatus.PENDING_INSPECTION
    
    # Check last test date (annual test requirement)
    if elt_data.get("last_test_date"):
        test_date = elt_data["last_test_date"]
        if isinstance(test_date, str):
            try:
                test_date = datetime.fromisoformat(test_date.replace('Z', '+00:00'))
            except:
                test_date = None
        
        if test_date:
            next_test_due = test_date + timedelta(days=365)  # Annual test
            days_remaining = (next_test_due - today).days
            
            if days_remaining < 0:
                alerts.append(ELTAlert(
                    type="test",
                    level=ELTAlertLevel.CRITICAL,
                    message=f"Test ELT annuel en retard de {abs(days_remaining)} jours",
                    due_date=next_test_due,
                    days_remaining=days_remaining
                ))
                if status == ELTStatus.ACTIVE:
                    status = ELTStatus.PENDING_INSPECTION
            elif days_remaining <= 15:
                alerts.append(ELTAlert(
                    type="test",
                    level=ELTAlertLevel.WARNING,
                    message=f"Test ELT annuel dans {days_remaining} jours",
                    due_date=next_test_due,
                    days_remaining=days_remaining
                ))
                if status == ELTStatus.ACTIVE:
                    status = ELTStatus.PENDING_INSPECTION
    
    return status, alerts


@router.get("/aircraft/{aircraft_id}", response_model=Optional[ELTResponse])
async def get_aircraft_elt(
    aircraft_id: str,
    current_user: User = Depends(get_current_user),
    db=Depends(get_database)
):
    """Get ELT record for an aircraft"""
    
    # Verify aircraft belongs to user
    aircraft = await db.aircrafts.find_one({
        "_id": aircraft_id,
        "user_id": current_user.id
    })
    
    if not aircraft:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Aircraft not found"
        )
    
    # Get ELT record
    elt = await db.elt_records.find_one({
        "aircraft_id": aircraft_id,
        "user_id": current_user.id
    })
    
    if not elt:
        return None
    
    # Compute alerts
    elt_status, alerts = compute_elt_alerts(elt)
    
    return ELTResponse(
        _id=str(elt["_id"]),
        user_id=elt["user_id"],
        aircraft_id=elt["aircraft_id"],
        brand=elt.get("brand"),
        model=elt.get("model"),
        serial_number=elt.get("serial_number"),
        installation_date=elt.get("installation_date"),
        certification_date=elt.get("certification_date"),
        last_test_date=elt.get("last_test_date"),
        battery_expiry_date=elt.get("battery_expiry_date"),
        battery_install_date=elt.get("battery_install_date"),
        battery_interval_months=elt.get("battery_interval_months"),
        beacon_hex_id=elt.get("beacon_hex_id"),
        registration_number=elt.get("registration_number"),
        remarks=elt.get("remarks"),
        source=elt.get("source"),
        ocr_scan_id=elt.get("ocr_scan_id"),
        status=elt_status,
        alerts=[a.dict() for a in alerts],
        created_at=elt["created_at"],
        updated_at=elt["updated_at"]
    )


@router.post("/", response_model=ELTResponse)
async def create_elt(
    elt_data: ELTCreate,
    current_user: User = Depends(get_current_user),
    db=Depends(get_database)
):
    """Create ELT record for an aircraft"""
    
    # Verify aircraft belongs to user
    aircraft = await db.aircrafts.find_one({
        "_id": elt_data.aircraft_id,
        "user_id": current_user.id
    })
    
    if not aircraft:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Aircraft not found"
        )
    
    # Check if ELT already exists
    existing = await db.elt_records.find_one({
        "aircraft_id": elt_data.aircraft_id,
        "user_id": current_user.id
    })
    
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="ELT record already exists for this aircraft. Use PUT to update."
        )
    
    now = datetime.utcnow()
    elt_doc = {
        "user_id": current_user.id,
        "aircraft_id": elt_data.aircraft_id,
        "brand": elt_data.brand,
        "model": elt_data.model,
        "serial_number": elt_data.serial_number,
        "installation_date": parse_date_string(elt_data.installation_date),
        "certification_date": parse_date_string(elt_data.certification_date),
        "last_test_date": parse_date_string(elt_data.last_test_date),
        "battery_expiry_date": parse_date_string(elt_data.battery_expiry_date),
        "battery_install_date": parse_date_string(elt_data.battery_install_date),
        "battery_interval_months": elt_data.battery_interval_months,
        "beacon_hex_id": elt_data.beacon_hex_id,
        "registration_number": elt_data.registration_number,
        "remarks": elt_data.remarks,
        "source": elt_data.source,
        "ocr_scan_id": elt_data.ocr_scan_id,
        "created_at": now,
        "updated_at": now
    }
    
    result = await db.elt_records.insert_one(elt_doc)
    elt_doc["_id"] = str(result.inserted_id)
    
    # Compute alerts
    elt_status, alerts = compute_elt_alerts(elt_doc)
    
    logger.info(f"Created ELT record for aircraft {elt_data.aircraft_id}")
    
    return ELTResponse(
        _id=str(result.inserted_id),
        user_id=current_user.id,
        aircraft_id=elt_data.aircraft_id,
        brand=elt_data.brand,
        model=elt_data.model,
        serial_number=elt_data.serial_number,
        installation_date=elt_doc["installation_date"],
        certification_date=elt_doc["certification_date"],
        last_test_date=elt_doc["last_test_date"],
        battery_expiry_date=elt_doc["battery_expiry_date"],
        battery_install_date=elt_doc["battery_install_date"],
        battery_interval_months=elt_data.battery_interval_months,
        beacon_hex_id=elt_data.beacon_hex_id,
        registration_number=elt_data.registration_number,
        remarks=elt_data.remarks,
        source=elt_data.source,
        ocr_scan_id=elt_data.ocr_scan_id,
        status=elt_status,
        alerts=[a.dict() for a in alerts],
        created_at=now,
        updated_at=now
    )


@router.put("/aircraft/{aircraft_id}", response_model=ELTResponse)
async def update_elt(
    aircraft_id: str,
    elt_update: ELTUpdate,
    current_user: User = Depends(get_current_user),
    db=Depends(get_database)
):
    """Update ELT record for an aircraft"""
    
    # Verify aircraft belongs to user
    aircraft = await db.aircrafts.find_one({
        "_id": aircraft_id,
        "user_id": current_user.id
    })
    
    if not aircraft:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Aircraft not found"
        )
    
    # Get existing ELT
    existing = await db.elt_records.find_one({
        "aircraft_id": aircraft_id,
        "user_id": current_user.id
    })
    
    if not existing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="ELT record not found. Use POST to create."
        )
    
    # Build update dict with date parsing
    update_dict = {"updated_at": datetime.utcnow()}
    date_fields = ["installation_date", "certification_date", "last_test_date", 
                   "battery_expiry_date", "battery_install_date"]
    
    for field, value in elt_update.dict(exclude_unset=True).items():
        if value is not None:
            if field in date_fields:
                update_dict[field] = parse_date_string(value)
            else:
                update_dict[field] = value
    
    await db.elt_records.update_one(
        {"_id": existing["_id"]},
        {"$set": update_dict}
    )
    
    # Get updated record
    updated = await db.elt_records.find_one({"_id": existing["_id"]})
    
    # Compute alerts
    elt_status, alerts = compute_elt_alerts(updated)
    
    logger.info(f"Updated ELT record for aircraft {aircraft_id}")
    
    return ELTResponse(
        _id=str(updated["_id"]),
        user_id=updated["user_id"],
        aircraft_id=updated["aircraft_id"],
        brand=updated.get("brand"),
        model=updated.get("model"),
        serial_number=updated.get("serial_number"),
        installation_date=updated.get("installation_date"),
        certification_date=updated.get("certification_date"),
        last_test_date=updated.get("last_test_date"),
        battery_expiry_date=updated.get("battery_expiry_date"),
        battery_install_date=updated.get("battery_install_date"),
        battery_interval_months=updated.get("battery_interval_months"),
        beacon_hex_id=updated.get("beacon_hex_id"),
        registration_number=updated.get("registration_number"),
        remarks=updated.get("remarks"),
        source=updated.get("source"),
        ocr_scan_id=updated.get("ocr_scan_id"),
        status=elt_status,
        alerts=[a.dict() for a in alerts],
        created_at=updated["created_at"],
        updated_at=updated["updated_at"]
    )


@router.delete("/aircraft/{aircraft_id}")
async def delete_elt(
    aircraft_id: str,
    current_user: User = Depends(get_current_user),
    db=Depends(get_database)
):
    """Delete ELT record for an aircraft"""
    
    result = await db.elt_records.delete_one({
        "aircraft_id": aircraft_id,
        "user_id": current_user.id
    })
    
    if result.deleted_count == 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="ELT record not found"
        )
    
    logger.info(f"Deleted ELT record for aircraft {aircraft_id}")
    
    return {"message": "ELT record deleted successfully"}
