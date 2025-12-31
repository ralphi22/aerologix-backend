"""
Script to create Stripe Products and Prices for AeroLogix AI
Run once to set up the subscription products in Stripe.
"""

import stripe
import os
from dotenv import load_dotenv

load_dotenv()

stripe.api_key = os.getenv("STRIPE_SECRET_KEY")

# Product definitions
PRODUCTS = [
    {
        "name": "Solo",
        "description": "Plan Solo - 1 aéronef, synchronisation de base",
        "metadata": {"plan_id": "solo"},
        "prices": [
            {"unit_amount": 999, "currency": "cad", "interval": "month", "lookup_key": "solo_monthly"},
            {"unit_amount": 9990, "currency": "cad", "interval": "year", "lookup_key": "solo_yearly"},
        ]
    },
    {
        "name": "Maintenance Pro",
        "description": "Plan Pro - Jusqu'à 3 aéronefs, partage TEA/AMO",
        "metadata": {"plan_id": "pro"},
        "prices": [
            {"unit_amount": 2499, "currency": "cad", "interval": "month", "lookup_key": "pro_monthly"},
            {"unit_amount": 24990, "currency": "cad", "interval": "year", "lookup_key": "pro_yearly"},
        ]
    },
    {
        "name": "Fleet AI",
        "description": "Plan Fleet - Aéronefs illimités, vue Fleet complète",
        "metadata": {"plan_id": "fleet"},
        "prices": [
            {"unit_amount": 7999, "currency": "cad", "interval": "month", "lookup_key": "fleet_monthly"},
            {"unit_amount": 79990, "currency": "cad", "interval": "year", "lookup_key": "fleet_yearly"},
        ]
    }
]


def create_stripe_products():
    """Create products and prices in Stripe"""
    
    print("=" * 50)
    print("Creating Stripe Products and Prices")
    print("=" * 50)
    
    price_ids = {}
    
    for product_def in PRODUCTS:
        print(f"\n📦 Creating product: {product_def['name']}")
        
        # Check if product already exists
        existing_products = stripe.Product.search(
            query=f"metadata['plan_id']:'{product_def['metadata']['plan_id']}'"
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
                    metadata={"lookup_key": lookup_key}
                )
                print(f"   💰 Created price ({lookup_key}): {price.id}")
            
            price_ids[lookup_key] = price.id
    
    print("\n" + "=" * 50)
    print("✅ Setup Complete!")
    print("=" * 50)
    print("\nAdd these Price IDs to your .env file:\n")
    
    print(f"STRIPE_PRICE_SOLO_MONTHLY={price_ids.get('solo_monthly', 'NOT_CREATED')}")
    print(f"STRIPE_PRICE_SOLO_YEARLY={price_ids.get('solo_yearly', 'NOT_CREATED')}")
    print(f"STRIPE_PRICE_PRO_MONTHLY={price_ids.get('pro_monthly', 'NOT_CREATED')}")
    print(f"STRIPE_PRICE_PRO_YEARLY={price_ids.get('pro_yearly', 'NOT_CREATED')}")
    print(f"STRIPE_PRICE_FLEET_MONTHLY={price_ids.get('fleet_monthly', 'NOT_CREATED')}")
    print(f"STRIPE_PRICE_FLEET_YEARLY={price_ids.get('fleet_yearly', 'NOT_CREATED')}")
    
    return price_ids


if __name__ == "__main__":
    create_stripe_products()
