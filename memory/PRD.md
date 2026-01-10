# AeroLogix AI - Product Requirements Document

## Overview
AeroLogix AI is a backend aviation maintenance management system with AI-powered document processing.

## Tech Stack
- **Backend**: FastAPI (Python 3.11)
- **Database**: MongoDB (motor async driver)
- **Authentication**: JWT
- **Payments**: Stripe
- **AI/OCR**: OpenAI GPT-4.1-mini (Vision API)

## Core Features

### 1. User Management
- ✅ JWT-based authentication (signup/login)
- ✅ User profile management
- ✅ GDPR-compliant account deletion (`DELETE /api/users/me`)
- ✅ Plan-based limits (BASIC, PILOT, PILOT_PRO, FLEET)

### 2. Aircraft Management
- ✅ CRUD operations for aircraft
- ✅ Aircraft hours tracking (airframe, engine, propeller)
- ✅ Plan-based aircraft limits

### 3. OCR Document Processing
- ✅ Maintenance report scanning
- ✅ Invoice scanning
- ✅ STC document scanning
- ✅ Automatic data extraction
- ✅ Deduplication on apply

### 4. Critical Components Tracking (Jan 2026)
- ✅ `installed_components` MongoDB collection
- ✅ Component extraction from OCR reports (keywords: engine, propeller, magneto, vacuum pump, overhaul, replaced)
- ✅ `GET /api/components/critical/{aircraft_id}` - Returns component lifecycle data
- ✅ Time calculations: `time_since_install`, `remaining` (TBO-based)
- ✅ Status: OK, WARNING (<100h), CRITICAL (<50h), OVERDUE (<=0)
- ✅ `POST /api/components/critical/{aircraft_id}/reprocess` - Reprocess OCR history
- ✅ MongoDB unique index on (aircraft_id, component_type, part_no, installed_at_hours)

### 5. TEA Operational Limitations Detection (Jan 2026)
- ✅ `operational_limitations` MongoDB collection
- ✅ Pattern-based detection from OCR reports:
  - **ELT**: 25 NM, LIMITED TO 25, ELT REMOVED/EXPIRED/BATTERY EXPIRED
  - **AVIONICS**: CONTROL ZONE, CONTROLLED AIRSPACE, PITOT, TRANSPONDER, MUST BE DONE BEFORE
  - **GENERAL**: ON CONDITION, OVERDUE, NOT SERVICEABLE, RESTRICTED, LIMITED, GROUNDED
- ✅ `GET /api/limitations/{aircraft_id}` - Returns all limitations
- ✅ `GET /api/limitations/{aircraft_id}?category=ELT` - Filter by category
- ✅ `GET /api/limitations/{aircraft_id}/summary` - Counts by category
- ✅ `DELETE /api/limitations/{aircraft_id}/{limitation_id}` - Remove limitation
- ✅ Stores RAW TEXT as written - NEVER transforms to status or calculates compliance
- ✅ MongoDB unique index on (aircraft_id, report_id, limitation_text)

### 6. OCR Mode Isolation (NEW - Jan 2026)
**INVOICE Mode** (document_type = "invoice"):
- ❌ DISABLED: Parts extraction → `part_records`
- ❌ DISABLED: Components extraction → `installed_components`
- ❌ DISABLED: AD/SB extraction
- ❌ DISABLED: Aircraft hours updates
- ✅ ONLY: Financial data extraction → `invoices` collection
- ✅ Output schema:
  ```json
  {
    "vendor_name": "string",
    "invoice_number": "string",
    "invoice_date": "datetime",
    "subtotal": "number",
    "tax": "number",
    "total": "number",
    "currency": "CAD | USD",
    "line_items": [{ "description", "part_number", "quantity", "unit_price", "line_total" }]
  }
  ```
- ✅ Response includes `mode: "INVOICE"` field

**REPORT Mode** (document_type = "report"):
- ✅ Full technical extraction (parts, hours, AD/SB, limitations, components)
- ✅ Response includes `mode: "REPORT"` field

### 7. Transport Canada Integration
- ✅ TC Aircraft Registry import (~35,000 records)
- ✅ Lookup API (`GET /api/tc/lookup`)
- ✅ Search API (`GET /api/tc/search`)

### 7. AD/SB Comparison Engine
- ✅ Compare aircraft records against TC directives
- ✅ Compliance status: FOUND, MISSING, DUE_SOON
- ⚠️ Currently using seeded sample data (not full TC AD/SB dataset)

### 8. Stripe Payments
- ✅ Checkout session creation
- ✅ Webhook handling
- ✅ Plan management (BASIC → PILOT → PILOT_PRO → FLEET)

