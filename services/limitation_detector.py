"""
TEA Operational Limitations Detector

Extracts operational limitations written by TEA/AME from OCR reports.
These are FACTS from the document - NOT calculated statuses.

ABSOLUTE RULES:
❌ Never transform to status
❌ Never deduce rules
❌ Never calculate compliance
✔ Always keep raw text
✔ Always reference the report
"""

import re
import logging
from typing import Dict, List, Optional, Any, Tuple
from datetime import datetime
from motor.motor_asyncio import AsyncIOMotorDatabase

from models.operational_limitations import (
    LimitationCategory,
    OperationalLimitationCreate
)

logger = logging.getLogger(__name__)


# ============================================================
# DETECTION PATTERNS BY CATEGORY
# ============================================================

# ELT-related limitations
ELT_PATTERNS = [
    # Distance limitations
    (r'\b25\s*n\.?m\.?\b', 0.95, "25 NM"),
    (r'\blimited\s+to\s+25\b', 0.95, "LIMITED TO 25"),
    (r'\b25\s*nautical\s*miles?\b', 0.95, "25 NAUTICAL MILES"),
    
    # ELT status
    (r'\belt\s+removed\b', 0.90, "ELT REMOVED"),
    (r'\belt\s+expired\b', 0.90, "ELT EXPIRED"),
    (r'\belt\s+inoperative\b', 0.90, "ELT INOPERATIVE"),
    (r'\belt\s+unserviceable\b', 0.90, "ELT UNSERVICEABLE"),
    (r'\belt\s+battery\s+expired\b', 0.90, "ELT BATTERY EXPIRED"),
    (r'\bno\s+elt\b', 0.85, "NO ELT"),
    (r'\bwithout\s+elt\b', 0.85, "WITHOUT ELT"),
    
    # ELT general
    (r'\belt\s+(?:must|shall|to)\s+be\b', 0.80, "ELT REQUIREMENT"),
]

# Avionics-related limitations
AVIONICS_PATTERNS = [
    # Airspace restrictions
    (r'\bcontrol(?:led)?\s+zone\b', 0.90, "CONTROL ZONE"),
    (r'\bcontrolled\s+airspace\b', 0.90, "CONTROLLED AIRSPACE"),
    (r'\bclass\s+[a-d]\s+airspace\b', 0.90, "CLASS AIRSPACE"),
    (r'\bimc\b', 0.80, "IMC"),
    (r'\bvfr\s+only\b', 0.90, "VFR ONLY"),
    (r'\bday\s+vfr\s+only\b', 0.95, "DAY VFR ONLY"),
    (r'\bnight\s+vfr\b', 0.85, "NIGHT VFR"),
    
    # Instrument limitations
    (r'\bpitot\b', 0.80, "PITOT"),
    (r'\bstatic\b', 0.75, "STATIC"),
    (r'\btransponder\b', 0.85, "TRANSPONDER"),
    (r'\bencoder\b', 0.85, "ENCODER"),
    (r'\baltimeter\b', 0.80, "ALTIMETER"),
    (r'\bairspeed\s+indicator\b', 0.85, "AIRSPEED INDICATOR"),
    (r'\battitude\s+indicator\b', 0.85, "ATTITUDE INDICATOR"),
    (r'\bheading\s+indicator\b', 0.85, "HEADING INDICATOR"),
    (r'\bturn\s+coordinator\b', 0.85, "TURN COORDINATOR"),
    (r'\bvsi\b', 0.80, "VSI"),
    (r'\bvertical\s+speed\b', 0.80, "VERTICAL SPEED"),
    
    # Communication
    (r'\bcom\s*(?:1|2)?\s+(?:inop|u/s|unserviceable)\b', 0.90, "COM INOP"),
    (r'\bnav\s*(?:1|2)?\s+(?:inop|u/s|unserviceable)\b', 0.90, "NAV INOP"),
    (r'\bgps\s+(?:inop|u/s|unserviceable)\b', 0.90, "GPS INOP"),
    (r'\bads-?b\b', 0.85, "ADS-B"),
    
    # Timing requirements
    (r'\bmust\s+be\s+done\s+before\b', 0.90, "MUST BE DONE BEFORE"),
    (r'\bprior\s+to\s+(?:entering|flight)\b', 0.85, "PRIOR TO"),
    (r'\bbefore\s+(?:entering|next\s+flight)\b', 0.85, "BEFORE"),
]

