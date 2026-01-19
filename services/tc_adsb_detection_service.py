"""
TC AD/SB Detection Service

Monthly detection mechanism for newly published Transport Canada AD/SB.

INFORMATIONAL ONLY - TC-SAFE:
- Flag means "new TC publication exists"
- NOT missing, NOT overdue, NOT non-compliant
- No compliance decisions

DATA FLOW:
1. For each aircraft, get designator from TC Registry
2. Query TC AD/SB by designator
3. Compare against previously known refs
4. If new refs found → set alert flag

GUARDRAILS:
- If TC data is incomplete → do nothing
- If aircraft identity is missing → skip
- No silent assumptions
- Log detection events for audit
"""

from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple
from motor.motor_asyncio import AsyncIOMotorDatabase
import logging

from models.tc_adsb_alert import (
    AuditEventType,
    AircraftDetectionResult,
    DetectionSummaryResponse,
    MarkReviewedResponse,
)

logger = logging.getLogger(__name__)


class TCADSBDetectionService:
    """
    Service for detecting newly published TC AD/SB applicable to aircraft.
    
    Runs after monthly TC data import.
    """
    
    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db
    
    # --------------------------------------------------------
    # TC VERSION MANAGEMENT
    # --------------------------------------------------------
    
    async def get_current_tc_version(self) -> Optional[str]:
        """
        Get the current TC AD/SB data version.
        
        Looks for the most recent version marker in tc_ad collection.
        Format: "YYYY-MM" (e.g., "2026-06")
        """
        # Check tc_ad for latest version marker
        latest_ad = await self.db.tc_ad.find_one(
            {"is_active": True},
            sort=[("created_at", -1)]
        )
        
        if latest_ad:
            # Try to extract version from created_at
            created_at = latest_ad.get("created_at")
            if created_at:
                if isinstance(created_at, datetime):
                    return created_at.strftime("%Y-%m")
        
        # Fallback: use current month
        return datetime.now(timezone.utc).strftime("%Y-%m")
    
    # --------------------------------------------------------
    # TC REGISTRY LOOKUP
    # --------------------------------------------------------
    
    async def get_aircraft_designator(self, registration: str) -> Optional[str]:
        """
        Get aircraft designator from TC Registry.
        
        Designator is the key for AD/SB applicability.
        """
        if not registration:
            return None
        
        # Normalize registration
        reg_norm = registration.strip().upper().replace("-", "")
        if not reg_norm.startswith("C"):
            reg_norm = "C" + reg_norm
        
        # Lookup in tc_aircraft
        tc_aircraft = await self.db.tc_aircraft.find_one(
            {"registration_norm": reg_norm}
        )
        
        if tc_aircraft:
            return tc_aircraft.get("designator")
        
        return None
    
    # --------------------------------------------------------
    # TC AD/SB LOOKUP
    # --------------------------------------------------------
    
    async def get_applicable_tc_refs(self, designator: str) -> List[str]:
        """
        Get all applicable TC AD/SB references for a designator.
        
        Returns list of reference identifiers only.
        """
        if not designator:
            return []
        
        refs = []
        
        # Get AD refs
        async for ad in self.db.tc_ad.find(
            {"designator": designator, "is_active": True},
            {"ref": 1, "_id": 0}
        ):
            if ad.get("ref"):
                refs.append(ad["ref"])
        
        # Get SB refs
        async for sb in self.db.tc_sb.find(
            {"designator": designator, "is_active": True},
            {"ref": 1, "_id": 0}
        ):
            if sb.get("ref"):
                refs.append(sb["ref"])
        
        return sorted(refs)
    
    # --------------------------------------------------------
    # DETECTION LOGIC
    # --------------------------------------------------------
    
    async def detect_new_items_for_aircraft(
        self,
        aircraft_id: str,
        user_id: str,
        tc_version: str,
        force: bool = False
    ) -> AircraftDetectionResult:
        """
        Detect new TC AD/SB items for a single aircraft.
        
        Args:
            aircraft_id: Aircraft document ID
            user_id: Owner user ID
            tc_version: Current TC AD/SB version
            force: Force detection even if same version
            
        Returns:
            AircraftDetectionResult with detection status
        """
        # Get aircraft
        aircraft = await self.db.aircrafts.find_one({
            "_id": aircraft_id,
            "user_id": user_id
        })
        
        if not aircraft:
            logger.warning(f"Aircraft not found: {aircraft_id}")
            return AircraftDetectionResult(
                aircraft_id=aircraft_id,
                registration="UNKNOWN",
                new_items_found=False,
                new_items_count=0,
                current_version=tc_version,
                skipped=True,
                skip_reason="Aircraft not found"
            )
        
        registration = aircraft.get("registration", "")
        
        # Check if already processed this version
        previous_version = aircraft.get("last_tc_adsb_version")
        if not force and previous_version == tc_version:
            logger.info(f"Aircraft {registration} already checked for version {tc_version}")
            return AircraftDetectionResult(
                aircraft_id=aircraft_id,
                registration=registration,
                new_items_found=False,
                new_items_count=0,
                previous_version=previous_version,
                current_version=tc_version,
                skipped=True,
                skip_reason=f"Already checked version {tc_version}"
            )
        
        # Get designator from TC Registry
        designator = await self.get_aircraft_designator(registration)
        
        if not designator:
            logger.warning(f"No designator found for aircraft {registration}")
            return AircraftDetectionResult(
                aircraft_id=aircraft_id,
                registration=registration,
                new_items_found=False,
                new_items_count=0,
                previous_version=previous_version,
                current_version=tc_version,
                skipped=True,
                skip_reason="Aircraft identity not found in TC Registry"
            )
        
        # Get current applicable TC refs
        current_refs = await self.get_applicable_tc_refs(designator)
        
        if not current_refs:
            # No TC AD/SB applicable - not necessarily an error
            logger.info(f"No TC AD/SB found for designator {designator}")
            
            # Update aircraft state
            await self.db.aircrafts.update_one(
                {"_id": aircraft_id},
                {"$set": {
                    "last_tc_adsb_version": tc_version,
                    "known_tc_adsb_refs": [],
                    "adsb_has_new_tc_items": False,
                    "count_new_adsb": 0,
                    "updated_at": datetime.now(timezone.utc)
                }}
            )
            
            return AircraftDetectionResult(
                aircraft_id=aircraft_id,
                registration=registration,
                designator=designator,
                new_items_found=False,
                new_items_count=0,
                previous_version=previous_version,
                current_version=tc_version,
                skipped=False
            )
        
        # Get previously known refs
        known_refs = set(aircraft.get("known_tc_adsb_refs", []))
        current_refs_set = set(current_refs)
        
        # Detect new items
        new_refs = current_refs_set - known_refs
        new_items_found = len(new_refs) > 0
        
        # Update aircraft state
        update_data = {
            "last_tc_adsb_version": tc_version,
            "known_tc_adsb_refs": current_refs,
            "updated_at": datetime.now(timezone.utc)
        }
        
        if new_items_found:
            update_data["adsb_has_new_tc_items"] = True
            update_data["count_new_adsb"] = len(new_refs)
            logger.info(f"Aircraft {registration}: {len(new_refs)} new TC AD/SB items detected")
        else:
            # Only clear if detection ran successfully
            if not aircraft.get("adsb_has_new_tc_items", False):
                update_data["adsb_has_new_tc_items"] = False
                update_data["count_new_adsb"] = 0
        
        await self.db.aircrafts.update_one(
            {"_id": aircraft_id},
            {"$set": update_data}
        )
        
        return AircraftDetectionResult(
            aircraft_id=aircraft_id,
            registration=registration,
            designator=designator,
            new_items_found=new_items_found,
            new_items_count=len(new_refs),
            new_items_refs=sorted(list(new_refs))[:50],  # Limit for response size
            previous_version=previous_version,
            current_version=tc_version,
            skipped=False
        )
    
    # --------------------------------------------------------
    # BATCH DETECTION
    # --------------------------------------------------------
    
    async def run_detection_for_user(
        self,
        user_id: str,
        tc_version: Optional[str] = None,
        force: bool = False,
        triggered_by: str = "system"
    ) -> DetectionSummaryResponse:
        """
        Run TC AD/SB detection for all aircraft belonging to a user.
        """
        # Get TC version
        if not tc_version:
            tc_version = await self.get_current_tc_version()
        
        if not tc_version:
            logger.error("Cannot determine TC AD/SB version")
            raise ValueError("TC AD/SB data version not available")
        
        # Log start
        await self._log_audit_event(
            event_type=AuditEventType.DETECTION_STARTED,
            tc_adsb_version=tc_version,
            triggered_by=triggered_by,
            notes=f"User detection started for user_id={user_id}"
        )
        
        # Get all aircraft for user
        cursor = self.db.aircrafts.find({"user_id": user_id})
        
        results = []
        total_new_items = 0
        aircraft_with_new = 0
        aircraft_skipped = 0
        
        async for aircraft in cursor:
            result = await self.detect_new_items_for_aircraft(
                aircraft_id=aircraft["_id"],
                user_id=user_id,
                tc_version=tc_version,
                force=force
            )
            results.append(result)
            
            if result.skipped:
                aircraft_skipped += 1
            elif result.new_items_found:
                aircraft_with_new += 1
                total_new_items += result.new_items_count
                
                # Log per-aircraft detection
                await self._log_audit_event(
                    event_type=AuditEventType.NEW_ITEMS_FOUND,
                    aircraft_id=result.aircraft_id,
                    registration=result.registration,
                    tc_adsb_version=tc_version,
                    new_items_count=result.new_items_count,
                    new_items_refs=result.new_items_refs,
                    triggered_by=triggered_by
                )
        
        # Log completion
        await self._log_audit_event(
            event_type=AuditEventType.DETECTION_COMPLETED,
            tc_adsb_version=tc_version,
            new_items_count=total_new_items,
            triggered_by=triggered_by,
            notes=f"Processed {len(results)} aircraft, {aircraft_with_new} with new items"
        )
        
        return DetectionSummaryResponse(
            tc_adsb_version=tc_version,
            detection_timestamp=datetime.now(timezone.utc).isoformat(),
            total_aircraft_processed=len(results),
            aircraft_with_new_items=aircraft_with_new,
            aircraft_skipped=aircraft_skipped,
            total_new_items_found=total_new_items,
            results=results,
            triggered_by=triggered_by
        )
    
    async def run_detection_all_aircraft(
        self,
        tc_version: Optional[str] = None,
        force: bool = False,
        triggered_by: str = "system"
    ) -> DetectionSummaryResponse:
        """
        Run TC AD/SB detection for ALL aircraft in the system.
        
        Used for monthly scheduled detection after TC import.
        """
        # Get TC version
        if not tc_version:
            tc_version = await self.get_current_tc_version()
        
        if not tc_version:
            logger.error("Cannot determine TC AD/SB version")
            raise ValueError("TC AD/SB data version not available")
        
        logger.info(f"Starting system-wide TC AD/SB detection for version {tc_version}")
        
        # Log start
        await self._log_audit_event(
            event_type=AuditEventType.DETECTION_STARTED,
            tc_adsb_version=tc_version,
            triggered_by=triggered_by,
            notes="System-wide detection started"
        )
        
        # Get ALL aircraft
        cursor = self.db.aircrafts.find({})
        
        results = []
        total_new_items = 0
        aircraft_with_new = 0
        aircraft_skipped = 0
        
        async for aircraft in cursor:
            result = await self.detect_new_items_for_aircraft(
                aircraft_id=aircraft["_id"],
                user_id=aircraft.get("user_id"),
                tc_version=tc_version,
                force=force
            )
            results.append(result)
            
            if result.skipped:
                aircraft_skipped += 1
            elif result.new_items_found:
                aircraft_with_new += 1
                total_new_items += result.new_items_count
                
                # Log per-aircraft detection
                await self._log_audit_event(
                    event_type=AuditEventType.NEW_ITEMS_FOUND,
                    aircraft_id=result.aircraft_id,
                    registration=result.registration,
                    tc_adsb_version=tc_version,
                    new_items_count=result.new_items_count,
                    new_items_refs=result.new_items_refs,
                    triggered_by=triggered_by
                )
        
        # Log completion
        await self._log_audit_event(
            event_type=AuditEventType.DETECTION_COMPLETED,
            tc_adsb_version=tc_version,
            new_items_count=total_new_items,
            triggered_by=triggered_by,
            notes=f"System-wide: Processed {len(results)} aircraft, {aircraft_with_new} with new items"
        )
        
        logger.info(
            f"TC AD/SB detection complete: {len(results)} aircraft, "
            f"{aircraft_with_new} with new items, {total_new_items} total new items"
        )
        
        return DetectionSummaryResponse(
            tc_adsb_version=tc_version,
            detection_timestamp=datetime.now(timezone.utc).isoformat(),
            total_aircraft_processed=len(results),
            aircraft_with_new_items=aircraft_with_new,
            aircraft_skipped=aircraft_skipped,
            total_new_items_found=total_new_items,
            results=results,
            triggered_by=triggered_by
        )
    
    # --------------------------------------------------------
    # ALERT MANAGEMENT
    # --------------------------------------------------------
    
    async def mark_adsb_reviewed(
        self,
        aircraft_id: str,
        user_id: str
    ) -> MarkReviewedResponse:
        """
        Mark AD/SB module as reviewed for an aircraft.
        
        Called when user opens/views the AD/SB module.
        Clears the alert flag and logs the event.
        """
        # Get aircraft
        aircraft = await self.db.aircrafts.find_one({
            "_id": aircraft_id,
            "user_id": user_id
        })
        
        if not aircraft:
            raise ValueError("Aircraft not found or not authorized")
        
        registration = aircraft.get("registration", "")
        previous_count = aircraft.get("count_new_adsb", 0)
        had_alert = aircraft.get("adsb_has_new_tc_items", False)
        
        now = datetime.now(timezone.utc)
        
        # Update aircraft - clear alert
        await self.db.aircrafts.update_one(
            {"_id": aircraft_id},
            {"$set": {
                "adsb_has_new_tc_items": False,
                "count_new_adsb": 0,
                "last_adsb_reviewed_at": now,
                "updated_at": now
            }}
        )
        
        # Log audit event (always, for traceability)
        await self._log_audit_event(
            event_type=AuditEventType.ALERT_CLEARED,
            aircraft_id=aircraft_id,
            registration=registration,
            tc_adsb_version=aircraft.get("last_tc_adsb_version"),
            new_items_count=previous_count,
            triggered_by=f"user:{user_id}",
            notes=f"Alert cleared on module view. Had {previous_count} new items."
        )
        
        logger.info(f"AD/SB alert cleared for aircraft {registration} by user {user_id}")
        
        return MarkReviewedResponse(
            aircraft_id=aircraft_id,
            registration=registration,
            alert_cleared=had_alert,
            reviewed_at=now.isoformat(),
            previous_new_items_count=previous_count,
            message="AD/SB review recorded. Alert cleared." if had_alert else "Review recorded. No alert was active."
        )
    
    async def get_alert_status(
        self,
        aircraft_id: str,
        user_id: str
    ) -> dict:
        """
        Get current AD/SB alert status for an aircraft.
        """
        aircraft = await self.db.aircrafts.find_one({
            "_id": aircraft_id,
            "user_id": user_id
        }, {
            "_id": 1,
            "registration": 1,
            "adsb_has_new_tc_items": 1,
            "count_new_adsb": 1,
            "last_tc_adsb_version": 1,
            "last_adsb_reviewed_at": 1
        })
        
        if not aircraft:
            raise ValueError("Aircraft not found or not authorized")
        
        last_reviewed = aircraft.get("last_adsb_reviewed_at")
        
        return {
            "aircraft_id": aircraft["_id"],
            "registration": aircraft.get("registration", ""),
            "adsb_has_new_tc_items": aircraft.get("adsb_has_new_tc_items", False),
            "count_new_adsb": aircraft.get("count_new_adsb", 0),
            "last_tc_adsb_version": aircraft.get("last_tc_adsb_version"),
            "last_adsb_reviewed_at": last_reviewed.isoformat() if last_reviewed else None
        }
    
    # --------------------------------------------------------
    # AUDIT LOGGING
    # --------------------------------------------------------
    
    async def _log_audit_event(
        self,
        event_type: AuditEventType,
        aircraft_id: Optional[str] = None,
        registration: Optional[str] = None,
        tc_adsb_version: Optional[str] = None,
        new_items_count: int = 0,
        new_items_refs: List[str] = None,
        triggered_by: str = "system",
        notes: Optional[str] = None
    ):
        """
        Log an audit event to tc_adsb_audit_log collection.
        """
        doc = {
            "event_type": event_type.value,
            "aircraft_id": aircraft_id,
            "registration": registration,
            "tc_adsb_version": tc_adsb_version,
            "new_items_count": new_items_count,
            "new_items_refs": (new_items_refs or [])[:50],  # Limit storage
            "triggered_by": triggered_by,
            "notes": notes,
            "created_at": datetime.now(timezone.utc)
        }
        
        try:
            await self.db.tc_adsb_audit_log.insert_one(doc)
        except Exception as e:
            # Never fail detection due to audit log error
            logger.error(f"Failed to write audit log: {e}")
    
    async def get_audit_log(
        self,
        aircraft_id: Optional[str] = None,
        limit: int = 100
    ) -> List[dict]:
        """
        Get audit log entries.
        
        Args:
            aircraft_id: Filter by aircraft (optional)
            limit: Maximum entries to return
        """
        query = {}
        if aircraft_id:
            query["aircraft_id"] = aircraft_id
        
        cursor = self.db.tc_adsb_audit_log.find(
            query,
            {"_id": 0}
        ).sort("created_at", -1).limit(limit)
        
        return await cursor.to_list(length=limit)
