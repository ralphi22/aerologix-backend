"""
Script to create Stripe Products and Prices for AeroLogix AI

OFFICIAL PRICING (CAD):
- PILOT: $24/month or $240/year
- PILOT_PRO: $39/month or $390/year
- FLEET: $65/month or $650/year

Run once to set up the subscription products in Stripe:
    python scripts/setup_stripe_products.py
"""

import stripe
import os
from dotenv import load_dotenv

load_dotenv()

stripe.api_key = os.getenv("STRIPE_SECRET_KEY")


# ============================================================
# OFFICIAL PRODUCT DEFINITIONS
# ============================================================

PRODUCTS = [
    {
        "name": "Pilot",
        "description": "AeroLogix Pilot - Pour pilotes propriétaires (1 aéronef)",
        "metadata": {"plan_code": "PILOT"},
        "prices": [
            {
                "unit_amount": 2400,  # $24.00 CAD
                "currency": "cad",
                "interval": "month",
                "lookup_key": "pilot_monthly"
            },
            {
                "unit_amount": 24000,  # $240.00 CAD
                "currency": "cad",
                "interval": "year",
                "lookup_key": "pilot_yearly"
            },
        ]
    },
    {
        "name": "Pilot Pro",
        "description": "AeroLogix Pilot Pro - Pour pilotes avec plusieurs aéronefs (jusqu'à 3)",
        "metadata": {"plan_code": "PILOT_PRO"},
        "prices": [
            {
                "unit_amount": 3900,  # $39.00 CAD
                "currency": "cad",
                "interval": "month",
                "lookup_key": "pilot_pro_monthly"
            },
            {
                "unit_amount": 39000,  # $390.00 CAD
                "currency": "cad",
                "interval": "year",
                "lookup_key": "pilot_pro_yearly"
            },
        ]
    },
    {
        "name": "Fleet",
        "description": "AeroLogix Fleet - Pour gestionnaires de flotte (aéronefs illimités)",
        "metadata": {"plan_code": "FLEET"},
        "prices": [
            {
                "unit_amount": 6500,  # $65.00 CAD
                "currency": "cad",
                "interval": "month",
                "lookup_key": "fleet_monthly"
            },
            {
                "unit_amount": 65000,  # $650.00 CAD
                "currency": "cad",
                "interval": "year",
                "lookup_key": "fleet_yearly"
            },
        ]
    }
]


def create_stripe_products():
    """Create products and prices in Stripe"""
    
    print("=" * 60)
    print("AEROLOGIX AI - STRIPE PRODUCT SETUP")
    print("=" * 60)
    
    if not stripe.api_key or stripe.api_key == "sk_test_placeholder":
        print("\n❌ ERROR: STRIPE_SECRET_KEY not configured in .env")
        print("   Please set a valid Stripe API key first.")
        return None
    
    price_ids = {}
    
    for product_def in PRODUCTS:
        print(f"\n📦 Creating product: {product_def['name']}")
        
        # Check if product already exists
        existing_products = stripe.Product.search(
            query=f"metadata['plan_code']:'{product_def['metadata']['plan_code']}'"
        )
        
        if existing_products.data:
            product = existing_products.data[0]
            print(f"   ✓ Product already exists: {product.id}")
        else:
            product = stripe.Product.create(
                name=product_def["name"],
                description=product_def["description"],
                metadata=product_def["metadata"]
            )
            print(f"   ✓ Created product: {product.id}")
        
        # Create prices
        for price_def in product_def["prices"]:
            lookup_key = price_def["lookup_key"]
            
            # Check if price already exists
            existing_prices = stripe.Price.list(
                product=product.id,
                lookup_keys=[lookup_key]
            )
            
            if existing_prices.data:
                price = existing_prices.data[0]
                print(f"   💰 Price already exists ({lookup_key}): {price.id}")
            else:
                price = stripe.Price.create(
                    product=product.id,
                    unit_amount=price_def["unit_amount"],
                    currency=price_def["currency"],
                    recurring={"interval": price_def["interval"]},
                    lookup_key=lookup_key,
                    metadata={"lookup_key": lookup_key, "plan_code": product_def["metadata"]["plan_code"]}
                )
                print(f"   💰 Created price ({lookup_key}): {price.id}")
            
            price_ids[lookup_key] = price.id
    
    print("\n" + "=" * 60)
    print("✅ SETUP COMPLETE")
    print("=" * 60)
    print("\nAdd these Price IDs to your .env file:\n")
    
    print(f"# Stripe Price IDs (UNIFIED PLAN_CODE SYSTEM)")
    print(f"STRIPE_PRICE_PILOT_MONTHLY={price_ids.get('pilot_monthly', 'NOT_CREATED')}")
    print(f"STRIPE_PRICE_PILOT_YEARLY={price_ids.get('pilot_yearly', 'NOT_CREATED')}")
    print(f"STRIPE_PRICE_PILOT_PRO_MONTHLY={price_ids.get('pilot_pro_monthly', 'NOT_CREATED')}")
    print(f"STRIPE_PRICE_PILOT_PRO_YEARLY={price_ids.get('pilot_pro_yearly', 'NOT_CREATED')}")
    print(f"STRIPE_PRICE_FLEET_MONTHLY={price_ids.get('fleet_monthly', 'NOT_CREATED')}")
    print(f"STRIPE_PRICE_FLEET_YEARLY={price_ids.get('fleet_yearly', 'NOT_CREATED')}")
    
    return price_ids


if __name__ == "__main__":
    create_stripe_products()
