"""
Report Type Classifier for AeroLogix AI OCR

TC-SAFE: This module ONLY SUGGESTS report types with confidence scores.
It does NOT make airworthiness or compliance decisions.
All suggestions MUST be confirmed by the user in the app.

Supports bilingual text (English/French) common in Canadian aviation.
"""

import re
import logging
from typing import List, Dict, Any, Tuple, Optional
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)


class ReportType(str, Enum):
    """Supported report types for classification"""
    INSPECTION_APP_B = "INSPECTION_APP_B"  # CARS/STD 625 Appendix B
    ELEMENTARY_WORK_APP_C = "ELEMENTARY_WORK_APP_C"  # CARS/STD 625 Appendix C
    AVIONICS_24_MONTH = "AVIONICS_24_MONTH"  # Altimeter/Static/Transponder
    ELT_INSPECTION = "ELT_INSPECTION"  # CARs 605.38 / Std 571 App G
    COMPASS_SWING = "COMPASS_SWING"  # Magnetic compass calibration
    WEIGHT_AND_BALANCE = "WEIGHT_AND_BALANCE"  # Aircraft weighing
    STC_MODIFICATION = "STC_MODIFICATION"  # STC installation
    REPAIR = "REPAIR"  # Major/minor repairs
    COMPONENT_OVERHAUL = "COMPONENT_OVERHAUL"  # TSO, overhaul
    UNKNOWN = "UNKNOWN"  # Fallback


@dataclass
class PatternMatch:
    """A single pattern match with evidence"""
    pattern: str
    snippet: str
    score: int


@dataclass
class ClassificationResult:
    """Result of report type classification"""
    suggested_report_type: str
    confidence: float
    evidence: List[Dict[str, str]]
    secondary_candidates: List[Dict[str, Any]]
    warnings: List[str]
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "suggested_report_type": self.suggested_report_type,
            "confidence": self.confidence,
            "evidence": self.evidence,
            "secondary_candidates": self.secondary_candidates,
            "warnings": self.warnings
        }


