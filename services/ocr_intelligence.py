"""
OCR Intelligence Service

Extracts critical component information from OCR reports.
Creates installed_components records when components are detected.

Detection targets:
- Engine overhaul/replacement
- Propeller overhaul/replacement
- Magneto replacement
- Vacuum pump replacement
- Life Limited Parts (LLP)
"""

import re
import logging
from datetime import datetime
from typing import Dict, List, Optional, Any, Tuple
from motor.motor_asyncio import AsyncIOMotorDatabase

from models.installed_components import (
    ComponentType, DEFAULT_TBO, InstalledComponentCreate
)

logger = logging.getLogger(__name__)


# ============================================================
# DETECTION PATTERNS
# ============================================================

# Keywords that indicate a component was installed/replaced/overhauled
ACTION_KEYWORDS = [
    r'\binstalled\b',
    r'\breplaced\b',
    r'\boverhaul(?:ed)?\b',
    r'\bo/h\b',
    r'\boh\b',
    r'\bnew\b',
    r'\brebuilt\b',
    r'\bexchanged?\b',
    r'\bremoved\s+and\s+replaced\b',
    r'\br\s*[&/]\s*r\b',  # R&R or R/R
]

# Component detection patterns with confidence weights
COMPONENT_PATTERNS = {
    ComponentType.ENGINE: {
        "patterns": [
            (r'\bengine\s+overhaul', 0.95),
            (r'\bengine\s+o/h', 0.95),
            (r'\bengine\s+oh\b', 0.90),
            (r'\bengine\s+replaced', 0.90),
            (r'\bnew\s+engine', 0.85),
            (r'\bengine\s+rebuilt', 0.85),
            (r'\brebuilt\s+engine', 0.85),
            (r'\bsmoh\b', 0.90),  # Since Major Overhaul
            (r'\bstoh\b', 0.90),  # Since Top Overhaul
            (r'\btsmoh\b', 0.90),  # Time Since Major Overhaul
            (r'\bsfoh\b', 0.85),  # Since Factory Overhaul
            (r'\bo-\d{3}', 0.70),  # Lycoming O-320, etc.
            (r'\bio-\d{3}', 0.70),  # IO-360, etc.
        ],
        "part_patterns": [
            r'(?:lycoming|continental|engine)\s*(?:model\s*)?([A-Z]{1,3}O?-\d{3}[A-Z0-9-]*)',
            r'([A-Z]{1,2}O-\d{3}[A-Z0-9-]*)',
        ]
    },
    ComponentType.PROP: {
        "patterns": [
            (r'\bprop(?:eller)?\s+overhaul', 0.95),
            (r'\bprop(?:eller)?\s+o/h', 0.95),
            (r'\bprop(?:eller)?\s+replaced', 0.90),
            (r'\bnew\s+prop(?:eller)?', 0.85),
            (r'\bprop(?:eller)?\s+rebuilt', 0.85),
            (r'\bpropeller\s+5\s*year', 0.90),
            (r'\b5\s*year\s+prop', 0.90),
            (r'\btspoh\b', 0.85),  # Time Since Prop Overhaul
        ],
        "part_patterns": [
            r'(?:hartzell|mccauley|sensenich|mt)\s*(?:prop)?\s*([A-Z0-9-]{5,})',
            r'prop(?:eller)?\s*(?:p/n|pn|part)?\s*[:#]?\s*([A-Z0-9-]{5,})',
        ]
    },
    ComponentType.MAGNETO: {
        "patterns": [
            (r'\bmag(?:neto)?s?\s+replaced', 0.90),
            (r'\bmag(?:neto)?s?\s+overhaul', 0.90),
            (r'\bmag(?:neto)?s?\s+o/h', 0.90),
            (r'\bnew\s+mag(?:neto)?s?', 0.85),
            (r'\bmag(?:neto)?s?\s+500\s*h(?:ou)?rs?', 0.95),
            (r'\bslick\s+mag', 0.80),
            (r'\bbendix\s+mag', 0.80),
            (r'\bimpulse\s+coupling', 0.75),
        ],
        "part_patterns": [
            r'(?:slick|bendix)\s*([A-Z0-9-]{4,})',
            r'mag(?:neto)?\s*(?:p/n|pn|part)?\s*[:#]?\s*([A-Z0-9-]{4,})',
        ]
    },
    ComponentType.VACUUM_PUMP: {
        "patterns": [
            (r'\bvacuum\s+pump\s+replaced', 0.95),
            (r'\bvacuum\s+pump\s+installed', 0.95),
            (r'\bnew\s+vacuum\s+pump', 0.90),
            (r'\bvac(?:uum)?\s+pump', 0.75),
            (r'\bdry\s+air\s+pump', 0.85),
            (r'\brapco\s+pump', 0.80),
            (r'\btempes[ct]\s+pump', 0.80),
        ],
        "part_patterns": [
            r'(?:rapco|tempest)\s*([A-Z0-9-]{4,})',
            r'vacuum\s*pump\s*(?:p/n|pn|part)?\s*[:#]?\s*([A-Z0-9-]{4,})',
        ]
    },
    ComponentType.STARTER: {
        "patterns": [
            (r'\bstarter\s+replaced', 0.90),
            (r'\bstarter\s+installed', 0.90),
            (r'\bnew\s+starter', 0.85),
            (r'\bstarter\s+overhaul', 0.85),
        ],
        "part_patterns": [
            r'starter\s*(?:p/n|pn|part)?\s*[:#]?\s*([A-Z0-9-]{4,})',
        ]
    },
    ComponentType.ALTERNATOR: {
        "patterns": [
            (r'\balternator\s+replaced', 0.90),
            (r'\balternator\s+installed', 0.90),
            (r'\bnew\s+alternator', 0.85),
            (r'\bgenerator\s+replaced', 0.85),
        ],
        "part_patterns": [
            r'alternator\s*(?:p/n|pn|part)?\s*[:#]?\s*([A-Z0-9-]{4,})',
        ]
    },
    ComponentType.LLP: {
        "patterns": [
            (r'\bllp\b', 0.90),
            (r'\blife\s+limit(?:ed)?\s+part', 0.90),
            (r'\bcylinder\s+replaced', 0.80),
            (r'\bcam(?:shaft)?\s+replaced', 0.85),
            (r'\bcrankshaft\s+replaced', 0.90),
        ],
        "part_patterns": [
            r'(?:cylinder|cam|crank)\s*(?:p/n|pn|part)?\s*[:#]?\s*([A-Z0-9-]{4,})',
        ]
    },
}