# Propeller limitations
PROPELLER_PATTERNS = [
    (r'\bprop(?:eller)?\s+(?:on\s+condition|overdue)\b', 0.90, "PROP CONDITION"),
    (r'\bprop(?:eller)?\s+(?:limited|restricted)\b', 0.90, "PROP LIMITED"),
    (r'\bblade\s+(?:damage|nick|crack)\b', 0.85, "BLADE DAMAGE"),
    (r'\bprop(?:eller)?\s+inspection\s+(?:due|overdue|required)\b', 0.90, "PROP INSPECTION"),
]

# Engine limitations
ENGINE_PATTERNS = [
    (r'\bengine\s+(?:on\s+condition|overdue)\b', 0.90, "ENGINE CONDITION"),
    (r'\bengine\s+(?:limited|restricted)\b', 0.90, "ENGINE LIMITED"),
    (r'\bpower\s+(?:limited|restricted|reduced)\b', 0.85, "POWER LIMITED"),
    (r'\brpm\s+(?:limited|restricted|max)\b', 0.85, "RPM LIMITED"),
    (r'\bmanifold\s+pressure\s+(?:limited|restricted)\b', 0.85, "MANIFOLD PRESSURE LIMITED"),
    (r'\boil\s+(?:consumption|pressure|temp)\b', 0.80, "OIL"),
    (r'\bmagneto\b', 0.75, "MAGNETO"),
]

# Fire extinguisher limitations
FIRE_EXTINGUISHER_PATTERNS = [
    (r'\bfire\s+extinguisher\b', 0.90, "FIRE EXTINGUISHER"),
    (r'\bextinguisher\s+(?:expired|overdue|due)\b', 0.95, "EXTINGUISHER EXPIRED"),
    (r'\bextinguisher\s+(?:removed|missing)\b', 0.90, "EXTINGUISHER REMOVED"),
    (r'\bextinguisher\s+(?:inspection|service)\b', 0.85, "EXTINGUISHER INSPECTION"),
    (r'\bhalon\b', 0.80, "HALON"),
    (r'\bco2\s+extinguisher\b', 0.85, "CO2 EXTINGUISHER"),
    (r'\bdry\s+(?:chemical|powder)\s+extinguisher\b', 0.85, "DRY CHEMICAL EXTINGUISHER"),
]

# General operational limitations
GENERAL_PATTERNS = [
    # Condition-based
    (r'\bon\s+condition\b', 0.90, "ON CONDITION"),
    (r'\boverdue\b', 0.85, "OVERDUE"),
    (r'\bnot\s+serviceable\b', 0.90, "NOT SERVICEABLE"),
    (r'\bunserviceable\b', 0.90, "UNSERVICEABLE"),
    (r'\bu/s\b', 0.85, "U/S"),
    (r'\binoperative\b', 0.90, "INOPERATIVE"),
    (r'\binop\b', 0.85, "INOP"),
    
    # Restrictions
    (r'\brestricted\b', 0.85, "RESTRICTED"),
    (r'\blimited\b', 0.80, "LIMITED"),
    (r'\bprohibited\b', 0.90, "PROHIBITED"),
    (r'\bnot\s+(?:for|approved\s+for)\b', 0.85, "NOT APPROVED"),
    
    # Requirements
    (r'\bmust\s+(?:be|have|comply)\b', 0.80, "MUST"),
    (r'\bshall\s+(?:be|have|comply)\b', 0.80, "SHALL"),
    (r'\brequired\s+(?:before|prior)\b', 0.85, "REQUIRED BEFORE"),
    (r'\bdo\s+not\s+(?:fly|operate)\b', 0.95, "DO NOT OPERATE"),
    
    # Time-based
    (r'\bwithin\s+\d+\s*(?:hours?|days?|flights?)\b', 0.85, "TIME LIMIT"),
    (r'\bbefore\s+next\s+(?:flight|100\s*h)\b', 0.90, "BEFORE NEXT"),
    (r'\bgrounded\b', 0.95, "GROUNDED"),
    (r'\baog\b', 0.90, "AOG"),  # Aircraft On Ground
]

# Phrases that indicate a limitation context
LIMITATION_CONTEXT_PHRASES = [
    r'\blimitation[s]?\b',
    r'\brestriction[s]?\b',
    r'\bsnag[s]?\b',
    r'\bdeficienc(?:y|ies)\b',
    r'\bdiscrepanc(?:y|ies)\b',
    r'\bdeferred\b',
    r'\bmel\b',  # Minimum Equipment List
    r'\bcdl\b',  # Configuration Deviation List
]