# Pattern definitions: (regex_pattern, score, description)
# Higher scores = more definitive patterns
PATTERNS = {
    ReportType.INSPECTION_APP_B: [
        # High confidence - regulatory references
        (r"625\s*APPENDI[XC]\s*B", 10, "CAR 625 Appendix B reference"),
        (r"STD\s*625\s*APP(?:ENDI[XC])?\s*B", 10, "STD 625 App B reference"),
        (r"APPENDI[XC]E?\s*B", 8, "Appendix B reference"),
        # English patterns
        (r"ANNUAL\s+INSPECTION", 8, "Annual inspection mention"),
        (r"PERIODIC\s+INSPECTION", 7, "Periodic inspection"),
        (r"100[\s-]*HOUR\s+INSPECTION", 7, "100-hour inspection"),
        (r"AIRWORTHINESS\s+INSPECTION", 7, "Airworthiness inspection"),
        # French patterns
        (r"INSPECTION\s+ANNUELLE", 8, "Inspection annuelle (FR)"),
        (r"INSPECTION\s+P[ÉE]RIODIQUE", 7, "Inspection périodique (FR)"),
        (r"625\s*APPENDICE\s*B", 10, "CAR 625 Appendice B (FR)"),
    ],
    
    ReportType.ELEMENTARY_WORK_APP_C: [
        (r"625\s*APPENDI[XC]\s*C", 10, "CAR 625 Appendix C reference"),
        (r"STD\s*625\s*APP(?:ENDI[XC])?\s*C", 10, "STD 625 App C reference"),
        (r"APPENDI[XC]E?\s*C", 8, "Appendix C reference"),
        (r"ELEMENTARY\s+WORK", 9, "Elementary work mention"),
        (r"TRAVAUX\s+[ÉE]L[ÉE]MENTAIRES", 9, "Travaux élémentaires (FR)"),
        (r"OWNER[\s-]*MAINTENANCE", 7, "Owner maintenance"),
        (r"ENTRETIEN\s+PROPRI[ÉE]TAIRE", 7, "Entretien propriétaire (FR)"),
    ],
    
    ReportType.AVIONICS_24_MONTH: [
        # Regulatory references (highest confidence)
        (r"571\.10", 10, "CAR 571.10 reference"),
        (r"605\.35", 10, "CAR 605.35 reference"),
        (r"STD\s*571\s*APP(?:ENDI[XC])?\s*F", 10, "STD 571 App F reference"),
        (r"APPENDI[XC]E?\s*F", 7, "Appendix F reference"),
        # Time-based patterns
        (r"24[\s-]*MONTH", 9, "24-month check reference"),
        (r"24[\s-]*MOIS", 9, "24 mois (FR)"),
        (r"BIENNIAL", 7, "Biennial check"),
        # Equipment keywords
        (r"ALTIMETER\s+(?:TEST|CHECK|CALIBRATION)", 8, "Altimeter test"),
        (r"ALTIM[ÈE]TRE", 6, "Altimètre (FR)"),
        (r"STATIC\s+SYSTEM\s+(?:TEST|CHECK)", 8, "Static system test"),
        (r"SYST[ÈE]ME\s+STATIQUE", 6, "Système statique (FR)"),
        (r"TRANSPONDER\s+(?:TEST|CHECK)", 8, "Transponder test"),
        (r"TRANSPONDEUR", 6, "Transpondeur (FR)"),
        (r"PITOT[\s-]*STATIC", 7, "Pitot-static system"),
        (r"ENCODER\s+(?:TEST|CHECK)", 6, "Encoder test"),
        (r"MODE\s*[CS]\s+(?:TEST|CHECK)", 6, "Mode C/S test"),
    ],
    
    ReportType.ELT_INSPECTION: [
        # Regulatory references
        (r"605\.38", 10, "CAR 605.38 reference"),
        (r"STD\s*571\s*APP(?:ENDI[XC])?\s*G", 10, "STD 571 App G reference"),
        (r"APPENDI[XC]E?\s*G", 7, "Appendix G reference"),
        # ELT patterns
        (r"ELT\s+(?:INSPECTION|TEST|CHECK)", 9, "ELT inspection"),
        (r"ELT\s+OPERATIONAL\s+TEST", 9, "ELT operational test"),
        (r"ELT\s+BATTERY", 7, "ELT battery reference"),
        (r"EMERGENCY\s+LOCATOR\s+TRANSMITTER", 8, "ELT full name"),
        (r"BALISE\s+DE\s+D[ÉE]TRESSE", 8, "Balise de détresse (FR)"),
        (r"12[\s-]*MONTH\s+(?:ELT|INSPECTION)", 7, "12-month ELT check"),
        (r"406\s*MHZ", 5, "406 MHz ELT frequency"),
        (r"121\.5\s*MHZ", 4, "121.5 MHz frequency"),
    ],
    
    ReportType.COMPASS_SWING: [
        (r"COMPASS\s+SWING", 10, "Compass swing"),
        (r"COMPENSATION\s+(?:DU\s+)?COMPAS", 10, "Compensation compas (FR)"),
        (r"DEVIATION\s+CARD", 9, "Deviation card"),
        (r"CARTE\s+DE\s+D[ÉE]VIATION", 9, "Carte de déviation (FR)"),
        (r"MAGNETIC\s+COMPASS\s+(?:CALIBRATION|CHECK|TEST)", 8, "Magnetic compass calibration"),
        (r"COMPAS\s+MAGN[ÉE]TIQUE", 6, "Compas magnétique (FR)"),
        (r"HEADING\s+INDICATOR\s+CALIBRATION", 6, "Heading indicator calibration"),
    ],
    
    ReportType.WEIGHT_AND_BALANCE: [
        (r"WEIGHT\s+AND\s+BALANCE", 10, "Weight and balance"),
        (r"MASSE\s+ET\s+CENTRAGE", 10, "Masse et centrage (FR)"),
        (r"AIRCRAFT\s+WEIGHING", 9, "Aircraft weighing"),
        (r"PES[ÉE]E\s+(?:DE\s+L['\s]?)?A[ÉE]RONEF", 9, "Pesée aéronef (FR)"),
        (r"EMPTY\s+WEIGHT", 8, "Empty weight reference"),
        (r"MASSE\s+[ÀA]\s+VIDE", 8, "Masse à vide (FR)"),
        (r"C\.?G\.?\s+(?:CALCULATION|POSITION|LOCATION)", 7, "C.G. reference"),
        (r"CENTRE\s+DE\s+GRAVIT[ÉE]", 7, "Centre de gravité (FR)"),
        (r"DATUM", 5, "Datum reference"),
        (r"ARM\s+(?:CALCULATION|MOMENT)", 5, "Arm/moment calculation"),
    ],
    
    ReportType.STC_MODIFICATION: [
        (r"INSTALLED\s+(?:IN\s+ACCORDANCE\s+WITH|PER|IAW)\s+STC", 10, "STC installation reference"),
        (r"INSTALL[ÉE]\s+(?:SELON|CONFORM[ÉE]MENT\s+AU?)\s+STC", 10, "Installé selon STC (FR)"),
        (r"STC\s+(?:SA|ST|SR)\d{4,}", 9, "STC number pattern"),
        (r"SUPPLEMENTAL\s+TYPE\s+CERTIFICATE", 8, "STC full name"),
        (r"CERTIFICAT\s+DE\s+TYPE\s+SUPPL[ÉE]MENTAIRE", 8, "CTS (FR)"),
        (r"STC\s+INSTALLATION", 7, "STC installation"),
        (r"APPROVED\s+MODIFICATION", 6, "Approved modification"),
        (r"MODIFICATION\s+APPROUV[ÉE]E", 6, "Modification approuvée (FR)"),
    ],
    
    ReportType.REPAIR: [
        (r"MAJOR\s+REPAIR", 10, "Major repair"),
        (r"R[ÉE]PARATION\s+MAJEURE", 10, "Réparation majeure (FR)"),
        (r"MINOR\s+REPAIR", 8, "Minor repair"),
        (r"R[ÉE]PARATION\s+MINEURE", 8, "Réparation mineure (FR)"),
        (r"STRUCTURAL\s+REPAIR", 9, "Structural repair"),
        (r"R[ÉE]PARATION\s+STRUCTURALE", 9, "Réparation structurale (FR)"),
        (r"IN\s+ACCORDANCE\s+WITH\s+(?:APPROVED\s+)?DATA", 7, "Approved data reference"),
        (r"(?:CONFORM[ÉE]MENT\s+AUX?\s+)?DONN[ÉE]ES\s+APPROUV[ÉE]ES", 7, "Données approuvées (FR)"),
        (r"REPAIR\s+(?:SCHEME|PROCEDURE|METHOD)", 6, "Repair procedure"),
        (r"DAMAGE\s+REPAIR", 6, "Damage repair"),
    ],
    
    ReportType.COMPONENT_OVERHAUL: [
        (r"OVERHAUL(?:ED)?", 8, "Overhaul reference"),
        (r"R[ÉE]VISION\s+(?:G[ÉE]N[ÉE]RALE|COMPL[ÈE]TE)?", 8, "Révision (FR)"),
        (r"TSO[\s-]*\d+", 7, "TSO reference number"),
        (r"SINCE\s+(?:LAST\s+)?OVERHAUL", 7, "Since overhaul reference"),
        (r"DEPUIS\s+(?:DERNI[ÈE]RE\s+)?R[ÉE]VISION", 7, "Depuis révision (FR)"),
        (r"LIFE[\s-]*LIMITED\s+PART", 8, "Life limited part"),
        (r"PI[ÈE]CE\s+[ÀA]\s+VIE\s+LIMIT[ÉE]E", 8, "Pièce à vie limitée (FR)"),
        (r"LLP", 6, "LLP abbreviation"),
        (r"TBO", 6, "TBO reference"),
        (r"TIME\s+BETWEEN\s+OVERHAUL", 6, "Time between overhaul"),
        (r"COMPONENT\s+(?:OVERHAUL|REBUILD)", 7, "Component overhaul"),
    ],
}


