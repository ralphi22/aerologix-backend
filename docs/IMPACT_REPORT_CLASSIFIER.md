# IMPACT REPORT: Report Type Classifier Implementation

## Date: 2026-01-05
## Author: Backend Agent

---

## 1. CURRENT PIPELINE ANALYSIS

### 1.1 OCR Text Flow
```
POST /api/ocr/scan
    ├── ocr_service.analyze_image(image_base64, document_type)
    │   ├── OpenAI Vision API call
    │   ├── _clean_json_response()
    │   ├── _normalize_ocr_keys() → FR→EN mapping
    │   ├── _normalize_parts() → ensures parts/parts_replaced exist
    │   └── _transform_to_standard_format() → structured output
    └── Store in MongoDB: ocr_scans collection
```

### 1.2 Current Output Schema (ExtractedMaintenanceData)
```python
class ExtractedMaintenanceData(BaseModel):
    date: Optional[str]
    ame_name: Optional[str]
    amo_name: Optional[str]
    ame_license: Optional[str]
    work_order_number: Optional[str]
    description: Optional[str]
    airframe_hours: Optional[float]
    engine_hours: Optional[float]
    propeller_hours: Optional[float]
    remarks: Optional[str]
    labor_cost: Optional[float]
    parts_cost: Optional[float]
    total_cost: Optional[float]
    ad_sb_references: List[ExtractedADSB]
    parts_replaced: List[ExtractedPart]
    stc_references: List[ExtractedSTC]
    elt_data: Optional[ExtractedELTData]
```

### 1.3 Where raw_text is Available
- `ocr_service.analyze_image()` returns `raw_text` in response
- Stored in `ocr_scans.raw_text` field
- Available during `/api/ocr/scan` processing

---

## 2. PROPOSED CHANGES

### 2.1 New Files to Create
| File | Purpose |
|------|---------|
| `/app/services/report_classifier.py` | Core classification logic |

### 2.2 Files to Modify
| File | Changes |
|------|---------|
| `/app/models/ocr_scan.py` | Add optional `report_classification` field |
| `/app/services/ocr_service.py` | Call classifier after OCR extraction |
| `/app/routes/ocr.py` | Pass classification to response (no breaking change) |

### 2.3 New Fields Added to OCR Response
```python
# NEW: Optional field in ExtractedMaintenanceData
class ReportClassification(BaseModel):
    suggested_report_type: str  # Enum value
    confidence: float  # 0.0-1.0
    evidence: List[Dict[str, str]]  # [{"pattern": "...", "snippet": "..."}]
    secondary_candidates: List[Dict[str, Any]]  # [{"type": "...", "score": ...}]
    warnings: List[str] = []

# Added to ExtractedMaintenanceData:
report_classification: Optional[ReportClassification] = None
```

### 2.4 Database Changes
**NONE** - Only additive optional fields in response schema.
Older OCR records will load normally (missing field = None).

---

## 3. RISK ASSESSMENT

### 3.1 Compatibility Risk: LOW
- All new fields are Optional with defaults
- No existing field renamed or removed
- No DB schema change required
- Frontend can ignore new fields until ready

### 3.2 Performance Risk: LOW
- Classification is O(n) regex scan over text
- Typical report text: 500-5000 chars
- Estimated latency: <10ms per classification
- No external API calls

### 3.3 False Positive Risk: MEDIUM
- Mitigated by:
  - Confidence scoring (0-1)
  - Evidence trail for debugging
  - TC-SAFE: User must confirm, no auto-decisions
  - Multiple candidates shown with scores

---

## 4. REPORT TYPES & PATTERNS

### 4.1 High-Confidence Anchors (score: 10)
| Type | Patterns (EN) | Patterns (FR) |
|------|---------------|---------------|
| INSPECTION_APP_B | `625 APPENDIX B`, `STD 625 APP B`, `ANNUAL INSPECTION` | `625 APPENDICE B`, `INSPECTION ANNUELLE` |
| ELEMENTARY_WORK_APP_C | `625 APPENDIX C`, `STD 625 APP C`, `ELEMENTARY WORK` | `625 APPENDICE C`, `TRAVAUX ÉLÉMENTAIRES` |
| AVIONICS_24_MONTH | `571.10`, `605.35`, `STD 571 APP F`, `24 MONTH`, `PITOT-STATIC`, `ALTIMETER TEST` | `24 MOIS`, `ALTIMÈTRE`, `TRANSPONDEUR` |
| ELT_INSPECTION | `605.38`, `STD 571 APP G`, `ELT INSPECTION`, `ELT OPERATIONAL TEST` | `BALISE DE DÉTRESSE`, `INSPECTION ELT` |
| COMPASS_SWING | `COMPASS SWING`, `DEVIATION CARD`, `MAGNETIC COMPASS` | `COMPENSATION COMPAS`, `CARTE DE DÉVIATION` |
| WEIGHT_AND_BALANCE | `WEIGHT AND BALANCE`, `EMPTY WEIGHT`, `C.G.`, `WEIGHING` | `MASSE ET CENTRAGE`, `PESÉE` |
| STC_MODIFICATION | `INSTALLED IN ACCORDANCE WITH STC`, `STC SA`, `STC ST` | `INSTALLÉ SELON STC` |
| REPAIR | `MAJOR REPAIR`, `MINOR REPAIR`, `STRUCTURAL REPAIR`, `APPROVED DATA` | `RÉPARATION MAJEURE`, `RÉPARATION MINEURE` |
| COMPONENT_OVERHAUL | `OVERHAUL`, `TSO`, `SINCE OVERHAUL`, `LIFE LIMITED`, `LLP` | `RÉVISION`, `DEPUIS RÉVISION` |

### 4.2 Lower-Confidence Keywords (score: 3-5)
- `annual`, `inspection`, `static`, `transponder`, `battery expiry`
- Generic maintenance terms provide context but not classification alone

---

## 5. IMPLEMENTATION PLAN

### Step 1: Create classifier module
- Pure Python, no external deps
- Rule-based scoring with regex
- Bilingual support built-in

### Step 2: Add Pydantic models
- ReportClassification model
- Add to ExtractedMaintenanceData as Optional

### Step 3: Integrate into OCR service
- Call classify_report_type(raw_text) after OCR
- Attach result to extracted_data

### Step 4: Unit tests
- 10+ test cases with expected outputs
- Mix EN/FR, noisy OCR

---

## 6. TESTING CHECKLIST

### Unit Tests
- [ ] INSPECTION_APP_B detection (EN)
- [ ] INSPECTION_APP_B detection (FR)
- [ ] AVIONICS_24_MONTH detection
- [ ] ELT_INSPECTION detection
- [ ] COMPASS_SWING detection
- [ ] WEIGHT_AND_BALANCE detection
- [ ] STC_MODIFICATION detection
- [ ] REPAIR detection
- [ ] COMPONENT_OVERHAUL detection
- [ ] UNKNOWN fallback
- [ ] Mixed patterns → secondary candidates
- [ ] Noisy OCR text handling

### Manual Verification
- [ ] Check logs for classification output
- [ ] Verify confidence scores are reasonable
- [ ] Verify evidence snippets are <120 chars
- [ ] Verify old OCR records still load
- [ ] Verify frontend receives new fields (optional)

---

## 7. APPROVAL

**Ready to implement after review.**

- No breaking changes to API contracts
- No DB schema modifications
- Pure additive optional fields
- TC-SAFE: Suggestion only, requires user confirmation
