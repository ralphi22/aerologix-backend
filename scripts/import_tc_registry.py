#!/usr/bin/env python3
"""
Transport Canada Aircraft Registry Import Script

Imports parsed TC CARS data into MongoDB collection `tc_aeronefs`.

Features:
- Upsert by registration (no duplicates)
- Version tagging (tc_version)
- Dry-run mode
- Detailed logging (inserted/updated/skipped/errors)
- Rollback support via version tracking

Usage:
    python scripts/import_tc_registry.py --version 2026Q1
    python scripts/import_tc_registry.py --version 2026Q1 --dry-run
    python scripts/import_tc_registry.py --version 2026Q1 --limit 100

Author: AeroLogix AI
"""

import asyncio
import argparse
import json
import sys
import os
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any
import logging

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

# Import the parser
from scripts.parse_tc_cars import (
    parse_carscurr, 
    parse_carsownr, 
    join_aircraft_owners
)

# Load environment
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


# ============================================================
# CONSTANTS
# ============================================================

COLLECTION_NAME = "tc_aeronefs"

# Index definitions
INDEXES = [
    {
        "keys": [("registration", 1)],
        "unique": True,
        "name": "registration_unique"
    },
    {
        "keys": [("manufacturer", 1)],
        "name": "manufacturer_idx"
    },
    {
        "keys": [("tc_version", 1)],
        "name": "tc_version_idx"
    },
    {
        "keys": [("first_owner_province", 1)],
        "name": "province_idx"
    },
    {
        "keys": [("category", 1)],
        "name": "category_idx"
    },
]


# ============================================================
# IMPORT STATISTICS
# ============================================================

class ImportStats:
    """Track import statistics"""
    
    def __init__(self):
        self.inserted = 0
        self.updated = 0
        self.skipped = 0
        self.errors = 0
        self.error_details: List[Dict] = []
        self.start_time = datetime.utcnow()
        self.end_time: Optional[datetime] = None
    
    def record_error(self, registration: str, error: str):
        self.errors += 1
        self.error_details.append({
            "registration": registration,
            "error": str(error)
        })
    
    def finish(self):
        self.end_time = datetime.utcnow()
    
    @property
    def duration_seconds(self) -> float:
        end = self.end_time or datetime.utcnow()
        return (end - self.start_time).total_seconds()
    
    @property
    def total_processed(self) -> int:
        return self.inserted + self.updated + self.skipped + self.errors
    
    def to_dict(self) -> Dict:
        return {
            "inserted": self.inserted,
            "updated": self.updated,
            "skipped": self.skipped,
            "errors": self.errors,
            "total_processed": self.total_processed,
            "duration_seconds": round(self.duration_seconds, 2),
            "error_details": self.error_details[:10]  # First 10 errors
        }
    
    def __str__(self) -> str:
        return (
            f"Inserted: {self.inserted} | "
            f"Updated: {self.updated} | "
            f"Skipped: {self.skipped} | "
            f"Errors: {self.errors} | "
            f"Duration: {self.duration_seconds:.1f}s"
        )


# ============================================================
# NORMALIZATION
# ============================================================

def normalize_record(record: dict, tc_version: str) -> dict:
    """
    Normalize a parsed record for MongoDB insertion.
    
    PRIVACY COMPLIANT: Only includes allowed fields.
    Forbidden fields (city, province, serial_number, etc.) are NOT stored.
    
    Allowed fields:
    - _id, registration, manufacturer, model, designator
    - first_owner_given_name, first_owner_family_name
    - validity_start, validity_end
    - tc_version, created_at, updated_at
    """
    now = datetime.utcnow()
    registration = record.get("registration", "")
    
    # PRIVACY: Only include allowed fields
    return {
        "_id": registration.replace("-", "").upper(),  # e.g., "CGABC"
        "registration": registration,
        "manufacturer": record.get("manufacturer"),
        "model": record.get("model"),
        "designator": record.get("designator"),
        # Owner names only (no city, province, full_name)
        "first_owner_given_name": record.get("first_owner_given_name"),
        "first_owner_family_name": record.get("first_owner_family_name"),
        # Dates
        "validity_start": record.get("validity_start"),
        "validity_end": record.get("validity_end"),
        # Metadata
        "tc_version": tc_version,
        "created_at": now,
        "updated_at": now,
    }
    # NOTE: The following fields are INTENTIONALLY NOT INCLUDED:
    # - serial_number (privacy)
    # - category (not needed)
    # - status (not needed)
    # - first_owner_city (privacy)
    # - first_owner_province (privacy)
    # - first_owner_full_name (privacy - use given + family instead)
    # - tc_data (bulk personal data)