def normalize_text(text: str) -> str:
    """
    Normalize OCR text for pattern matching.
    - Uppercase for consistent matching
    - Remove excessive whitespace
    - Handle common OCR errors
    """
    if not text:
        return ""
    
    # Uppercase
    normalized = text.upper()
    
    # Replace common OCR confusions
    ocr_fixes = [
        (r"APPENIDX", "APPENDIX"),  # Common OCR error
        (r"APENDIX", "APPENDIX"),
        (r"APPENOIX", "APPENDIX"),
        (r"APPENDICE", "APPENDICE"),  # Keep French
        (r"INSPECTI0N", "INSPECTION"),  # O vs 0
        (r"TRANSF0NDER", "TRANSPONDER"),
        (r"TRANSP0NDER", "TRANSPONDER"),
        (r"ALTlMETER", "ALTIMETER"),  # l vs I
        (r"EI\.T", "ELT"),  # Common confusion
        (r"E\.L\.T", "ELT"),
    ]
    
    for pattern, replacement in ocr_fixes:
        normalized = re.sub(pattern, replacement, normalized)
    
    # Collapse multiple spaces
    normalized = re.sub(r'\s+', ' ', normalized)
    
    return normalized.strip()


def extract_snippet(text: str, match_start: int, match_end: int, max_length: int = 120) -> str:
    """Extract a snippet around a match, max 120 chars"""
    # Calculate context window
    context_before = 30
    context_after = 30
    
    start = max(0, match_start - context_before)
    end = min(len(text), match_end + context_after)
    
    snippet = text[start:end]
    
    # Add ellipsis if truncated
    if start > 0:
        snippet = "..." + snippet
    if end < len(text):
        snippet = snippet + "..."
    
    # Ensure max length
    if len(snippet) > max_length:
        snippet = snippet[:max_length - 3] + "..."
    
    return snippet


