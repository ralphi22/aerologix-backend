#!/usr/bin/env python3
"""
TC_Aeronefs Privacy Migration Script

Removes all non-compliant fields from existing tc_aeronefs documents.

FIELDS REMOVED:
- first_owner_city
- first_owner_province
- first_owner_full_name
- serial_number
- status
- category
- tc_data

FIELDS KEPT:
- _id
- registration
- manufacturer
- model
- designator
- first_owner_given_name
- first_owner_family_name
- validity_start
- validity_end
- tc_version
- created_at
- updated_at

Usage:
    python scripts/migrate_tc_privacy.py --dry-run
    python scripts/migrate_tc_privacy.py --execute

Author: AeroLogix AI
"""

import asyncio
import argparse
import sys
import os
from datetime import datetime
import logging

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from motor.motor_asyncio import AsyncIOMotorClient

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


# ============================================================
# PRIVACY CONFIGURATION
# ============================================================

COLLECTION_NAME = "tc_aeronefs"

# Fields to REMOVE (privacy reasons)
FIELDS_TO_REMOVE = [
    "first_owner_city",
    "first_owner_province", 
    "first_owner_full_name",
    "serial_number",
    "status",
    "category",
    "tc_data",
]

# Fields to KEEP (whitelist)
ALLOWED_FIELDS = {
    "_id",
    "registration",
    "manufacturer",
    "model", 
    "designator",
    "first_owner_given_name",
    "first_owner_family_name",
    "validity_start",
    "validity_end",
    "tc_version",
    "created_at",
    "updated_at",
}

# Indexes to DROP (no longer needed)
INDEXES_TO_DROP = [
    "province_idx",
    "category_idx",
]


# ============================================================
# MIGRATION FUNCTIONS
# ============================================================

async def count_affected_documents(db) -> dict:
    """Count documents with fields that need to be removed"""
    collection = db[COLLECTION_NAME]
    
    counts = {}
    
    for field in FIELDS_TO_REMOVE:
        count = await collection.count_documents({field: {"$exists": True}})
        counts[field] = count
    
    return counts


async def remove_forbidden_fields(db, dry_run: bool = True) -> dict:
    """
    Remove all forbidden fields from documents.
    
    Returns stats about the migration.
    """
    collection = db[COLLECTION_NAME]
    
    stats = {
        "documents_scanned": 0,
        "documents_modified": 0,
        "fields_removed": {f: 0 for f in FIELDS_TO_REMOVE}
    }
    
    # Build the $unset operation
    unset_fields = {field: "" for field in FIELDS_TO_REMOVE}
    
    if dry_run:
        # In dry-run, just count affected documents
        affected = await count_affected_documents(db)
        stats["fields_to_remove"] = affected
        stats["total_documents"] = await collection.count_documents({})
        
        # Count documents with at least one forbidden field
        or_conditions = [{field: {"$exists": True}} for field in FIELDS_TO_REMOVE]
        if or_conditions:
            stats["documents_to_modify"] = await collection.count_documents({"$or": or_conditions})
        
        return stats
    
    # Real migration - use update_many for efficiency
    logger.info("Removing forbidden fields from all documents...")
    
    result = await collection.update_many(
        {},  # All documents
        {"$unset": unset_fields}
    )
    
    stats["documents_scanned"] = result.matched_count
    stats["documents_modified"] = result.modified_count
    
    return stats


async def drop_unused_indexes(db, dry_run: bool = True) -> list:
    """Drop indexes that are no longer needed"""
    collection = db[COLLECTION_NAME]
    
    dropped = []
    
    # Get existing indexes
    existing_indexes = await collection.index_information()
    
    for index_name in INDEXES_TO_DROP:
        if index_name in existing_indexes:
            if dry_run:
                logger.info(f"DRY-RUN: Would drop index '{index_name}'")
                dropped.append(index_name)
            else:
                logger.info(f"Dropping index '{index_name}'...")
                await collection.drop_index(index_name)
                dropped.append(index_name)
                logger.info(f"  Dropped: {index_name}")
    
    return dropped


