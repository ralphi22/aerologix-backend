"""
AD/SB Comparison Service

Compares aircraft OCR records against Transport Canada AD/SB database.

TC-SAFE:
- Never returns "compliant" / "non-compliant"
- Only returns factual comparison data
- All compliance decisions are made by licensed AME/TEA
"""

from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Tuple
from motor.motor_asyncio import AsyncIOMotorDatabase
import logging

from models.tc_adsb import (
    ADSBType, RecurrenceType, ComparisonStatus,
    ADSBComparisonItem, NewTCItem, ADSBComparisonResponse
)

logger = logging.getLogger(__name__)


class ADSBComparisonService:
    """Service for comparing aircraft records against TC AD/SB database"""
    
    # Invalid designator values that must trigger fail-fast
    INVALID_DESIGNATORS = frozenset(["", "AUCUN", "N/A", "NONE", "NULL", "UNKNOWN"])
    
    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db
    
    # --------------------------------------------------------
    # DESIGNATOR VALIDATION (FAIL-FAST)
    # --------------------------------------------------------
    
    def _is_valid_designator(self, designator: Optional[str]) -> bool:
        """
        Validate that designator is usable for TC AD/SB lookup.
        
        FAIL-FAST: Returns False for any invalid value.
        """
        if designator is None:
            return False
        
        cleaned = designator.strip().upper()
        
        if not cleaned:
            return False
        
        if cleaned in self.INVALID_DESIGNATORS:
            return False
        
        # Block registration patterns
        if cleaned.startswith("C-") or (cleaned.startswith("C") and len(cleaned) == 5 and cleaned[1:].isalpha()):
            return False
        
        return True
    
    async def get_aircraft_info(self, aircraft_id: str, user_id: str) -> Optional[Dict]:
        """
        Get aircraft information including designator.
        
        First checks user's aircraft, then falls back to TC registry.
        """
        # Get from user's aircraft collection
        aircraft = await self.db.aircrafts.find_one({
            "_id": aircraft_id,
            "user_id": user_id
        })
        
        if not aircraft:
            return None
        
        # If no designator in user aircraft, try TC lookup
        if not aircraft.get("designator"):
            registration = aircraft.get("registration", "")
            if registration:
                tc_aircraft = await self.db.tc_aeronefs.find_one({
                    "registration": registration.upper()
                })
                if tc_aircraft:
                    aircraft["designator"] = tc_aircraft.get("designator")
                    aircraft["tc_manufacturer"] = tc_aircraft.get("manufacturer")
                    aircraft["tc_model"] = tc_aircraft.get("model")
        
        return aircraft
    
    async def get_ocr_adsb_records(
        self, 
        aircraft_id: str, 
        user_id: str
    ) -> Tuple[List[Dict], Optional[datetime]]:
        """
        Get all AD/SB references from OCR scans for an aircraft.
        
        Priority:
        1. Use last APPLIED scan date
        2. Fallback to latest COMPLETED scan date (with warning log)
        
        Returns:
            - List of AD/SB records from OCR
            - Last applied/scan date
        """
        adsb_records = []
        last_applied_date = None
        last_scan_date = None
        used_fallback = False
        
        # Get all OCR scans for this aircraft (prioritize APPLIED)
        cursor = self.db.ocr_scans.find({
            "aircraft_id": aircraft_id,
            "user_id": user_id,
            "status": {"$in": ["COMPLETED", "APPLIED"]}
        }).sort("created_at", -1)
        
        async for scan in cursor:
            scan_date = scan.get("created_at")
            scan_status = scan.get("status", "")
            
            # Track last applied date (priority) vs last scan date (fallback)
            if scan_status == "APPLIED":
                if last_applied_date is None:
                    last_applied_date = scan_date
            else:
                if last_scan_date is None:
                    last_scan_date = scan_date
            
            # Extract AD/SB references from extracted_data
            extracted = scan.get("extracted_data", {})
            if isinstance(extracted, dict):
                ad_sb_refs = extracted.get("ad_sb_references", [])
                
                for ref in ad_sb_refs:
                    if isinstance(ref, dict):
                        adsb_records.append({
                            "ref": ref.get("reference_number", "").upper(),
                            "type": ref.get("adsb_type", "AD").upper(),
                            "compliance_date": ref.get("compliance_date"),
                            "airframe_hours": ref.get("airframe_hours"),
                            "description": ref.get("description"),
                            "scan_id": scan.get("_id"),
                            "scan_date": scan_date,
                            "source": "ocr"
                        })
        
        # Determine which date to use
        if last_applied_date:
            effective_date = last_applied_date
        elif last_scan_date:
            effective_date = last_scan_date
            used_fallback = True
            logger.warning(
                f"No APPLIED scans found for aircraft {aircraft_id}, "
                f"using latest COMPLETED scan date as fallback"
            )
        else:
            effective_date = None
        
        # Also check adsb_records collection (manual entries)
        cursor = self.db.adsb_records.find({
            "aircraft_id": aircraft_id,
            "user_id": user_id
        })
        
        async for record in cursor:
            adsb_records.append({
                "ref": record.get("reference_number", "").upper(),
                "type": record.get("adsb_type", "AD").upper(),
                "compliance_date": record.get("compliance_date"),
                "airframe_hours": record.get("compliance_hours"),
                "description": record.get("description"),
                "source": "manual",
                "record_date": record.get("created_at")
            })
            
            # Update effective date if manual record is more recent
            record_date = record.get("created_at")
            if record_date and (effective_date is None or record_date > effective_date):
                effective_date = record_date
        
        return adsb_records, effective_date
    
    async def get_tc_requirements(
        self,
        designator: Optional[str],
        manufacturer: Optional[str],
        model: Optional[str]
    ) -> List[Dict]:
        """
        Get all applicable TC AD/SB requirements for an aircraft.
        
        Matches by:
        - Designator (type certificate)
        - Manufacturer
        - Model (partial match)
        """
        requirements = []
        
        # Build query for TC_AD
        ad_query = {"is_active": True}
        sb_query = {"is_active": True}
        
        # Match by designator OR manufacturer
        or_conditions = []
        
        if designator:
            or_conditions.append({"designator": designator})
        
        if manufacturer:
            # Case-insensitive partial match
            or_conditions.append({
                "manufacturer": {"$regex": manufacturer, "$options": "i"}
            })
        
        if model:
            or_conditions.append({
                "model": {"$regex": model, "$options": "i"}
            })
        
        if or_conditions:
            ad_query["$or"] = or_conditions
            sb_query["$or"] = or_conditions
        
        # Get ADs
        async for ad in self.db.tc_ad.find(ad_query):
            requirements.append({
                "ref": ad.get("ref"),
                "type": ADSBType.AD,
                "title": ad.get("title"),
                "designator": ad.get("designator"),
                "manufacturer": ad.get("manufacturer"),
                "model": ad.get("model"),
                "effective_date": ad.get("effective_date"),
                "recurrence_type": ad.get("recurrence_type", RecurrenceType.ONCE),
                "recurrence_value": ad.get("recurrence_value"),
                "compliance_text": ad.get("compliance_text"),
                "source_url": ad.get("source_url"),
            })
        
        # Get SBs
        async for sb in self.db.tc_sb.find(sb_query):
            requirements.append({
                "ref": sb.get("ref"),
                "type": ADSBType.SB,
                "title": sb.get("title"),
                "designator": sb.get("designator"),
                "manufacturer": sb.get("manufacturer"),
                "model": sb.get("model"),
                "effective_date": sb.get("effective_date"),
                "recurrence_type": sb.get("recurrence_type", RecurrenceType.ONCE),
                "recurrence_value": sb.get("recurrence_value"),
                "compliance_text": sb.get("compliance_text"),
                "source_url": sb.get("source_url"),
                "is_mandatory": sb.get("is_mandatory", False),
            })
        
        return requirements
    
    def normalize_ref(self, ref: str) -> str:
        """Normalize AD/SB reference for comparison"""
        if not ref:
            return ""
        # Remove common prefixes and normalize
        ref = ref.upper().strip()
        ref = ref.replace("AD ", "").replace("SB ", "")
        ref = ref.replace("-", "").replace(" ", "")
        return ref
    
    def find_matching_record(
        self, 
        tc_ref: str, 
        ocr_records: List[Dict]
    ) -> Optional[Dict]:
        """
        Find matching OCR record for a TC requirement.
        
        Uses fuzzy matching on reference numbers.
        """
        tc_ref_normalized = self.normalize_ref(tc_ref)
        
        for record in ocr_records:
            ocr_ref_normalized = self.normalize_ref(record.get("ref", ""))
            
            # Exact match
            if tc_ref_normalized == ocr_ref_normalized:
                return record
            
            # Partial match (TC ref contained in OCR ref or vice versa)
            if tc_ref_normalized in ocr_ref_normalized or ocr_ref_normalized in tc_ref_normalized:
                return record
        
        return None
    
    def calculate_next_due(
        self,
        recurrence_type: RecurrenceType,
        recurrence_value: Optional[int],
        last_compliance_date: Optional[datetime],
        last_hours: Optional[float] = None
    ) -> Tuple[Optional[str], Optional[datetime]]:
        """
        Calculate next due date/hours for recurring items.
        
        Returns:
        - String representation of next due
        - Datetime of next due (for DUE_SOON calculation)
        """
        if recurrence_type == RecurrenceType.ONCE:
            return None, None
        
        if recurrence_value is None:
            return None, None
        
        if recurrence_type == RecurrenceType.YEARS:
            if last_compliance_date:
                next_due_dt = last_compliance_date + timedelta(days=365 * recurrence_value)
                return next_due_dt.strftime("%Y-%m-%d"), next_due_dt
            return f"Every {recurrence_value} year(s) from compliance", None
        
        elif recurrence_type == RecurrenceType.HOURS:
            if last_hours is not None:
                next_due_hours = last_hours + recurrence_value
                return f"{next_due_hours:.1f} hours", None
            return f"Every {recurrence_value} hours from compliance", None
        
        elif recurrence_type == RecurrenceType.CYCLES:
            return f"Every {recurrence_value} cycles", None
        
        elif recurrence_type == RecurrenceType.CALENDAR:
            if last_compliance_date:
                next_due_dt = last_compliance_date + timedelta(days=30 * recurrence_value)
                return next_due_dt.strftime("%Y-%m-%d"), next_due_dt
            return f"Every {recurrence_value} month(s) from compliance", None
        
        return None, None
    
    def determine_status(
        self,
        found: bool,
        recurrence_type: RecurrenceType,
        next_due: Optional[str],
        effective_date: Optional[datetime],
        last_logbook_date: Optional[datetime],
        next_due_datetime: Optional[datetime] = None
    ) -> ComparisonStatus:
        """
        Determine comparison status (TC-SAFE: never compliance).
        
        Returns:
        - MISSING: Item not found in aircraft records
        - NEW: TC item effective_date > last_logbook_date
        - DUE_SOON: Found but recurring item due within 90 days
        - OK: Found and not due soon
        """
        if not found:
            # Check if it's a new regulatory item
            if effective_date and last_logbook_date:
                if effective_date > last_logbook_date:
                    return ComparisonStatus.NEW_REGULATORY
            return ComparisonStatus.MISSING
        
        # Item found - check if due soon for recurring items
        if recurrence_type != RecurrenceType.ONCE and next_due_datetime:
            days_until_due = (next_due_datetime - datetime.utcnow()).days
            if days_until_due <= 90:  # Due within 90 days
                return ComparisonStatus.DUE_SOON
        
        return ComparisonStatus.OK
    
    async def compare(
        self,
        aircraft_id: str,
        user_id: str
    ) -> ADSBComparisonResponse:
        """
        Main comparison function.
        
        Compares aircraft OCR records against TC AD/SB database.
        """
        logger.info(f"AD/SB Comparison started | aircraft_id={aircraft_id}")
        
        # Get aircraft info
        aircraft = await self.get_aircraft_info(aircraft_id, user_id)
        
        if not aircraft:
            raise ValueError("Aircraft not found or not authorized")
        
        designator = aircraft.get("designator")
        manufacturer = aircraft.get("manufacturer") or aircraft.get("tc_manufacturer")
        model = aircraft.get("model") or aircraft.get("tc_model")
        registration = aircraft.get("registration")
        
        # Get OCR records
        ocr_records, last_logbook_date = await self.get_ocr_adsb_records(
            aircraft_id, user_id
        )
        
        logger.info(f"Found {len(ocr_records)} OCR AD/SB records")
        
        # Get TC requirements
        tc_requirements = await self.get_tc_requirements(
            designator, manufacturer, model
        )
        
        logger.info(f"Found {len(tc_requirements)} TC requirements for designator={designator}")
        
        # Build comparison
        comparison_items = []
        new_tc_items = []
        found_count = 0
        missing_count = 0
        
        for tc_req in tc_requirements:
            ref = tc_req.get("ref", "")
            matching_record = self.find_matching_record(ref, ocr_records)
            
            found = matching_record is not None
            
            # Get compliance date from matching record
            last_recorded_date = None
            last_hours = None
            if matching_record:
                if matching_record.get("compliance_date"):
                    last_recorded_date = matching_record.get("compliance_date")
                elif matching_record.get("scan_date"):
                    last_recorded_date = matching_record["scan_date"].strftime("%Y-%m-%d")
                last_hours = matching_record.get("airframe_hours")
            
            # Parse last_recorded_date to datetime if string
            last_compliance_dt = None
            if last_recorded_date:
                if isinstance(last_recorded_date, str):
                    try:
                        last_compliance_dt = datetime.strptime(last_recorded_date, "%Y-%m-%d")
                    except:
                        pass
                elif isinstance(last_recorded_date, datetime):
                    last_compliance_dt = last_recorded_date
            
            # Calculate next due
            recurrence_type = tc_req.get("recurrence_type", RecurrenceType.ONCE)
            if isinstance(recurrence_type, str):
                recurrence_type = RecurrenceType(recurrence_type)
            
            next_due, next_due_dt = self.calculate_next_due(
                recurrence_type,
                tc_req.get("recurrence_value"),
                last_compliance_dt,
                last_hours
            )
            
            # Determine status
            effective_date = tc_req.get("effective_date")
            status = self.determine_status(
                found, recurrence_type, next_due,
                effective_date, last_logbook_date, next_due_dt
            )
            
            # Track counts
            if found:
                found_count += 1
            else:
                missing_count += 1
            
            # Check for new regulatory items
            if status == ComparisonStatus.NEW_REGULATORY:
                new_tc_items.append(NewTCItem(
                    ref=ref,
                    type=tc_req.get("type", ADSBType.AD),
                    title=tc_req.get("title"),
                    effective_date=effective_date.strftime("%Y-%m-%d") if effective_date else "Unknown",
                    source_url=tc_req.get("source_url")
                ))
            
            # Build comparison item
            comparison_items.append(ADSBComparisonItem(
                ref=ref,
                type=tc_req.get("type", ADSBType.AD),
                title=tc_req.get("title"),
                found=found,
                last_recorded_date=last_recorded_date if isinstance(last_recorded_date, str) else None,
                recurrence_type=recurrence_type,
                recurrence_value=tc_req.get("recurrence_value"),
                next_due=next_due,
                status=status,
                source="tc"
            ))
        
        # Also include OCR items not in TC (aircraft-specific)
        tc_refs_normalized = {self.normalize_ref(r.get("ref", "")) for r in tc_requirements}
        
        for ocr_record in ocr_records:
            ocr_ref_normalized = self.normalize_ref(ocr_record.get("ref", ""))
            if ocr_ref_normalized and ocr_ref_normalized not in tc_refs_normalized:
                comparison_items.append(ADSBComparisonItem(
                    ref=ocr_record.get("ref", ""),
                    type=ADSBType(ocr_record.get("type", "AD")),
                    title=ocr_record.get("description"),
                    found=True,
                    last_recorded_date=ocr_record.get("compliance_date"),
                    recurrence_type=RecurrenceType.ONCE,
                    recurrence_value=None,
                    next_due=None,
                    status=ComparisonStatus.INFO_ONLY,
                    source="ocr"
                ))
        
        # Sort: missing first, then by ref
        comparison_items.sort(key=lambda x: (x.found, x.ref))
        
        logger.info(
            f"AD/SB Comparison complete | aircraft_id={aircraft_id} | "
            f"tc_items={len(tc_requirements)} | found={found_count} | missing={missing_count}"
        )
        
        return ADSBComparisonResponse(
            aircraft_id=aircraft_id,
            registration=registration,
            designator=designator,
            manufacturer=manufacturer,
            model=model,
            last_logbook_date=last_logbook_date.strftime("%Y-%m-%d") if last_logbook_date else None,
            total_tc_items=len(tc_requirements),
            found_count=found_count,
            missing_count=missing_count,
            new_tc_items=new_tc_items,
            comparison=comparison_items
        )