# ============================================================
# INDEX MANAGEMENT
# ============================================================

async def ensure_indexes(db) -> None:
    """Create indexes if they don't exist"""
    collection = db[COLLECTION_NAME]
    
    for index_def in INDEXES:
        try:
            await collection.create_index(
                index_def["keys"],
                unique=index_def.get("unique", False),
                name=index_def["name"]
            )
            logger.info(f"Index ensured: {index_def['name']}")
        except Exception as e:
            logger.warning(f"Index {index_def['name']} may already exist: {e}")


# ============================================================
# IMPORT FUNCTIONS
# ============================================================

async def import_single_record(
    collection, 
    record: Dict, 
    stats: ImportStats,
    dry_run: bool = False
) -> None:
    """
    Import a single record with upsert logic.
    
    - If registration doesn't exist: INSERT
    - If registration exists with same tc_version: SKIP
    - If registration exists with older tc_version: UPDATE
    """
    registration = record.get("registration", "unknown")
    
    try:
        if dry_run:
            # In dry-run, just count as would-be-inserted
            existing = await collection.find_one({"registration": registration})
            if existing:
                if existing.get("tc_version") == record.get("tc_version"):
                    stats.skipped += 1
                else:
                    stats.updated += 1
            else:
                stats.inserted += 1
            return
        
        # Real upsert
        existing = await collection.find_one({"registration": registration})
        
        if existing:
            # Check if same version (skip) or different version (update)
            if existing.get("tc_version") == record.get("tc_version"):
                stats.skipped += 1
                return
            
            # Update existing record
            record["created_at"] = existing.get("created_at", datetime.utcnow())
            record["updated_at"] = datetime.utcnow()
            
            await collection.replace_one(
                {"registration": registration},
                record
            )
            stats.updated += 1
        else:
            # Insert new record
            await collection.insert_one(record)
            stats.inserted += 1
            
    except Exception as e:
        stats.record_error(registration, str(e))
        logger.error(f"Error importing {registration}: {e}")


async def import_tc_registry(
    db,
    tc_version: str,
    data_dir: Path,
    limit: Optional[int] = None,
    dry_run: bool = False,
    batch_size: int = 500
) -> ImportStats:
    """
    Main import function.
    
    Args:
        db: MongoDB database instance
        tc_version: Version tag (e.g., "2026Q1")
        data_dir: Directory containing TC data files
        limit: Optional limit on records to process
        dry_run: If True, don't actually write to DB
        batch_size: Number of records to process before logging progress
    
    Returns:
        ImportStats with results
    """
    stats = ImportStats()
    collection = db[COLLECTION_NAME]
    
    logger.info("=" * 60)
    logger.info(f"TC REGISTRY IMPORT - Version: {tc_version}")
    logger.info(f"Mode: {'DRY-RUN' if dry_run else 'LIVE'}")
    logger.info("=" * 60)
    
    # Ensure indexes (skip in dry-run)
    if not dry_run:
        await ensure_indexes(db)
    
    # Parse TC files
    carscurr_path = data_dir / "carscurr.txt"
    carsownr_path = data_dir / "carsownr.txt"
    
    if not carscurr_path.exists():
        raise FileNotFoundError(f"carscurr.txt not found in {data_dir}")
    if not carsownr_path.exists():
        raise FileNotFoundError(f"carsownr.txt not found in {data_dir}")
    
    logger.info(f"Parsing {carscurr_path}...")
    aircraft = parse_carscurr(carscurr_path, limit)
    
    logger.info(f"Parsing {carsownr_path}...")
    owners = parse_carsownr(carsownr_path, limit)
    
    logger.info("Joining aircraft and owner data...")
    records = join_aircraft_owners(aircraft, owners)
    
    total_records = len(records)
    logger.info(f"Total records to process: {total_records}")
    
    # Process records
    for i, record in enumerate(records):
        normalized = normalize_record(record, tc_version)
        await import_single_record(collection, normalized, stats, dry_run)
        
        # Progress logging
        if (i + 1) % batch_size == 0:
            logger.info(f"Progress: {i + 1}/{total_records} | {stats}")
    
    stats.finish()
    
    logger.info("=" * 60)
    logger.info("IMPORT COMPLETE")
    logger.info(f"Final stats: {stats}")
    logger.info("=" * 60)
    
    return stats


