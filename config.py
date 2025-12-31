from pydantic_settings import BaseSettings
from functools import lru_cache
import os

class Settings(BaseSettings):
    # MongoDB Configuration
    mongo_url: str
    db_name: str
    
    # Emergent LLM Key (OpenAI GPT-5.1 + Vision)
    emergent_llm_key: str
    
    # Stripe Configuration
    stripe_secret_key: str = "sk_test_placeholder"
    stripe_publishable_key: str = "pk_test_placeholder"
    stripe_webhook_secret: str = "whsec_placeholder"
    
    # Stripe Price IDs (configure in Stripe Dashboard)
    stripe_price_solo_monthly: str = "price_solo_monthly"
    stripe_price_solo_yearly: str = "price_solo_yearly"
    stripe_price_pro_monthly: str = "price_pro_monthly"
    stripe_price_pro_yearly: str = "price_pro_yearly"
    stripe_price_fleet_monthly: str = "price_fleet_monthly"
    stripe_price_fleet_yearly: str = "price_fleet_yearly"
    
    # Application Configuration
    domain: str = "http://localhost:8001"
    frontend_url: str = "http://localhost:3000"
    environment: str = "development"
    
    # JWT Configuration
    jwt_secret_key: str = "your-secret-key-change-in-production"
    jwt_algorithm: str = "HS256"
    jwt_expiration_minutes: int = 60 * 24 * 7  # 7 days
    
    def get_stripe_price_id(self, plan_id: str, billing_cycle: str) -> str:
        """Get Stripe price ID for a plan and billing cycle"""
        price_map = {
            ("solo", "monthly"): self.stripe_price_solo_monthly,
            ("solo", "yearly"): self.stripe_price_solo_yearly,
            ("pro", "monthly"): self.stripe_price_pro_monthly,
            ("pro", "yearly"): self.stripe_price_pro_yearly,
            ("fleet", "monthly"): self.stripe_price_fleet_monthly,
            ("fleet", "yearly"): self.stripe_price_fleet_yearly,
        }
        return price_map.get((plan_id, billing_cycle), "")
    
    class Config:
        env_file = ".env"
        case_sensitive = False

@lru_cache()
def get_settings():
    return Settings()