class OCRIntelligenceService:
    """
    Extracts component installation data from OCR reports.
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
    
    def _extract_part_number(
        self, 
        text: str, 
        patterns: List[str]
    ) -> Tuple[Optional[str], float]:
        """
        Extract part number from text using patterns.
        Returns (part_no, confidence)
        """
        text_lower = text.lower()
        
        for pattern in patterns:
            match = re.search(pattern, text_lower, re.IGNORECASE)
            if match:
                part_no = match.group(1).upper()
                # Clean up part number
                part_no = re.sub(r'^[^A-Z0-9]+|[^A-Z0-9]+$', '', part_no)
                if len(part_no) >= 3:
                    return part_no, 0.8
        
        return None, 0.0
    
    def _detect_components(
        self, 
        text: str,
        description: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Detect components mentioned in text.
        
        Returns list of detected components with confidence.
        """
        detected = []
        
        # Combine text sources
        full_text = f"{text or ''} {description or ''}"
        normalized = self._normalize_text(full_text)
        
        if not normalized:
            return detected
        
        # Check for action keywords first
        has_action = any(
            re.search(kw, normalized) 
            for kw in ACTION_KEYWORDS
        )
        
        for comp_type, config in COMPONENT_PATTERNS.items():
            best_confidence = 0.0
            part_no = "UNKNOWN"
            
            # Check component patterns
            for pattern, conf in config["patterns"]:
                if re.search(pattern, normalized):
                    # Boost confidence if action keyword present
                    effective_conf = conf if has_action else conf * 0.7
                    if effective_conf > best_confidence:
                        best_confidence = effective_conf
            
            if best_confidence > 0.5:  # Minimum threshold
                # Try to extract part number
                pn, pn_conf = self._extract_part_number(
                    full_text, 
                    config.get("part_patterns", [])
                )
                if pn:
                    part_no = pn
                    best_confidence = max(best_confidence, pn_conf)
                
                detected.append({
                    "component_type": comp_type,
                    "part_no": part_no,
                    "confidence": round(best_confidence, 2),
                    "source_text": full_text[:200] if full_text else None
                })
        
        return detected
    
    async def process_ocr_report(
        self,
        aircraft_id: str,
        user_id: str,
        scan_id: str,
        extracted_data: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """
        Process an OCR report and extract component installations.
        
        Args:
            aircraft_id: Aircraft ID
            user_id: User ID (for authorization)
            scan_id: OCR scan ID
            extracted_data: Extracted data from OCR
        
        Returns:
            List of created component records
        """
        created_components = []
        
        try:
            # Extract report date and hours
            report_date_str = extracted_data.get("date")
            airframe_hours = extracted_data.get("airframe_hours")
            
            # Parse report date
            report_date = None
            if report_date_str:
                try:
                    report_date = datetime.strptime(report_date_str, "%Y-%m-%d")
                except:
                    try:
                        report_date = datetime.strptime(report_date_str, "%Y/%m/%d")
                    except:
                        pass
            
            if airframe_hours is None:
                logger.warning(f"No airframe_hours in OCR report {scan_id}, skipping component extraction")
                return created_components
            
            # Build text to analyze
            text_parts = []
            
            # Description/work performed
            if extracted_data.get("description"):
                text_parts.append(extracted_data["description"])
            if extracted_data.get("work_performed"):
                text_parts.append(extracted_data["work_performed"])
            if extracted_data.get("remarks"):
                text_parts.append(extracted_data["remarks"])
            
            # Parts replaced
            parts_replaced = extracted_data.get("parts_replaced", [])
            for part in parts_replaced:
                if isinstance(part, dict):
                    if part.get("name"):
                        text_parts.append(part["name"])
                    if part.get("description"):
                        text_parts.append(part["description"])
            
            full_text = " ".join(text_parts)
            
            # Detect components
            detected = self._detect_components(full_text)
            
            logger.info(f"OCR Intelligence | scan={scan_id} | detected={len(detected)} components")
            
            # Create component records
            now = datetime.utcnow()
            
            for comp in detected:
                comp_type = comp["component_type"]
                part_no = comp["part_no"]
                confidence = comp["confidence"]
                
                # Build document
                doc = {
                    "aircraft_id": aircraft_id,
                    "user_id": user_id,
                    "component_type": comp_type.value,
                    "part_no": part_no,
                    "description": comp.get("source_text", "")[:200],
                    "installed_at_hours": float(airframe_hours),
                    "installed_date": report_date,
                    "tbo": DEFAULT_TBO.get(comp_type),
                    "source_report_id": scan_id,
                    "confidence": confidence,
                    "created_at": now,
                    "updated_at": now,
                }
                
                # Upsert to avoid duplicates
                try:
                    result = await self.db.installed_components.update_one(
                        {
                            "aircraft_id": aircraft_id,
                            "component_type": comp_type.value,
                            "part_no": part_no,
                            "installed_at_hours": float(airframe_hours),
                        },
                        {"$set": doc},
                        upsert=True
                    )
                    
                    if result.upserted_id:
                        doc["_id"] = str(result.upserted_id)
                        created_components.append(doc)
                        logger.info(
                            f"Created component | aircraft={aircraft_id} | "
                            f"type={comp_type.value} | part={part_no} | hours={airframe_hours}"
                        )
                    else:
                        logger.debug(f"Component already exists | {comp_type.value} at {airframe_hours}h")
                        
                except Exception as e:
                    logger.error(f"Failed to create component: {e}")
            
            return created_components
            
        except Exception as e:
            logger.error(f"OCR Intelligence error for scan {scan_id}: {e}")
            return created_components
    
    async def reprocess_aircraft_history(
        self,
        aircraft_id: str,
        user_id: str
    ) -> int:
        """
        Reprocess all OCR history for an aircraft to extract components.
        
        Returns number of components created.
        """
        total_created = 0
        
        # Get all completed/applied scans
        cursor = self.db.ocr_scans.find({
            "aircraft_id": aircraft_id,
            "user_id": user_id,
            "status": {"$in": ["COMPLETED", "APPLIED"]}
        })
        
        async for scan in cursor:
            extracted_data = scan.get("extracted_data", {})
            if isinstance(extracted_data, dict):
                created = await self.process_ocr_report(
                    aircraft_id=aircraft_id,
                    user_id=user_id,
                    scan_id=str(scan.get("_id")),
                    extracted_data=extracted_data
                )
                total_created += len(created)
        
        return total_created
