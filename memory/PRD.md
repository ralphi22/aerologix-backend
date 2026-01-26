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

#### Canonical TC Data (READ-ONLY)
- `tc_ad` / `tc_sb` - TC AD/SB regulatory items (canonical, never modified by imports)
- `tc_aircraft` - TC Registry for designator lookup

#### User Import Collections (NEW - Phase 1)
- `tc_pdf_imports` - PDF file metadata
- `tc_imported_references` - Extracted references linked to aircraft

#### Other Collections
- `aircrafts` - User aircraft with alert fields
- `tc_adsb_audit_log` - Audit trail for all events

---

## Phase 1: TC PDF Import (2026-01-25)

### Collections créées

#### Collection A: `tc_pdf_imports`
1 document = 1 PDF TC importé physiquement.

```javascript
{
  _id: ObjectId,
  tc_pdf_id: string,        // UUID v4, UNIQUE INDEX
  filename: string,
  storage_path: string,
  content_type: "application/pdf",
  file_size_bytes: int,
  source: "TRANSPORT_CANADA",
  imported_by: user_id,
  imported_at: datetime
}
```

**Indexes:**
- `tc_pdf_id_unique` (UNIQUE)
- `imported_by_idx`

#### Collection B: `tc_imported_references`
1 document = 1 référence TC liée à un avion et à un PDF.

```javascript
{
  _id: ObjectId,            // ← tc_reference_id canonique (pour DELETE)
  aircraft_id: string,
  identifier: string,       // ex: CF-1987-15R (affichage humain UNIQUEMENT)
  type: "AD" | "SB",
  title: string | null,
  tc_pdf_id: string,        // UUID (pour GET PDF)
  source: "TC_PDF_IMPORT",
  scope: string | null,
  created_by: user_id,
  created_at: datetime
}
```

**Indexes:**
- `aircraft_id_idx`
- `tc_pdf_id_idx`
- `aircraft_identifier_idx`
- `created_by_idx`

### IMPORTANT - Règles d'ID

| Opération | ID à utiliser | Format |
|-----------|---------------|--------|
| DELETE référence | `tc_reference_id` | ObjectId (24-char hex) |
| GET PDF | `tc_pdf_id` | UUID (36-char) |
| Affichage | `identifier` | CF-xxxx (jamais clé DB) |

### API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/adsb/tc/import-pdf/{aircraft_id}` | POST | Upload TC PDF, extract references |
| `/api/adsb/tc/references/{aircraft_id}` | GET | List imported references |
| `/api/adsb/tc/pdf/{tc_pdf_id}` | GET | Download stored PDF |
| `/api/adsb/tc/reference-by-id/{tc_reference_id}` | DELETE | Delete imported reference |
| `/api/adsb/tc/import-history/{aircraft_id}` | GET | View import audit log |

### Files Created/Modified

**New Files:**
- `/app/backend/models/tc_pdf_import.py` - Pydantic models for collections
- `/app/backend/services/tc_pdf_db_service.py` - Database service
- `/app/backend/services/tc_pdf_import_service.py` - Import service (V2)
- `/app/backend/routes/tc_import.py` - API routes (V2)

**Modified Files:**
- `/app/backend/routes/adsb.py` - Baseline reads from `tc_imported_references`

---

## Prioritized Backlog

### P0 (Completed)
- [x] Detection service with designator-based lookup
- [x] Alert flag management
- [x] Auto-clear on module view
- [x] Audit logging
- [x] TC PDF Import collections: `tc_pdf_imports`, `tc_imported_references`
- [x] Stable ID system: `tc_reference_id` (ObjectId) + `tc_pdf_id` (UUID)
- [x] Separation from canonical `tc_ad`/`tc_sb` collections
- [x] Legal pages for Apple compliance

### P1 (Upcoming)
- [ ] Full TC AD/SB Data Ingestion from official source
- [ ] Render cron job for monthly detection
- [ ] Email notifications for new AD/SB

### P2 (Technical Debt)
- [ ] Clean legacy data from `tc_ad`/`tc_sb` (source="TC_PDF_IMPORT")
- [ ] Remove risky vocabulary (`ComparisonStatus` enum)
- [ ] Remove deprecated endpoints

---

## Test Coverage
- Regression tests: `/app/backend/scripts/test_tc_pdf_import.py`
- Test credentials: `test@aerologix.ca` / `password123`
- All PDF import tests passing (import, list, view, delete, baseline)

## TC-Safety Compliance
- NO compliance decisions made
- Only factual "new TC publication exists"
- All events auditable
- Clear disclaimer on all responses
- Data sources strictly separated
