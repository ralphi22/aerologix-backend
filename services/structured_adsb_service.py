"""
TC-Safe AD/SB Structured Comparison Service

Compares aircraft documentary evidence against TC AD/SB applicability.

DATA FLOW:
1. Registration → TC Registry lookup (authoritative identity)
2. TC Registry → aircraft identity (manufacturer, model, designator)
3. Aircraft identity → TC AD/SB applicability lookup
4. TC AD/SB list → comparison against OCR-applied references

TC-SAFE CONSTRAINTS:
- NO compliance decision
- NO "compliant" / "non-compliant" wording
- NO assumptions
- Informational and auditable only
- OCR data is documentary evidence only
"""

from datetime import datetime
from typing import Dict, List, Optional, Any, Tuple
from motor.motor_asyncio import AsyncIOMotorDatabase
from pydantic import BaseModel
from enum import Enum
import re
import logging

logger = logging.getLogger(__name__)


# ============================================================
# RESPONSE MODELS
# ============================================================

class RecurrenceInfo(BaseModel):
    """Recurrence information for AD/SB"""
    type: str  # "one-time", "recurring", "unspecified"
    interval: Optional[str] = None  # "500 hours", "12 months", etc.
    value: Optional[int] = None
    unit: Optional[str] = None  # "hours", "cycles", "months", "years"


class TCItemResult(BaseModel):
    """Single TC AD/SB item with detection count"""
    identifier: str
    type: str  # "AD" or "SB"
    title: Optional[str] = None
    effective_date: Optional[str] = None
    recurrence_info: RecurrenceInfo
    detected_count: int
    evidence_source: str  # "OCR documents" or "None found"
    ocr_dates: List[str] = []  # Dates when detected in OCR


class AircraftIdentity(BaseModel):
    """Aircraft identity from TC Registry"""
    registration: str
    manufacturer: str
    model: str
    designator: Optional[str] = None
    serial_number: Optional[str] = None
    owner_name: Optional[str] = None  # Informational only
    tc_source: str = "TC Registry"


class LookupStatus(str, Enum):
    """Status of TC AD/SB lookup"""
    SUCCESS = "SUCCESS"
    UNAVAILABLE = "UNAVAILABLE"


class StructuredComparisonResponse(BaseModel):
    """TC-Safe structured comparison response"""
    aircraft_identity: AircraftIdentity
    tc_ad_list: List[TCItemResult]
    tc_sb_list: List[TCItemResult]
    total_applicable_ad: int
    total_applicable_sb: int
    total_ad_with_evidence: int
    total_sb_with_evidence: int
    ocr_documents_analyzed: int
    analysis_date: str
    lookup_status: LookupStatus = LookupStatus.SUCCESS
    lookup_unavailable_reason: Optional[str] = None
    disclaimer: str


# ============================================================
# SERVICE
# ============================================================

