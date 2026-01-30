"""
Collaborative AD/SB Detection Service

Detects new AD/SB references and notifies users with the same aircraft TYPE.

ARCHITECTURE:
1. Global reference pool: tc_adsb_global_references (aircraft_type_key + reference)
2. User alerts: tc_adsb_alerts (per user, per aircraft)

CANONICAL KEY (OBLIGATOIRE):
aircraft_type_key = normalize(manufacturer) + "::" + normalize(model)

NO comparison by:
- User ID
- Registration
- Serial number

PROCESS:
1. On TC import → check if reference is new for this aircraft_type_key
2. If new → add to global pool + create alerts for other users with same type
3. Alerts are informational only (no compliance decisions)

TC-SAFE:
- Documentary detection only
- No regulatory interpretation
- Human decision required
"""

import re
import logging
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional, Set
from motor.motor_asyncio import AsyncIOMotorDatabase
from pydantic import BaseModel

logger = logging.getLogger(__name__)


# ============================================================
# MODELS
# ============================================================

class NewReferenceAlert(BaseModel):
    """Alert for a new AD/SB reference"""
    type: str = "NEW_AD_SB"
    aircraft_type_key: str  # CANONICAL KEY: manufacturer::model
    manufacturer: str
    model: str
    reference: str
    reference_type: str  # "AD" or "SB"
    message: str
    created_at: datetime


class CollaborativeDetectionResult(BaseModel):
    """Result of collaborative detection"""
    new_references_count: int = 0
    alerts_created_count: int = 0
    users_notified: int = 0
    references_added_to_pool: List[str] = []
    aircraft_type_key: str = ""


# ============================================================
# SERVICE
# ============================================================

