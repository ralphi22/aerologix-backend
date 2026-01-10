"""
Migration Script: Unified Plan Code System

This script migrates existing users and subscriptions to the new unified plan_code system.

MAPPING:
- BASIC -> BASIC
- PILOT -> PILOT
- MAINTENANCE_PRO -> PILOT_PRO
- FLEET_AI -> FLEET
- solo -> PILOT
- pro -> PILOT_PRO
- fleet -> FLEET

Run with: python scripts/migrate_plan_codes.py
"""

import asyncio
import os
import sys
from datetime import datetime

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

load_dotenv()


# Plan code mapping
LEGACY_PLAN_MAPPING = {
    # Old PlanTier values
    "BASIC": "BASIC",
    "PILOT": "PILOT",
    "MAINTENANCE_PRO": "PILOT_PRO",
    "FLEET_AI": "FLEET",
    
    # Old PlanType values (lowercase)
    "solo": "PILOT",
    "pro": "PILOT_PRO",
    "fleet": "FLEET",
}


# Limits by plan_code
PLAN_LIMITS = {
    "BASIC": {
        "max_aircrafts": 1,
        "ocr_per_month": 5,
        "gps_logbook": False,
        "tea_amo_sharing": False,
        "invoices": False,
        "cost_per_hour": False,
        "prebuy": False,
    },
    "PILOT": {
        "max_aircrafts": 1,
        "ocr_per_month": 10,
        "gps_logbook": True,
        "tea_amo_sharing": True,
        "invoices": True,
        "cost_per_hour": True,
        "prebuy": False,
    },
    "PILOT_PRO": {
        "max_aircrafts": 3,
        "ocr_per_month": -1,
        "gps_logbook": True,
        "tea_amo_sharing": True,
        "invoices": True,
        "cost_per_hour": True,
        "prebuy": False,
    },
    "FLEET": {
        "max_aircrafts": -1,
        "ocr_per_month": -1,
        "gps_logbook": True,
        "tea_amo_sharing": True,
        "invoices": True,
        "cost_per_hour": True,
        "prebuy": True,
    },
}


def normalize_plan_code(legacy_value: str) -> str:
    """Convert legacy plan value to new plan_code"""
    if not legacy_value:
        return "BASIC"
    
    # Already new format?
    if legacy_value in ["BASIC", "PILOT", "PILOT_PRO", "FLEET"]:
        return legacy_value
    
    # Try mapping
    return LEGACY_PLAN_MAPPING.get(legacy_value, "BASIC")


async def migrate_users(db):
    """Migrate all users to new plan_code system"""
    
    print("\n" + "="*60)
    print("MIGRATING USERS")
    print("="*60)
    
    users = await db.users.find({}).to_list(length=10000)
    
    migrated = 0
    skipped = 0
    
    for user in users:
        user_id = user["_id"]
        email = user.get("email", "unknown")
        
        # Get current plan info
        subscription = user.get("subscription", {})
        old_plan = subscription.get("plan", "BASIC")
        existing_plan_code = subscription.get("plan_code")
        
        # Skip if already has plan_code
        if existing_plan_code:
            print(f"  SKIP: {email} - already has plan_code={existing_plan_code}")
            skipped += 1
            continue
        
        # Check for active Stripe subscription
        active_sub = await db.subscriptions.find_one({
            "user_id": user_id,
            "status": {"$in": ["active", "trialing"]}
        })
        
        if active_sub:
            # Use plan from active subscription
            sub_plan = active_sub.get("plan_code") or active_sub.get("plan_id")
            new_plan_code = normalize_plan_code(sub_plan) if sub_plan else normalize_plan_code(old_plan)
        else:
            # Use plan from user document
            new_plan_code = normalize_plan_code(old_plan)
        
        # Get limits for new plan_code
        limits = PLAN_LIMITS.get(new_plan_code, PLAN_LIMITS["BASIC"])
        
        # Update user
        update_result = await db.users.update_one(
            {"_id": user_id},
            {"$set": {
                "subscription.plan_code": new_plan_code,
                "limits.max_aircrafts": limits["max_aircrafts"],
                "limits.ocr_per_month": limits["ocr_per_month"],
                "limits.gps_logbook": limits["gps_logbook"],
                "limits.tea_amo_sharing": limits["tea_amo_sharing"],
                "limits.invoices": limits["invoices"],
                "limits.cost_per_hour": limits["cost_per_hour"],
                "limits.prebuy": limits["prebuy"],
                "updated_at": datetime.utcnow()
            }}
        )
        
        if update_result.modified_count > 0:
            print(f"  OK: {email} - {old_plan} -> {new_plan_code}")
            migrated += 1
        else:
            print(f"  WARN: {email} - no changes made")
    
    print(f"\nUsers: migrated={migrated}, skipped={skipped}, total={len(users)}")
    return migrated


