# AeroLogix AI - TC AD/SB Monthly Detection Mechanism

## Problem Statement
Implement a monthly backend mechanism to detect newly published Transport Canada AD/SB applicable to each aircraft and expose a simple alert flag for the frontend. This mechanism must be informational only and TC-safe.

## Architecture

### Data Flow
1. Monthly TC AD/SB data import (manual CSV/JSON)
2. Detection service compares TC AD/SB refs vs aircraft designators
3. New items → set `adsb_has_new_tc_items = true` on aircraft
4. User views AD/SB module → clear alert + audit log

### Collections
- `aircrafts` - Extended with alert fields
- `tc_ad` / `tc_sb` - TC AD/SB regulatory items
- `tc_aircraft` - TC Registry for designator lookup
- `tc_adsb_audit_log` - Audit trail for all detection events

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

## What's Been Implemented (2026-01-19)
- **Models**: `tc_adsb_alert.py` - Alert and audit models
- **Service**: `tc_adsb_detection_service.py` - Detection logic
- **Routes**: `tc_adsb_detection.py` - API endpoints
- **Integration**: Auto-clear in structured AD/SB endpoint
- **Aircraft Model Extension**: Alert fields added

### API Endpoints
| Endpoint | Method | Auth | Description |
|----------|--------|------|-------------|
| `/api/tc-adsb/detect` | POST | Yes | Trigger detection for user's aircraft |
| `/api/tc-adsb/detect-all` | POST | Yes | System-wide detection (admin) |
| `/api/tc-adsb/detect-scheduled` | POST | No* | Monthly cron endpoint |
| `/api/tc-adsb/alert/{id}` | GET | Yes | Get alert status |
| `/api/tc-adsb/mark-reviewed/{id}` | POST | Yes | Clear alert |
| `/api/tc-adsb/audit-log` | GET | Yes | View audit trail |
| `/api/tc-adsb/version` | GET | No | Current TC version |

*scheduled endpoint accepts API key for cron authentication

## Prioritized Backlog

### P0 (Done)
- [x] Detection service with designator-based lookup
- [x] Alert flag management
- [x] Auto-clear on module view
- [x] Audit logging

### P1 (Future)
- [ ] Render cron job configuration for monthly trigger
- [ ] Email notification option (optional, user-controlled)
- [ ] Dashboard widget for alert count

### P2 (Enhancements)
- [ ] Filter audit log by date range
- [ ] Export audit log to CSV
- [ ] Admin dashboard for detection status

## Test Coverage
- All 27 backend tests passed (100%)
- Test user: test@aerologix.ca
- Test aircraft: C-FGSO (Cessna 152)

## TC-Safety Compliance
- NO compliance decisions made
- Only factual "new TC publication exists"
- All events auditable
- Clear disclaimer on all responses