class CollaborativeADSBService:
    """
    Service for collaborative AD/SB reference detection.
    
    CANONICAL KEY: aircraft_type_key = manufacturer::model
    
    Collections used:
    - tc_adsb_global_references: Global pool of type_key+reference pairs
    - tc_adsb_alerts: User alerts for new references
    - aircrafts: To find users with same aircraft_type_key
    
    TC-SAFE: Detection only, no compliance decisions.
    """
    
    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db
    
    # --------------------------------------------------------
    # NORMALIZATION - CANONICAL KEY
    # --------------------------------------------------------
    
    def normalize_manufacturer(self, manufacturer: str) -> str:
        """
        Normalize manufacturer name for matching.
        
        - Uppercase
        - Remove spaces and special chars
        - Keep alphanumeric only
        """
        if not manufacturer:
            return ""
        return re.sub(r'[^A-Z0-9]', '', manufacturer.upper())
    
    def normalize_model(self, model: str) -> str:
        """
        Normalize aircraft model for matching.
        
        - Uppercase
        - Remove spaces and hyphens
        - Keep alphanumeric only
        """
        if not model:
            return ""
        return re.sub(r'[^A-Z0-9]', '', model.upper())
    
    def create_aircraft_type_key(self, manufacturer: str, model: str) -> str:
        """
        Create CANONICAL aircraft type key.
        
        OBLIGATOIRE: aircraft_type_key = normalize(manufacturer) + "::" + normalize(model)
        
        NO comparison by user, registration, or serial number.
        """
        mfr_norm = self.normalize_manufacturer(manufacturer)
        model_norm = self.normalize_model(model)
        return f"{mfr_norm}::{model_norm}"
    
    def normalize_reference(self, ref: str) -> str:
        """
        Normalize AD/SB reference for matching.
        
        - Uppercase
        - Standardize separators
        """
        if not ref:
            return ""
        normalized = ref.strip().upper()
        # Remove multiple spaces/separators
        normalized = re.sub(r'[\s\-_.]+', '-', normalized)
        return normalized.strip('-')
    
    def create_global_key(self, manufacturer: str, model: str, reference: str) -> str:
        """
        Create a unique key for the global reference pool.
        
        Format: aircraft_type_key::reference
        Example: CESSNA::172M::CF-2024-03
        """
        type_key = self.create_aircraft_type_key(manufacturer, model)
        ref_norm = self.normalize_reference(reference)
        return f"{type_key}::{ref_norm}"
    
    # --------------------------------------------------------
    # GLOBAL REFERENCE POOL
    # --------------------------------------------------------
    
    async def check_reference_exists(self, manufacturer: str, model: str, reference: str) -> bool:
        """Check if a reference already exists in the global pool for this aircraft type."""
        global_key = self.create_global_key(manufacturer, model, reference)
        
        existing = await self.db.tc_adsb_global_references.find_one({
            "global_key": global_key
        })
        
        return existing is not None
    
    async def add_reference_to_pool(
        self,
        manufacturer: str,
        model: str,
        reference: str,
        reference_type: str,
        contributed_by: str,
        aircraft_id: str
    ) -> bool:
        """
        Add a reference to the global pool.
        
        Uses CANONICAL aircraft_type_key = manufacturer::model
        
        Returns:
            True if added (was new), False if already existed
        """
        global_key = self.create_global_key(manufacturer, model, reference)
        type_key = self.create_aircraft_type_key(manufacturer, model)
        mfr_norm = self.normalize_manufacturer(manufacturer)
        model_norm = self.normalize_model(model)
        ref_norm = self.normalize_reference(reference)
        
        # Check if already exists
        existing = await self.db.tc_adsb_global_references.find_one({
            "global_key": global_key
        })
        
        if existing:
            # Update seen count
            await self.db.tc_adsb_global_references.update_one(
                {"global_key": global_key},
                {
                    "$inc": {"seen_count": 1},
                    "$set": {"last_seen_at": datetime.now(timezone.utc)},
                    "$addToSet": {"contributing_users": contributed_by}
                }
            )
            return False
        
        # Create new entry with CANONICAL aircraft_type_key
        doc = {
            "global_key": global_key,
            "aircraft_type_key": type_key,  # CANONICAL: manufacturer::model
            "manufacturer_normalized": mfr_norm,
            "manufacturer_original": manufacturer,
            "model_normalized": model_norm,
            "model_original": model,
            "reference_normalized": ref_norm,
            "reference_original": reference,
            "reference_type": reference_type,
            "first_contributed_by": contributed_by,
            "first_contributed_aircraft_id": aircraft_id,
            "contributing_users": [contributed_by],
            "seen_count": 1,
            "first_seen_at": datetime.now(timezone.utc),
            "last_seen_at": datetime.now(timezone.utc)
        }
        
        try:
            await self.db.tc_adsb_global_references.insert_one(doc)
            logger.info(f"[COLLABORATIVE] New reference added to pool: {reference} for type_key={type_key}")
            return True
        except Exception as e:
            # Handle race condition (duplicate key)
            logger.warning(f"[COLLABORATIVE] Failed to add reference (race condition?): {e}")
            return False
    
    # --------------------------------------------------------
    # FIND USERS WITH SAME AIRCRAFT TYPE (CANONICAL KEY)
    # --------------------------------------------------------
    
    async def find_users_with_aircraft_type(
        self,
        manufacturer: str,
        model: str,
        exclude_user_id: str
    ) -> List[Dict[str, Any]]:
        """
        Find all users who have an aircraft with the same CANONICAL type.
        
        Uses aircraft_type_key = manufacturer::model for matching.
        NO matching by registration or serial number.
        
        Returns:
            List of {user_id, aircraft_id, aircraft_type_key} dicts
        """
        target_type_key = self.create_aircraft_type_key(manufacturer, model)
        
        if not target_type_key or target_type_key == "::":
            return []
        
        # Find all aircraft with matching type (different users)
        results = []
        
        cursor = self.db.aircrafts.find({
            "user_id": {"$ne": exclude_user_id}
        })
        
        async for aircraft in cursor:
            aircraft_mfr = aircraft.get("manufacturer", "")
            aircraft_model = aircraft.get("model", "")
            aircraft_type_key = self.create_aircraft_type_key(aircraft_mfr, aircraft_model)
            
            # CANONICAL MATCH: exact aircraft_type_key
            if aircraft_type_key == target_type_key:
                results.append({
                    "user_id": aircraft["user_id"],
                    "aircraft_id": aircraft["_id"],
                    "aircraft_type_key": aircraft_type_key,
                    "manufacturer": aircraft_mfr,
                    "model": aircraft_model
                })
        
        # Deduplicate by user_id (keep first aircraft per user)
        seen_users: Set[str] = set()
        unique_results = []
        for r in results:
            if r["user_id"] not in seen_users:
                seen_users.add(r["user_id"])
                unique_results.append(r)
        
        return unique_results
    
    # --------------------------------------------------------
    # CREATE ALERTS
    # --------------------------------------------------------
    
    async def create_alert(
        self,
        user_id: str,
        aircraft_id: str,
        manufacturer: str,
        model: str,
        reference: str,
        reference_type: str
    ) -> bool:
        """
        Create an alert for a user about a new reference.
        
        Uses CANONICAL aircraft_type_key for identification.
        
        Returns:
            True if alert created, False if duplicate
        """
        aircraft_type_key = self.create_aircraft_type_key(manufacturer, model)
        
        # Check for duplicate alert (by aircraft_type_key + reference)
        existing = await self.db.tc_adsb_alerts.find_one({
            "user_id": user_id,
            "aircraft_type_key": aircraft_type_key,
            "reference": reference,
            "status": {"$ne": "DISMISSED"}
        })
        
        if existing:
            return False
        
        # Create alert with CANONICAL key
        alert_doc = {
            "type": "NEW_AD_SB",
            "user_id": user_id,
            "aircraft_id": aircraft_id,
            "aircraft_type_key": aircraft_type_key,  # CANONICAL KEY
            "manufacturer": manufacturer,
            "model": model,
            "reference": reference,
            "reference_type": reference_type,
            "message": f"A new {reference_type} reference ({reference}) was added for your aircraft type ({manufacturer} {model}). Please review with your AME.",
            "status": "UNREAD",
            "created_at": datetime.now(timezone.utc),
            "read_at": None,
            "dismissed_at": None
        }
        
        try:
            await self.db.tc_adsb_alerts.insert_one(alert_doc)
            return True
        except Exception as e:
            logger.warning(f"[COLLABORATIVE] Failed to create alert: {e}")
            return False
    
    # --------------------------------------------------------
    # MAIN DETECTION METHOD
    # --------------------------------------------------------
    
    async def process_imported_references(
        self,
        references: List[str],
        reference_type: str,
        aircraft_id: str,
        user_id: str,
        manufacturer: str,
        model: str
    ) -> CollaborativeDetectionResult:
        """
        Process imported references for collaborative detection.
        
        Uses CANONICAL aircraft_type_key = manufacturer::model
        
        Called after TC PDF import to:
        1. Check each reference against global pool (by type_key)
        2. Add new references to pool
        3. Create alerts for other users with same aircraft_type_key
        
        Args:
            references: List of imported reference identifiers
            reference_type: "AD" or "SB"
            aircraft_id: Source aircraft ID
            user_id: User who imported
            manufacturer: Aircraft manufacturer
            model: Aircraft model
            
        Returns:
            CollaborativeDetectionResult with counts
        """
        aircraft_type_key = self.create_aircraft_type_key(manufacturer, model)
        
        logger.info(
            f"[COLLABORATIVE] Processing {len(references)} references | "
            f"aircraft_type_key={aircraft_type_key} | aircraft={aircraft_id} | user={user_id}"
        )
        
        if not references or not aircraft_type_key or aircraft_type_key == "::":
            return CollaborativeDetectionResult(aircraft_type_key=aircraft_type_key)
        
        new_references = []
        alerts_created = 0
        users_notified: Set[str] = set()
        
        # Step 1: Check each reference against global pool (by aircraft_type_key)
        for ref in references:
            is_new = await self.add_reference_to_pool(
                manufacturer=manufacturer,
                model=model,
                reference=ref,
                reference_type=reference_type,
                contributed_by=user_id,
                aircraft_id=aircraft_id
            )
            
            if is_new:
                new_references.append(ref)
        
        # Step 2: If new references found, notify other users with same aircraft_type_key
        if new_references:
            logger.info(f"[COLLABORATIVE] {len(new_references)} NEW references detected for type_key={aircraft_type_key}")
            
            # Find other users with same CANONICAL aircraft_type_key
            other_users = await self.find_users_with_aircraft_type(
                manufacturer=manufacturer,
                model=model,
                exclude_user_id=user_id
            )
            
            logger.info(f"[COLLABORATIVE] Found {len(other_users)} other users with type_key={aircraft_type_key}")
            
            # Create alerts for each new reference to each user
            for user_info in other_users:
                for ref in new_references:
                    created = await self.create_alert(
                        user_id=user_info["user_id"],
                        aircraft_id=user_info["aircraft_id"],
                        manufacturer=manufacturer,
                        model=model,
                        reference=ref,
                        reference_type=reference_type
                    )
                    
                    if created:
                        alerts_created += 1
                        users_notified.add(user_info["user_id"])
        
        result = CollaborativeDetectionResult(
            new_references_count=len(new_references),
            alerts_created_count=alerts_created,
            users_notified=len(users_notified),
            references_added_to_pool=new_references,
            aircraft_type_key=aircraft_type_key
        )
        
        # Log summary
        if new_references:
            logger.info(
                f"[COLLABORATIVE] RESULT | new_refs={result.new_references_count} | "
                f"alerts={result.alerts_created_count} | users_notified={result.users_notified}"
            )
        
        return result


# ============================================================
# SINGLETON
# ============================================================

_service_instance: Optional[CollaborativeADSBService] = None


def get_collaborative_service(db: AsyncIOMotorDatabase) -> CollaborativeADSBService:
    """Get or create the collaborative service instance."""
    global _service_instance
    if _service_instance is None:
        _service_instance = CollaborativeADSBService(db)
    return _service_instance