async def migrate_subscriptions(db):
    """Migrate all subscriptions to use plan_code"""
    
    print("\n" + "="*60)
    print("MIGRATING SUBSCRIPTIONS")
    print("="*60)
    
    subscriptions = await db.subscriptions.find({}).to_list(length=10000)
    
    migrated = 0
    skipped = 0
    
    for sub in subscriptions:
        sub_id = sub["_id"]
        
        # Get current plan info
        old_plan_id = sub.get("plan_id")
        existing_plan_code = sub.get("plan_code")
        
        # Skip if already has plan_code
        if existing_plan_code:
            print(f"  SKIP: {sub_id} - already has plan_code={existing_plan_code}")
            skipped += 1
            continue
        
        # Convert old plan_id to new plan_code
        new_plan_code = normalize_plan_code(old_plan_id) if old_plan_id else "BASIC"
        
        # Update subscription
        update_result = await db.subscriptions.update_one(
            {"_id": sub_id},
            {"$set": {
                "plan_code": new_plan_code,
                "updated_at": datetime.utcnow()
            }}
        )
        
        if update_result.modified_count > 0:
            print(f"  OK: {sub_id} - {old_plan_id} -> {new_plan_code}")
            migrated += 1
        else:
            print(f"  WARN: {sub_id} - no changes made")
    
    print(f"\nSubscriptions: migrated={migrated}, skipped={skipped}, total={len(subscriptions)}")
    return migrated


async def verify_migration(db):
    """Verify migration was successful"""
    
    print("\n" + "="*60)
    print("VERIFICATION")
    print("="*60)
    
    # Check users without plan_code
    users_missing = await db.users.count_documents({
        "subscription.plan_code": {"$exists": False}
    })
    
    # Check subscriptions without plan_code
    subs_missing = await db.subscriptions.count_documents({
        "plan_code": {"$exists": False}
    })
    
    print(f"  Users without plan_code: {users_missing}")
    print(f"  Subscriptions without plan_code: {subs_missing}")
    
    if users_missing == 0 and subs_missing == 0:
        print("\n✅ MIGRATION COMPLETE - All documents have plan_code")
    else:
        print("\n⚠️  MIGRATION INCOMPLETE - Some documents need attention")
    
    return users_missing == 0 and subs_missing == 0


async def main():
    """Run migration"""
    
    print("="*60)
    print("AEROLOGIX AI - PLAN CODE MIGRATION")
    print("="*60)
    
    # Connect to MongoDB
    mongo_url = os.getenv("MONGO_URL", "mongodb://localhost:27017")
    db_name = os.getenv("DB_NAME", "aerologix")
    
    client = AsyncIOMotorClient(mongo_url)
    db = client[db_name]
    
    print(f"\nConnected to: {mongo_url}/{db_name}")
    
    # Run migrations
    await migrate_users(db)
    await migrate_subscriptions(db)
    
    # Verify
    success = await verify_migration(db)
    
    # Close connection
    client.close()
    
    return 0 if success else 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