class LimitationDetectorService:
    """
    Detects TEA operational limitations from OCR reports.
    Stores raw text - never transforms to status.
    """
    
    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db
    
    def _normalize_text(self, text: str) -> str:
        """Normalize text for pattern matching"""
        if not text:
            return ""
        # Lowercase, normalize whitespace
        text = text.lower()
        text = re.sub(r'\s+', ' ', text)
        return text
    
    def _extract_sentence_context(
        self, 
        text: str, 
        match_start: int, 
        match_end: int,
        window: int = 100
    ) -> str:
        """
        Extract the sentence/context around a match.
        Tries to find sentence boundaries, falls back to character window.
        """
        # Try to find sentence boundaries
        text_before = text[max(0, match_start - window):match_start]
        text_after = text[match_end:min(len(text), match_end + window)]
        
        # Find start of sentence (period, newline, or start of text)
        sentence_start = match_start - window
        for delimiter in ['. ', '.\n', '\n\n', '\n']:
            idx = text_before.rfind(delimiter)
            if idx != -1:
                sentence_start = match_start - (len(text_before) - idx - len(delimiter))
                break
        sentence_start = max(0, sentence_start)
        
        # Find end of sentence
        sentence_end = match_end + window
        for delimiter in ['. ', '.\n', '\n\n', '\n']:
            idx = text_after.find(delimiter)
            if idx != -1:
                sentence_end = match_end + idx + 1
                break
        sentence_end = min(len(text), sentence_end)
        
        # Extract and clean
        context = text[sentence_start:sentence_end].strip()
        context = re.sub(r'\s+', ' ', context)
        
        return context
    
    def _has_limitation_context(self, text: str) -> Tuple[bool, float]:
        """
        Check if text has limitation context phrases.
        Returns (has_context, confidence_boost)
        """
        normalized = self._normalize_text(text)
        
        for pattern in LIMITATION_CONTEXT_PHRASES:
            if re.search(pattern, normalized, re.IGNORECASE):
                return True, 0.1
        
        return False, 0.0
    
    def _detect_category_patterns(
        self,
        text: str,
        patterns: List[Tuple[str, float, str]],
        category: LimitationCategory
    ) -> List[Dict[str, Any]]:
        """
        Detect patterns for a specific category.
        Returns list of detected limitations.
        """
        detected = []
        normalized = self._normalize_text(text)
        
        # Check for context boost
        has_context, context_boost = self._has_limitation_context(text)
        
        for pattern, base_confidence, keyword in patterns:
            for match in re.finditer(pattern, normalized, re.IGNORECASE):
                # Extract sentence context
                context = self._extract_sentence_context(
                    text, 
                    match.start(), 
                    match.end()
                )
                
                # Calculate confidence
                confidence = min(1.0, base_confidence + context_boost)
                
                # Only include if confidence is reasonable
                if confidence >= 0.5:
                    detected.append({
                        "limitation_text": context.upper(),  # Keep as written (uppercase for consistency)
                        "detected_keywords": [keyword],
                        "category": category,
                        "confidence": round(confidence, 2),
                        "match_position": match.start()
                    })
        
        return detected
    
    def detect_limitations(
        self, 
        text: str,
        description: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Detect operational limitations in OCR text.
        
        Args:
            text: Raw OCR text
            description: Additional description/remarks text
        
        Returns:
            List of detected limitations with context
        """
        all_detected = []
        
        # Combine text sources
        full_text = f"{text or ''} {description or ''}"
        
        if not full_text.strip():
            return all_detected
        
        # Detect by category
        category_patterns = [
            (ELT_PATTERNS, LimitationCategory.ELT),
            (AVIONICS_PATTERNS, LimitationCategory.AVIONICS),
            (PROPELLER_PATTERNS, LimitationCategory.PROPELLER),
            (ENGINE_PATTERNS, LimitationCategory.ENGINE),
            (GENERAL_PATTERNS, LimitationCategory.GENERAL),
        ]
        
        for patterns, category in category_patterns:
            detected = self._detect_category_patterns(full_text, patterns, category)
            all_detected.extend(detected)
        
        # Deduplicate by limitation_text
        seen_texts = set()
        unique_detected = []
        
        for item in all_detected:
            text_key = item["limitation_text"][:100].lower()
            if text_key not in seen_texts:
                seen_texts.add(text_key)
                unique_detected.append(item)
        
        # Sort by confidence (highest first)
        unique_detected.sort(key=lambda x: (-x["confidence"], x.get("match_position", 0)))
        
        return unique_detected
    
    async def process_ocr_report(
        self,
        aircraft_id: str,
        user_id: str,
        report_id: str,
        raw_text: str,
        extracted_data: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """
        Process an OCR report and extract operational limitations.
        
        Args:
            aircraft_id: Aircraft ID
            user_id: User ID
            report_id: OCR scan ID
            raw_text: Raw OCR text
            extracted_data: Structured extracted data
        
        Returns:
            List of created limitation records
        """
        created_limitations = []
        
        try:
            # Get additional text from extracted data
            description = extracted_data.get("description", "")
            remarks = extracted_data.get("remarks", "")
            work_performed = extracted_data.get("work_performed", "")
            
            # Build full text to analyze
            text_parts = [raw_text or ""]
            if description:
                text_parts.append(description)
            if remarks:
                text_parts.append(remarks)
            if work_performed:
                text_parts.append(work_performed)
            
            # Check limitations_or_notes field specifically
            limitations_notes = extracted_data.get("limitations_or_notes", [])
            if limitations_notes:
                for note in limitations_notes:
                    if isinstance(note, dict):
                        text_parts.append(note.get("text", ""))
                    elif isinstance(note, str):
                        text_parts.append(note)
            
            full_text = " ".join(text_parts)
            
            # Detect limitations
            detected = self.detect_limitations(full_text)
            
            logger.info(f"Limitation Detector | report={report_id} | detected={len(detected)} limitations")
            
            # Parse report date
            report_date = None
            date_str = extracted_data.get("date") or extracted_data.get("report_date")
            if date_str:
                try:
                    report_date = datetime.strptime(date_str, "%Y-%m-%d")
                except:
                    try:
                        report_date = datetime.strptime(date_str, "%Y/%m/%d")
                    except:
                        pass
            
            now = datetime.utcnow()
            
            # Create limitation records
            for lim in detected:
                doc = {
                    "aircraft_id": aircraft_id,
                    "user_id": user_id,
                    "report_id": report_id,
                    "limitation_text": lim["limitation_text"][:500],  # Limit length
                    "detected_keywords": lim["detected_keywords"],
                    "category": lim["category"].value,
                    "confidence": lim["confidence"],
                    "source": "OCR",
                    "report_date": report_date,
                    "created_at": now,
                }
                
                # Upsert to avoid duplicates
                try:
                    result = await self.db.operational_limitations.update_one(
                        {
                            "aircraft_id": aircraft_id,
                            "report_id": report_id,
                            "limitation_text": lim["limitation_text"][:500],
                        },
                        {"$set": doc},
                        upsert=True
                    )
                    
                    if result.upserted_id:
                        doc["_id"] = str(result.upserted_id)
                        created_limitations.append(doc)
                        logger.info(
                            f"Created limitation | aircraft={aircraft_id} | "
                            f"category={lim['category'].value} | text={lim['limitation_text'][:50]}..."
                        )
                    else:
                        logger.debug(f"Limitation already exists | {lim['limitation_text'][:50]}...")
                        
                except Exception as e:
                    logger.error(f"Failed to create limitation: {e}")
            
            return created_limitations
            
        except Exception as e:
            logger.error(f"Limitation Detector error for report {report_id}: {e}")
            return created_limitations
    
    async def get_aircraft_limitations(
        self,
        aircraft_id: str,
        user_id: str,
        category: Optional[LimitationCategory] = None,
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        """
        Get all limitations for an aircraft.
        
        Args:
            aircraft_id: Aircraft ID
            user_id: User ID
            category: Optional category filter
            limit: Max results
        
        Returns:
            List of limitation documents
        """
        query = {
            "aircraft_id": aircraft_id,
            "user_id": user_id
        }
        
        if category:
            query["category"] = category.value
        
        cursor = self.db.operational_limitations.find(
            query,
            {"_id": 0}  # Exclude _id for JSON serialization
        ).sort("created_at", -1).limit(limit)
        
        limitations = []
        async for doc in cursor:
            # Convert datetime to string for JSON
            if doc.get("report_date"):
                doc["report_date"] = doc["report_date"].isoformat()
            if doc.get("created_at"):
                doc["created_at"] = doc["created_at"].isoformat()
            limitations.append(doc)
        
        return limitations