## API Endpoints Summary

### Authentication
- `POST /api/auth/signup` - Create account
- `POST /api/auth/login` - OAuth2 form login
- `GET /api/auth/me` - Current user profile
- `DELETE /api/users/me` - Delete account (GDPR)

### Aircraft
- `GET /api/aircraft` - List user's aircraft
- `POST /api/aircraft` - Create aircraft
- `GET /api/aircraft/{id}` - Get aircraft details
- `PUT /api/aircraft/{id}` - Update aircraft
- `DELETE /api/aircraft/{id}` - Delete aircraft

### OCR
- `POST /api/ocr/scan` - Scan document
- `GET /api/ocr/{scan_id}` - Get scan results
- `POST /api/ocr/apply/{scan_id}` - Apply OCR data (creates components + limitations)
- `GET /api/ocr/check-duplicates/{scan_id}` - Check for duplicates

### Critical Components
- `GET /api/components/critical/{aircraft_id}` - Get component lifecycle data
- `POST /api/components/critical/{aircraft_id}/reprocess` - Reprocess OCR history

### Operational Limitations (NEW)
- `GET /api/limitations/{aircraft_id}` - Get all limitations
- `GET /api/limitations/{aircraft_id}?category=ELT` - Filter by category
- `GET /api/limitations/{aircraft_id}/summary` - Counts by category
- `DELETE /api/limitations/{aircraft_id}/{limitation_id}` - Remove limitation

### Transport Canada
- `GET /api/tc/lookup?registration={reg}` - Lookup aircraft in TC registry
- `GET /api/tc/search?q={query}` - Search TC registry

### AD/SB
- `GET /api/adsb/compare/{aircraft_id}` - Compare against TC directives

## Database Collections

### users
```json
{
  "_id": "string",
  "email": "string",
  "hashed_password": "string",
  "name": "string",
  "subscription": { "plan_code": "BASIC|PILOT|PILOT_PRO|FLEET", "status": "string" },
  "limits": { "max_aircrafts": "number", "ocr_per_month": "number" }
}
```

### aircrafts
```json
{
  "_id": "string",
  "user_id": "string",
  "registration": "string",
  "airframe_hours": "number",
  "engine_hours": "number",
  "propeller_hours": "number"
}
```

### installed_components
```json
{
  "_id": "ObjectId",
  "aircraft_id": "string",
  "user_id": "string",
  "component_type": "ENGINE|PROP|MAGNETO|VACUUM_PUMP|LLP|STARTER|ALTERNATOR",
  "part_no": "string",
  "serial_no": "string (optional)",
  "description": "string",
  "installed_at_hours": "number",
  "installed_date": "datetime",
  "tbo": "number (optional)",
  "source_report_id": "string",
  "confidence": "number 0..1",
  "created_at": "datetime",
  "updated_at": "datetime"
}
```
**Unique Index**: (aircraft_id, component_type, part_no, installed_at_hours)

### operational_limitations (NEW)
```json
{
  "_id": "ObjectId",
  "aircraft_id": "string",
  "user_id": "string",
  "report_id": "string",
  "limitation_text": "string (raw text as written by TEA)",
  "detected_keywords": ["string"],
  "category": "ELT|AVIONICS|PROPELLER|ENGINE|AIRFRAME|GENERAL",
  "confidence": "number 0..1",
  "source": "OCR",
  "report_date": "datetime",
  "created_at": "datetime"
}
```
**Unique Index**: (aircraft_id, report_id, limitation_text)

### TC_Aeronefs
```json
{
  "_id": "string (registration)",
  "manufacturer": "string",
  "model": "string",
  "tc_version": "number"
}
```

## Completed Work (Jan 2026)

### Session 1 (Jan 10, 2026)
- ✅ User account deletion endpoint
- ✅ Stripe integration overhaul (unified plan_codes)
- ✅ TC Aircraft Registry import (35,000 records)
- ✅ TC Lookup/Search API
- ✅ AD/SB Comparison Engine
- ✅ **Mission Critical Components Feature** - OCR Intelligence for component extraction
- ✅ **TEA Operational Limitations Detection** - Pattern-based limitation extraction

## Upcoming Tasks (P1)
- Advanced OCR Candidate Extraction (date_candidates, registration_candidates, keyword_hits)

## Future Tasks (P2-P3)
- Automate TC Registry import (quarterly)
- Import real TC AD/SB data
- Generic TC Search endpoint enhancement

## Known Limitations
- AD/SB engine uses sample seeded data, not full TC dataset
- OCR component detection relies on keyword matching (may miss some patterns)
- Limitation detection is pattern-based (may have false positives/negatives)
- No frontend - backend only application