async def verify_migration(db) -> dict:
    """Verify no forbidden fields remain"""
    collection = db[COLLECTION_NAME]
    
    verification = {
        "status": "PASS",
        "forbidden_fields_found": {},
        "sample_clean_document": None
    }
    
    # Check for any remaining forbidden fields
    for field in FIELDS_TO_REMOVE:
        count = await collection.count_documents({field: {"$exists": True}})
        if count > 0:
            verification["status"] = "FAIL"
            verification["forbidden_fields_found"][field] = count
    
    # Get a sample clean document
    doc = await collection.find_one()
    if doc:
        # Only include allowed fields in sample
        clean_doc = {k: v for k, v in doc.items() if k in ALLOWED_FIELDS}
        clean_doc["_id"] = str(clean_doc.get("_id", ""))
        if "created_at" in clean_doc:
            clean_doc["created_at"] = str(clean_doc["created_at"])
        if "updated_at" in clean_doc:
            clean_doc["updated_at"] = str(clean_doc["updated_at"])
        verification["sample_clean_document"] = clean_doc
    
    return verification


async def run_migration(dry_run: bool = True):
    """Main migration function"""
    
    # Connect to MongoDB
    mongo_url = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
    db_name = os.environ.get("DB_NAME", "aerologix")
    
    client = AsyncIOMotorClient(mongo_url)
    db = client[db_name]
    
    logger.info("=" * 60)
    logger.info("TC_AERONEFS PRIVACY MIGRATION")
    logger.info(f"Mode: {'DRY-RUN' if dry_run else 'EXECUTE'}")
    logger.info("=" * 60)
    
    try:
        # Step 1: Count affected documents
        logger.info("\n[STEP 1] Analyzing affected documents...")
        affected = await count_affected_documents(db)
        
        total_affected = sum(affected.values())
        logger.info(f"Fields to remove:")
        for field, count in affected.items():
            logger.info(f"  - {field}: {count} documents")
        
        if total_affected == 0:
            logger.info("\n✅ No forbidden fields found. Database is already clean.")
            return {"status": "ALREADY_CLEAN"}
        
        # Step 2: Remove forbidden fields
        logger.info("\n[STEP 2] Removing forbidden fields...")
        stats = await remove_forbidden_fields(db, dry_run)
        
        if dry_run:
            logger.info(f"DRY-RUN: Would modify {stats.get('documents_to_modify', 0)} documents")
        else:
            logger.info(f"Modified {stats['documents_modified']} documents")
        
        # Step 3: Drop unused indexes
        logger.info("\n[STEP 3] Dropping unused indexes...")
        dropped_indexes = await drop_unused_indexes(db, dry_run)
        logger.info(f"{'Would drop' if dry_run else 'Dropped'} {len(dropped_indexes)} indexes")
        
        # Step 4: Verify (only if not dry-run)
        if not dry_run:
            logger.info("\n[STEP 4] Verifying migration...")
            verification = await verify_migration(db)
            
            if verification["status"] == "PASS":
                logger.info("✅ VERIFICATION PASSED - All forbidden fields removed")
            else:
                logger.error("❌ VERIFICATION FAILED - Some forbidden fields remain")
                logger.error(f"   Remaining: {verification['forbidden_fields_found']}")
            
            if verification.get("sample_clean_document"):
                logger.info("\nSample clean document:")
                import json
                logger.info(json.dumps(verification["sample_clean_document"], indent=2))
        
        logger.info("\n" + "=" * 60)
        logger.info("MIGRATION COMPLETE" if not dry_run else "DRY-RUN COMPLETE")
        logger.info("=" * 60)
        
        return stats
        
    finally:
        client.close()


# ============================================================
# MAIN
# ============================================================

async def main():
    parser = argparse.ArgumentParser(
        description="TC_Aeronefs Privacy Migration - Remove non-compliant fields"
    )
    
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate migration without making changes"
    )
    group.add_argument(
        "--execute",
        action="store_true",
        help="Execute the migration (DESTRUCTIVE)"
    )
    
    args = parser.parse_args()
    
    if args.execute:
        confirm = input(
            "\n⚠️  WARNING: This will PERMANENTLY remove data from tc_aeronefs.\n"
            "Fields to remove: first_owner_city, first_owner_province, serial_number, etc.\n"
            "\nType 'YES' to confirm: "
        )
        if confirm != "YES":
            print("Migration cancelled.")
            return
    
    await run_migration(dry_run=args.dry_run)


if __name__ == "__main__":
    asyncio.run(main())