def classify_report_type(ocr_text: str) -> ClassificationResult:
    """
    Classify the report type based on OCR text content.
    
    TC-SAFE: Returns SUGGESTION only with confidence and evidence.
    Does NOT make airworthiness or compliance decisions.
    
    Args:
        ocr_text: Raw or normalized OCR text from document
        
    Returns:
        ClassificationResult with suggested type, confidence, and evidence
    """
    if not ocr_text:
        return ClassificationResult(
            suggested_report_type=ReportType.UNKNOWN.value,
            confidence=0.0,
            evidence=[],
            secondary_candidates=[],
            warnings=["No text provided for classification"]
        )
    
    # Normalize text
    normalized = normalize_text(ocr_text)
    
    # Score each report type
    scores: Dict[ReportType, Tuple[int, List[PatternMatch]]] = {}
    
    for report_type, patterns in PATTERNS.items():
        type_score = 0
        type_matches: List[PatternMatch] = []
        
        for pattern, score, description in patterns:
            try:
                for match in re.finditer(pattern, normalized, re.IGNORECASE):
                    type_score += score
                    snippet = extract_snippet(normalized, match.start(), match.end())
                    type_matches.append(PatternMatch(
                        pattern=description,
                        snippet=snippet,
                        score=score
                    ))
            except re.error as e:
                logger.warning(f"Regex error for pattern '{pattern}': {e}")
        
        if type_score > 0:
            scores[report_type] = (type_score, type_matches)
    
    # Sort by score descending
    sorted_types = sorted(scores.items(), key=lambda x: x[1][0], reverse=True)
    
    # Handle no matches
    if not sorted_types:
        return ClassificationResult(
            suggested_report_type=ReportType.UNKNOWN.value,
            confidence=0.0,
            evidence=[],
            secondary_candidates=[],
            warnings=["No matching patterns found in document"]
        )
    
    # Best match
    best_type, (best_score, best_matches) = sorted_types[0]
    
    # Calculate confidence (0-1 scale)
    # Max possible score for a single type is ~50 (multiple high-confidence patterns)
    max_expected_score = 40
    confidence = min(1.0, best_score / max_expected_score)
    
    # Build evidence (top 5 matches)
    evidence = [
        {"pattern": m.pattern, "snippet": m.snippet}
        for m in sorted(best_matches, key=lambda x: x.score, reverse=True)[:5]
    ]
    
    # Secondary candidates (types with score > 5)
    secondary = []
    for report_type, (score, _) in sorted_types[1:]:
        if score >= 5:
            secondary.append({
                "type": report_type.value,
                "score": score,
                "confidence": min(1.0, score / max_expected_score)
            })
    
    # Warnings
    warnings = []
    
    # Low confidence warning
    if confidence < 0.3:
        warnings.append("Low confidence classification - manual review recommended")
    
    # Multiple strong candidates warning
    if len(sorted_types) >= 2:
        second_score = sorted_types[1][1][0]
        if second_score >= best_score * 0.7:
            warnings.append(f"Multiple report types detected with similar confidence")
    
    # Short text warning
    if len(normalized) < 100:
        warnings.append("Limited text available for classification")
    
    result = ClassificationResult(
        suggested_report_type=best_type.value,
        confidence=round(confidence, 3),
        evidence=evidence,
        secondary_candidates=secondary[:3],  # Top 3 alternatives
        warnings=warnings
    )
    
    logger.info(
        f"REPORT CLASSIFICATION | type={best_type.value} | "
        f"confidence={confidence:.2f} | score={best_score} | "
        f"alternatives={len(secondary)}"
    )
    
    return result


# ============== UNIT TESTS ==============