# ============================================================
# ROLLBACK FUNCTION
# ============================================================

async def rollback_version(db, tc_version: str, dry_run: bool = False) -> int:
    """
    Remove all records with a specific tc_version.
    
    Use this to undo a bad import.
    
    Returns:
        Number of records deleted
    """
    collection = db[COLLECTION_NAME]
    
    count = await collection.count_documents({"tc_version": tc_version})
    logger.info(f"Found {count} records with tc_version={tc_version}")
    
    if dry_run:
        logger.info(f"DRY-RUN: Would delete {count} records")
        return count
    
    if count > 0:
        result = await collection.delete_many({"tc_version": tc_version})
        logger.info(f"Deleted {result.deleted_count} records")
        return result.deleted_count
    
    return 0


# ============================================================
# VERSION LISTING
# ============================================================

async def list_versions(db) -> List[Dict]:
    """
    List all tc_versions in the database with counts.
    """
    collection = db[COLLECTION_NAME]
    
    pipeline = [
        {"$group": {
            "_id": "$tc_version",
            "count": {"$sum": 1},
            "last_updated": {"$max": "$updated_at"}
        }},
        {"$sort": {"_id": -1}}
    ]
    
    versions = []
    async for doc in collection.aggregate(pipeline):
        versions.append({
            "tc_version": doc["_id"],
            "count": doc["count"],
            "last_updated": doc["last_updated"]
        })
    
    return versions


# ============================================================
# MAIN
# ============================================================

async def main():
    parser = argparse.ArgumentParser(
        description="Import Transport Canada aircraft registry into MongoDB"
    )
    parser.add_argument(
        "--version", "-v",
        type=str,
        required=True,
        help="TC data version tag (e.g., 2026Q1)"
    )
    parser.add_argument(
        "--data-dir",
        type=str,
        default="tc_data",
        help="Directory containing TC data files"
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limit number of records to import"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate import without writing to database"
    )
    parser.add_argument(
        "--rollback",
        action="store_true",
        help="Remove all records with the specified version"
    )
    parser.add_argument(
        "--list-versions",
        action="store_true",
        help="List all versions in the database"
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Output stats to JSON file"
    )
    
    args = parser.parse_args()
    
    # Connect to MongoDB
    mongo_url = os.getenv("MONGO_URL", "mongodb://localhost:27017")
    db_name = os.getenv("DB_NAME", "aerologix")
    
    client = AsyncIOMotorClient(mongo_url)
    db = client[db_name]
    
    logger.info(f"Connected to MongoDB: {db_name}")
    
    try:
        # List versions mode
        if args.list_versions:
            versions = await list_versions(db)
            print("\nTC_AERONEFS VERSIONS:")
            print("-" * 50)
            for v in versions:
                print(f"  {v['tc_version']}: {v['count']} records (updated: {v['last_updated']})")
            if not versions:
                print("  No versions found")
            return
        
        # Rollback mode
        if args.rollback:
            confirm = input(f"Delete all records with version '{args.version}'? (yes/no): ")
            if confirm.lower() == "yes":
                deleted = await rollback_version(db, args.version, args.dry_run)
                print(f"Rollback complete: {deleted} records deleted")
            else:
                print("Rollback cancelled")
            return
        
        # Import mode
        data_dir = Path(args.data_dir)
        if not data_dir.is_absolute():
            # Try relative to script location
            script_dir = Path(__file__).parent.parent
            data_dir = script_dir / args.data_dir
        
        stats = await import_tc_registry(
            db=db,
            tc_version=args.version,
            data_dir=data_dir,
            limit=args.limit,
            dry_run=args.dry_run
        )
        
        # Output stats to file if requested
        if args.output:
            output_data = {
                "tc_version": args.version,
                "dry_run": args.dry_run,
                "timestamp": datetime.utcnow().isoformat(),
                "stats": stats.to_dict()
            }
            with open(args.output, 'w') as f:
                json.dump(output_data, f, indent=2, default=str)
            logger.info(f"Stats saved to {args.output}")
        
        # Print summary
        print("\n" + "=" * 60)
        print("IMPORT SUMMARY")
        print("=" * 60)
        print(json.dumps(stats.to_dict(), indent=2, default=str))
        
    finally:
        client.close()


if __name__ == "__main__":
    asyncio.run(main())