class StructuredADSBComparisonService:
    """
    TC-Safe Structured AD/SB Comparison Service
    
    Provides factual comparison between TC AD/SB requirements
    and OCR documentary evidence.
    
    NO compliance decisions are made.
    """
    
    DISCLAIMER = (
        "This comparison is for INFORMATIONAL PURPOSES ONLY. "
        "It does NOT constitute a compliance assessment. "
        "All airworthiness decisions must be made by a licensed AME/TEA. "
        "AeroLogix AI does not determine aircraft airworthiness status."
    )
    
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
        
        Invalid values:
        - None
        - Empty string
        - Whitespace only
        - "Aucun", "N/A", "None", etc.
        - Registration patterns (C-XXXX)
        """
        if designator is None:
            return False
        
        cleaned = designator.strip().upper()
        
        # Empty or whitespace
        if not cleaned:
            return False
        
        # Known invalid placeholders
        if cleaned in self.INVALID_DESIGNATORS:
            return False
        
        # Block registration patterns (C-XXXX or CXXXX)
        if cleaned.startswith("C-") or (cleaned.startswith("C") and len(cleaned) == 5 and cleaned[1:].isalpha()):
            return False
        
        return True
    
    # --------------------------------------------------------
    # STEP 1: TC REGISTRY LOOKUP
    # --------------------------------------------------------
    
    async def lookup_tc_registry(self, registration: str) -> Optional[AircraftIdentity]:
        """
        Lookup aircraft in TC Registry by registration.
        
        TC Registry is the AUTHORITATIVE source for aircraft identity.
        
        Args:
            registration: Aircraft registration (e.g., "C-FGSO")
            
        Returns:
            AircraftIdentity if found, None otherwise
        """
        # Normalize registration
        reg_norm = registration.strip().upper().replace("-", "")
        if not reg_norm.startswith("C"):
            reg_norm = "C" + reg_norm
        
        # Lookup in tc_aircraft collection
        tc_aircraft = await self.db.tc_aircraft.find_one(
            {"registration_norm": reg_norm}
        )
        
        if not tc_aircraft:
            logger.warning(f"TC Registry lookup failed: {registration} not found")
            return None
        
        return AircraftIdentity(
            registration=tc_aircraft.get("registration", registration),
            manufacturer=tc_aircraft.get("manufacturer", ""),
            model=tc_aircraft.get("model", ""),
            designator=tc_aircraft.get("designator"),
            serial_number=tc_aircraft.get("serial_number"),
            owner_name=tc_aircraft.get("owner_name"),
            tc_source="TC Registry"
        )
    
    # --------------------------------------------------------
    # STEP 2: TC AD/SB APPLICABILITY LOOKUP (DESIGNATOR ONLY)
    # --------------------------------------------------------
    
    async def get_applicable_tc_adsb(
        self, 
        designator: str
    ) -> Tuple[List[Dict], List[Dict]]:
        """
        Get applicable AD/SB from TC database based on aircraft designator.
        
        CRITICAL: Uses ONLY designator for lookup.
        NO manufacturer, NO model, NO registration fallbacks.
        
        Args:
            designator: Type certificate designator (e.g., "3A19")
            
        Returns:
            Tuple of (applicable_ads, applicable_sbs)
        """
        applicable_ads = []
        applicable_sbs = []
        
        # DEFENSIVE: Log the exact key being used
        logger.info(f"TC AD/SB lookup using designator={designator}")
        
        # Query TC AD collection - DESIGNATOR ONLY
        ad_query = {
            "designator": designator,
            "is_active": True
        }
        
        async for ad in self.db.tc_ad.find(ad_query):
            applicable_ads.append({
                "identifier": ad.get("ref"),
                "type": "AD",
                "title": ad.get("title"),
                "effective_date": ad.get("effective_date"),
                "recurrence_type": ad.get("recurrence_type", "ONCE"),
                "recurrence_value": ad.get("recurrence_value"),
                "source_url": ad.get("source_url"),
            })
        
        # Query TC SB collection - DESIGNATOR ONLY
        sb_query = {
            "designator": designator,
            "is_active": True
        }
        
        async for sb in self.db.tc_sb.find(sb_query):
            applicable_sbs.append({
                "identifier": sb.get("ref"),
                "type": "SB",
                "title": sb.get("title"),
                "effective_date": sb.get("effective_date"),
                "recurrence_type": sb.get("recurrence_type", "ONCE"),
                "recurrence_value": sb.get("recurrence_value"),
                "is_mandatory": sb.get("is_mandatory", False),
                "source_url": sb.get("source_url"),
            })
        
        logger.info(
            f"TC AD/SB lookup completed | designator={designator} | "
            f"ADs={len(applicable_ads)} | SBs={len(applicable_sbs)}"
        )
        
        return applicable_ads, applicable_sbs
    
    # --------------------------------------------------------
    # STEP 3: OCR-APPLIED MAINTENANCE REFERENCES
    # --------------------------------------------------------
    
    async def get_ocr_adsb_references(
        self,
        aircraft_id: str,
        user_id: str
    ) -> Tuple[Dict[str, List[str]], int]:
        """
        Get AD/SB references from OCR APPLIED records ONLY.
        
        User-validated documents are the only source of documentary evidence.
        
        Args:
            aircraft_id: Aircraft ID in user's collection
            user_id: User ID
            
        Returns:
            Tuple of (reference_dict, document_count)
            reference_dict: {identifier: [dates_detected]}
        """
        references: Dict[str, List[str]] = {}
        document_count = 0
        
        # Get ONLY APPLIED OCR scans (user-validated)
        cursor = self.db.ocr_scans.find({
            "aircraft_id": aircraft_id,
            "user_id": user_id,
            "status": "APPLIED"  # ONLY user-validated documents
        })
        
        async for scan in cursor:
            document_count += 1
            scan_date = scan.get("created_at")
            date_str = scan_date.strftime("%Y-%m-%d") if scan_date else "Unknown"
            
            extracted_data = scan.get("extracted_data", {})
            
            # Extract AD/SB references
            adsb_refs = extracted_data.get("ad_sb_references", [])
            
            for ref in adsb_refs:
                if isinstance(ref, dict):
                    identifier = ref.get("reference_number") or ref.get("identifier") or ref.get("ref")
                elif isinstance(ref, str):
                    identifier = ref
                else:
                    continue
                
                if identifier:
                    # Normalize identifier
                    identifier = self._normalize_identifier(identifier)
                    
                    if identifier not in references:
                        references[identifier] = []
                    if date_str not in references[identifier]:
                        references[identifier].append(date_str)
        
        logger.info(
            f"OCR AD/SB references for aircraft {aircraft_id}: "
            f"{len(references)} unique references from {document_count} documents"
        )
        
        return references, document_count
    
    def _normalize_identifier(self, identifier: str) -> str:
        """
        Normalize AD/SB identifier for comparison.
        
        - Uppercase
        - Remove extra whitespace
        - Standardize format
        """
        if not identifier:
            return ""
        
        # Uppercase and strip
        normalized = identifier.strip().upper()
        
        # Remove multiple spaces
        normalized = re.sub(r'\s+', ' ', normalized)
        
        # Common normalizations
        # CF-2020-01 vs CF202001 vs CF 2020-01
        normalized = re.sub(r'[.\s]', '-', normalized)
        
        return normalized
    
    # --------------------------------------------------------
    # STEP 4: COUNTING LOGIC (NO DUPLICATES)
    # --------------------------------------------------------
    
    def _build_recurrence_info(self, tc_item: Dict) -> RecurrenceInfo:
        """
        Build recurrence information from TC item.
        """
        rec_type = tc_item.get("recurrence_type", "").upper()
        rec_value = tc_item.get("recurrence_value")
        
        if rec_type == "ONCE" or not rec_type:
            return RecurrenceInfo(
                type="one-time",
                interval=None,
                value=None,
                unit=None
            )
        elif rec_type in ["HOURS", "CYCLES", "YEARS", "MONTHS", "CALENDAR"]:
            unit_map = {
                "HOURS": "hours",
                "CYCLES": "cycles",
                "YEARS": "years",
                "MONTHS": "months",
                "CALENDAR": "calendar"
            }
            unit = unit_map.get(rec_type, rec_type.lower())
            interval = f"{rec_value} {unit}" if rec_value else "unspecified"
            
            return RecurrenceInfo(
                type="recurring",
                interval=interval,
                value=rec_value,
                unit=unit
            )
        else:
            return RecurrenceInfo(
                type="unspecified",
                interval=None,
                value=None,
                unit=None
            )
    
    def _count_detections(
        self,
        tc_items: List[Dict],
        ocr_references: Dict[str, List[str]]
    ) -> List[TCItemResult]:
        """
        Count OCR detections for each TC AD/SB item.
        
        ONE row per TC item - no duplicates.
        """
        results = []
        
        for item in tc_items:
            identifier = item.get("identifier", "")
            normalized_id = self._normalize_identifier(identifier)
            
            # Check for matches in OCR references
            detected_dates = []
            detected_count = 0
            
            for ocr_ref, dates in ocr_references.items():
                # Check for exact match or partial match
                if self._identifiers_match(normalized_id, ocr_ref):
                    detected_count += len(dates)
                    detected_dates.extend(dates)
            
            # Format effective date
            eff_date = item.get("effective_date")
            eff_date_str = None
            if eff_date:
                if isinstance(eff_date, datetime):
                    eff_date_str = eff_date.strftime("%Y-%m-%d")
                elif isinstance(eff_date, str):
                    eff_date_str = eff_date[:10]
            
            results.append(TCItemResult(
                identifier=identifier,
                type=item.get("type", "AD"),
                title=item.get("title"),
                effective_date=eff_date_str,
                recurrence_info=self._build_recurrence_info(item),
                detected_count=detected_count,
                evidence_source="OCR documents" if detected_count > 0 else "None found",
                ocr_dates=sorted(set(detected_dates))
            ))
        
        return results
    
    def _identifiers_match(self, tc_id: str, ocr_id: str) -> bool:
        """
        Check if TC identifier matches OCR identifier.
        
        Handles common variations:
        - CF-2020-01 vs CF202001
        - Different separators
        """
        if not tc_id or not ocr_id:
            return False
        
        # Exact match
        if tc_id == ocr_id:
            return True
        
        # Remove all separators and compare
        tc_clean = re.sub(r'[-_.\s]', '', tc_id)
        ocr_clean = re.sub(r'[-_.\s]', '', ocr_id)
        
        if tc_clean == ocr_clean:
            return True
        
        # Check if one contains the other (for partial references)
        if tc_clean in ocr_clean or ocr_clean in tc_clean:
            return True
        
        return False
    
    # --------------------------------------------------------
    # MAIN COMPARISON METHOD
    # --------------------------------------------------------
    
    async def compare(
        self,
        registration: str,
        aircraft_id: str,
        user_id: str
    ) -> StructuredComparisonResponse:
        """
        Perform TC-Safe structured AD/SB comparison.
        
        DATA FLOW:
        1. Registration → TC Registry lookup
        2. TC Registry → aircraft identity
        3. Aircraft identity → TC AD/SB applicability
        4. TC AD/SB → comparison against OCR references
        
        Args:
            registration: Aircraft registration (e.g., "C-FGSO")
            aircraft_id: Aircraft ID in user's collection
            user_id: User ID
            
        Returns:
            StructuredComparisonResponse with factual comparison data
        """
        logger.info(f"Starting structured AD/SB comparison for {registration}")
        
        # STEP 1: TC Registry lookup
        identity = await self.lookup_tc_registry(registration)
        
        if not identity:
            raise ValueError(
                f"Aircraft {registration} not found in TC Registry. "
                "Cannot perform AD/SB comparison without authoritative aircraft identity."
            )
        
        # STEP 2: Get applicable TC AD/SB
        applicable_ads, applicable_sbs = await self.get_applicable_tc_adsb(identity)
        
        # STEP 3: Get OCR references
        ocr_references, doc_count = await self.get_ocr_adsb_references(aircraft_id, user_id)
        
        # STEP 4: Count detections
        ad_results = self._count_detections(applicable_ads, ocr_references)
        sb_results = self._count_detections(applicable_sbs, ocr_references)
        
        # Count items with evidence
        ad_with_evidence = sum(1 for r in ad_results if r.detected_count > 0)
        sb_with_evidence = sum(1 for r in sb_results if r.detected_count > 0)
        
        logger.info(
            f"Structured comparison complete for {registration}: "
            f"{len(ad_results)} ADs ({ad_with_evidence} with evidence), "
            f"{len(sb_results)} SBs ({sb_with_evidence} with evidence)"
        )
        
        return StructuredComparisonResponse(
            aircraft_identity=identity,
            tc_ad_list=ad_results,
            tc_sb_list=sb_results,
            total_applicable_ad=len(ad_results),
            total_applicable_sb=len(sb_results),
            total_ad_with_evidence=ad_with_evidence,
            total_sb_with_evidence=sb_with_evidence,
            ocr_documents_analyzed=doc_count,
            analysis_date=datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
            disclaimer=self.DISCLAIMER
        )
