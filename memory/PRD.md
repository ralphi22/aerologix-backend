# AeroLogix AI - Backend PRD

## Problem Statement
AeroLogix AI is a backend system for managing aircraft airworthiness data. The system provides TC-SAFE (Transport Canada Safe) features for:
1. TC AD/SB (Airworthiness Directives / Service Bulletins) detection and tracking
2. PDF import of TC documents with reference extraction
3. User aircraft management with regulatory compliance alerting

## Architecture

### Data Flow
1. Monthly TC AD/SB data import (manual CSV/JSON or PDF upload)
2. Detection service compares TC AD/SB refs vs aircraft designators
3. New items → set `adsb_has_new_tc_items = true` on aircraft
4. User views AD/SB module → clear alert + audit log

### Collections
- `aircrafts` - User aircraft with alert fields
- `tc_ad` / `tc_sb` - TC AD/SB regulatory items
- `tc_aircraft` - TC Registry for designator lookup
- `tc_adsb_audit_log` - Audit trail for all events

## User Personas
- **Aircraft Owners**: View alert flag on aircraft dashboard
- **AME/TEA**: Access detailed AD/SB module for compliance work
- **Admin**: Trigger monthly detection, view audit logs

## Core Requirements (Static)
1. ✅ Store last TC review state per aircraft
2. ✅ Detect new AD/SB by comparing against known refs
3. ✅ Create alert flag (`adsb_has_new_tc_items`, `count_new_adsb`)
4. ✅ Clear alert when user views AD/SB module
5. ✅ API exposure of alert flag in aircraft response
6. ✅ Audit logging for TC-safety
7. ✅ PDF import with reference extraction
8. ✅ Stable ID system for frontend integration

---

## What's Been Implemented

### 2026-01-19: TC AD/SB Detection System
- **Models**: `tc_adsb_alert.py` - Alert and audit models
- **Service**: `tc_adsb_detection_service.py` - Detection logic
- **Routes**: `tc_adsb_detection.py` - API endpoints
- **Integration**: Auto-clear in structured AD/SB endpoint
- **Aircraft Model Extension**: Alert fields added

### 2026-01-25: TC PDF Import Feature (Phase 1)
**Critical fix: Stable ID System**
- Fixed `tc_reference_id` to return proper MongoDB ObjectId (24-char hex string)
- Fixed `tc_pdf_id` to return UUID for PDF file identification
- Fixed DELETE endpoint to use ObjectId conversion for MongoDB queries
- Removed duplicate/dead code from `tc_import.py`

**Components:**
- `POST /api/adsb/tc/import-pdf/{aircraft_id}` - Upload TC PDF
- `GET /api/adsb/baseline/{aircraft_id}` - Returns merged canonical + imported data
- `GET /api/adsb/tc/pdf/{tc_pdf_id}` - Download stored PDF
- `DELETE /api/adsb/tc/reference-by-id/{tc_reference_id}` - Delete imported reference

**ID Contract (for Frontend):**
```json
{
  "identifier": "CF-2024-01",
  "origin": "USER_IMPORTED_REFERENCE",
  "tc_reference_id": "6976a4a0031091881c8d6fcd",  // 24-char ObjectId - use for DELETE
  "tc_pdf_id": "ac4c7dcf-a153-473d-a868-73554a838f4c"  // UUID - use for GET PDF
}
```

### Legal Pages (Apple Compliance)
- `GET /privacy` - Returns HTML privacy policy
- `GET /terms` - Returns HTML terms of use

---

## API Endpoints

### TC PDF Import
| Endpoint | Method | Auth | Description |
|----------|--------|------|-------------|
| `/api/adsb/tc/import-pdf/{aircraft_id}` | POST | Yes | Upload TC PDF, extract references |
| `/api/adsb/tc/pdf/{tc_pdf_id}` | GET | Yes | Download stored PDF |
| `/api/adsb/tc/reference-by-id/{tc_reference_id}` | DELETE | Yes | Delete imported reference |
| `/api/adsb/tc/import-history/{aircraft_id}` | GET | Yes | View import audit log |

### TC AD/SB Detection
| Endpoint | Method | Auth | Description |
|----------|--------|------|-------------|
| `/api/adsb/baseline/{aircraft_id}` | GET | Yes | Full AD/SB baseline with OCR history |
| `/api/adsb/structured/{aircraft_id}` | GET | Yes | Structured comparison |
| `/api/adsb/mark-reviewed/{aircraft_id}` | POST | Yes | Clear alert |
| `/api/tc-adsb/detect` | POST | Yes | Trigger detection |
| `/api/tc-adsb/alert/{id}` | GET | Yes | Get alert status |

### Legal (Public)
| Endpoint | Method | Auth | Description |
|----------|--------|------|-------------|
| `/privacy` | GET | No | Privacy policy HTML |
| `/terms` | GET | No | Terms of use HTML |

---

## Prioritized Backlog

### P0 (Completed)
- [x] Detection service with designator-based lookup
- [x] Alert flag management
- [x] Auto-clear on module view
- [x] Audit logging
- [x] TC PDF Import (Phase 1)
- [x] Stable ID system for imported references
- [x] Legal pages for Apple compliance

### P1 (Upcoming)
- [ ] Full TC AD/SB Data Ingestion from official source
- [ ] Render cron job configuration for monthly trigger
- [ ] Email notification option (optional, user-controlled)

### P2 (Future/Technical Debt)
- [ ] Remove risky vocabulary (`ComparisonStatus` enum: DUE_SOON, MISSING, COMPLIED)
- [ ] Remove deprecated `/api/adsb/compare` endpoint
- [ ] Remove deprecated `/api/adsb/{aircraft_id}/summary` endpoint
- [ ] Admin dashboard for detection status
- [ ] Filter/export audit log

---

## Test Coverage
- Regression tests: `/app/backend/scripts/test_tc_pdf_import.py`
- Test credentials: `test@aerologix.ca` / `password123`
- All PDF import tests passing (import, baseline, delete, view)

## TC-Safety Compliance
- NO compliance decisions made
- Only factual "new TC publication exists"
- All events auditable
- Clear disclaimer on all responses
- Data sources clearly separated (TC_BASELINE vs USER_IMPORTED_REFERENCE)

## Key Files
- `/app/backend/routes/tc_import.py` - PDF import endpoints
- `/app/backend/routes/adsb.py` - Baseline and structured endpoints
- `/app/backend/services/tc_pdf_import_service.py` - PDF parsing service
- `/app/backend/models/tc_adsb.py` - Pydantic models