def run_tests():
    """Run unit tests for the classifier"""
    
    test_cases = [
        # Test 1: Clear Appendix B (English)
        {
            "text": "This aircraft underwent annual inspection in accordance with CAR 625 APPENDIX B requirements. All items checked satisfactory.",
            "expected_type": "INSPECTION_APP_B",
            "min_confidence": 0.4
        },
        
        # Test 2: Appendix B (French)
        {
            "text": "Inspection annuelle effectuée selon le RAC 625 Appendice B. Tous les items vérifiés.",
            "expected_type": "INSPECTION_APP_B",
            "min_confidence": 0.4
        },
        
        # Test 3: 24-month avionics check
        {
            "text": "24 MONTH PITOT-STATIC SYSTEM TEST completed per CAR 571.10 and 605.35. Altimeter, static system, transponder Mode C all tested satisfactory.",
            "expected_type": "AVIONICS_24_MONTH",
            "min_confidence": 0.5
        },
        
        # Test 4: ELT Inspection
        {
            "text": "ELT OPERATIONAL TEST performed per CAR 605.38 and STD 571 Appendix G. Battery expiry date: 2027-06-15. 406 MHz beacon functional.",
            "expected_type": "ELT_INSPECTION",
            "min_confidence": 0.5
        },
        
        # Test 5: Compass swing
        {
            "text": "COMPASS SWING performed. New deviation card installed. Magnetic compass calibrated on all cardinal headings.",
            "expected_type": "COMPASS_SWING",
            "min_confidence": 0.4
        },
        
        # Test 6: Weight and balance (French)
        {
            "text": "Pesée de l'aéronef effectuée. Masse à vide: 1234 kg. Centre de gravité calculé. Nouvelle fiche de masse et centrage établie.",
            "expected_type": "WEIGHT_AND_BALANCE",
            "min_confidence": 0.5
        },
        
        # Test 7: STC modification
        {
            "text": "Garmin GTN 750 installed in accordance with STC SA02345NY. All wiring per approved data. Operational check satisfactory.",
            "expected_type": "STC_MODIFICATION",
            "min_confidence": 0.5
        },
        
        # Test 8: Major repair
        {
            "text": "MAJOR REPAIR to right wing leading edge. Structural repair performed in accordance with approved data from manufacturer SRM.",
            "expected_type": "REPAIR",
            "min_confidence": 0.5
        },
        
        # Test 9: Component overhaul
        {
            "text": "Propeller governor overhauled. TSO-C117a. Time since overhaul: 0.0 hours. Life limited parts replaced per CMM.",
            "expected_type": "COMPONENT_OVERHAUL",
            "min_confidence": 0.4
        },
        
        # Test 10: Elementary work (Appendix C)
        {
            "text": "Elementary work performed per CAR 625 APPENDIX C. Owner maintenance - oil change and tire replacement.",
            "expected_type": "ELEMENTARY_WORK_APP_C",
            "min_confidence": 0.5
        },
        
        # Test 11: Noisy OCR with typos
        {
            "text": "ANUAL INSPECTI0N per 625 APPENIDX B completed. All itms checked satisfactory.",
            "expected_type": "INSPECTION_APP_B",
            "min_confidence": 0.3
        },
        
        # Test 12: Mixed bilingual
        {
            "text": "Inspection annuelle / Annual inspection completed. CAR 625 Appendix B / Appendice B. AME signature below.",
            "expected_type": "INSPECTION_APP_B",
            "min_confidence": 0.5
        },
    ]
    
    print("\n" + "="*60)
    print("REPORT TYPE CLASSIFIER - UNIT TESTS")
    print("="*60 + "\n")
    
    passed = 0
    failed = 0
    
    for i, test in enumerate(test_cases, 1):
        result = classify_report_type(test["text"])
        
        type_match = result.suggested_report_type == test["expected_type"]
        conf_match = result.confidence >= test["min_confidence"]
        
        status = "✅ PASS" if (type_match and conf_match) else "❌ FAIL"
        
        if type_match and conf_match:
            passed += 1
        else:
            failed += 1
        
        print(f"Test {i}: {status}")
        print(f"  Expected: {test['expected_type']} (conf >= {test['min_confidence']})")
        print(f"  Got: {result.suggested_report_type} (conf = {result.confidence})")
        if result.evidence:
            print(f"  Evidence: {result.evidence[0]['pattern']}")
        if result.warnings:
            print(f"  Warnings: {result.warnings}")
        print()
    
    print("="*60)
    print(f"RESULTS: {passed}/{passed+failed} tests passed")
    print("="*60 + "\n")
    
    return failed == 0


if __name__ == "__main__":
    run_tests()
