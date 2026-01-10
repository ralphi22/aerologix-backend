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
    
    # ============================================================
    # NEW: Unified Stripe Price IDs (plan_code based)
    # ============================================================
    stripe_price_pilot_monthly: str = ""
    stripe_price_pilot_yearly: str = ""
    stripe_price_pilot_pro_monthly: str = ""
    stripe_price_pilot_pro_yearly: str = ""
    stripe_price_fleet_monthly: str = ""
    stripe_price_fleet_yearly: str = ""
    
    # DEPRECATED: Legacy price IDs (kept for backward compatibility)
    stripe_price_solo_monthly: str = ""
    stripe_price_solo_yearly: str = ""
    stripe_price_pro_monthly: str = ""
    stripe_price_pro_yearly: str = ""
    
    # Application Configuration
    domain: str = "http://localhost:8001"
    frontend_url: str = "http://localhost:3000"
    environment: str = "development"
    
    # JWT Configuration
    jwt_secret_key: str = "your-secret-key-change-in-production"
    jwt_algorithm: str = "HS256"
    jwt_expiration_minutes: int = 60 * 24 * 7  # 7 days
    
    def get_stripe_price_id(self, plan_code: str, billing_cycle: str) -> str:
        """
        Get Stripe price ID for a plan_code and billing_cycle.
        
        NEW: Uses plan_code (PILOT, PILOT_PRO, FLEET)
        LEGACY: Also supports old values (solo, pro, fleet) for migration
        
        Returns empty string if plan is BASIC (free) or not found.
        """
        # New unified mapping (plan_code)
        new_price_map = {
            ("PILOT", "monthly"): self.stripe_price_pilot_monthly,
            ("PILOT", "yearly"): self.stripe_price_pilot_yearly,
            ("PILOT_PRO", "monthly"): self.stripe_price_pilot_pro_monthly,
            ("PILOT_PRO", "yearly"): self.stripe_price_pilot_pro_yearly,
            ("FLEET", "monthly"): self.stripe_price_fleet_monthly,
            ("FLEET", "yearly"): self.stripe_price_fleet_yearly,
        }
        
        # Legacy mapping (for backward compatibility during migration)
        legacy_price_map = {
            ("solo", "monthly"): self.stripe_price_pilot_monthly or self.stripe_price_solo_monthly,
            ("solo", "yearly"): self.stripe_price_pilot_yearly or self.stripe_price_solo_yearly,
            ("pro", "monthly"): self.stripe_price_pilot_pro_monthly or self.stripe_price_pro_monthly,
            ("pro", "yearly"): self.stripe_price_pilot_pro_yearly or self.stripe_price_pro_yearly,
            ("fleet", "monthly"): self.stripe_price_fleet_monthly,
            ("fleet", "yearly"): self.stripe_price_fleet_yearly,
        }
        
        # Try new mapping first
        price_id = new_price_map.get((plan_code, billing_cycle))
        if price_id:
            return price_id
        
        # Fallback to legacy mapping
        price_id = legacy_price_map.get((plan_code.lower(), billing_cycle))
        if price_id:
            return price_id
        
        # BASIC plan or not found - return empty
        return ""
    
    class Config:
        env_file = ".env"
        case_sensitive = False

@lru_cache()
def get_settings():
    return Settings()
