"""
Transport Canada Aircraft Registry Lookup API

Provides read-only access to the tc_aircraft collection.
Privacy-compliant: Only public registration data is exposed.

CANONICAL TC REGISTRY ENDPOINTS:
- GET /api/tc/lookup?registration=C-FKZY
- GET /api/tc/search?prefix=C-FG
- GET /api/tc/stats

DATA SOURCE: MongoDB tc_aircraft collection (~34,000 aircraft)

FIELD MAPPING:
- DB fields are in FRENCH (faithful to TC source)
- API exposes ENGLISH canonical schema
- Mapping is centralized in map_tc_aircraft()

STRICT RULES:
- If a field is absent in DB → return null
- NO invented/calculated/deduced values
- NO silent fallbacks
- Backend = single source of truth for TC data
"""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from motor.motor_asyncio import AsyncIOMotorDatabase
from pydantic import BaseModel
from typing import Optional, List, Any
import re
import logging

from database.mongodb import get_database

router = APIRouter(prefix="/api/tc", tags=["transport-canada"])
logger = logging.getLogger(__name__)


# ============================================================
# FR → EN FIELD MAPPING (CENTRALIZED)
# ============================================================
# This is the SINGLE SOURCE OF TRUTH for field name translation
# DB fields (French) → API fields (English)
#
# ⚠️ RULES:
# - NO default values
# - NO business logic
# - NO transformations
# - None if absent

def map_tc_aircraft(doc: dict) -> dict:
    """
    Map TC aircraft document from French DB fields to English API fields.
    
    This function handles BOTH:
    - French field names (from real TC import)
    - English field names (legacy/test data)
    
    Returns dict with English canonical field names.
    None for any missing field - NO invented values.
    """
    if not doc:
        return {}
    
    def get_field(fr_key: str, en_key: str = None) -> Any:
        """Get field value, trying French key first, then English fallback."""
        value = doc.get(fr_key)
        if value is None and en_key:
            value = doc.get(en_key)
        return value
    
    return {
        # Primary identifiers
        "registration": get_field("inscription", "registration"),
        "registration_norm": get_field("norme d'enregistrement", "registration_norm"),
        
        # Aircraft identification
        "manufacturer": get_field("fabricant", "manufacturer"),
        "model": get_field("modèle", "model"),
        "designator": get_field("désignateur", "designator"),
        "serial_number": get_field("numéro_de_série", "serial_number"),
        
        # Technical specifications
        "year": get_field("date_fabrication", "year"),
        "category": get_field("catégorie_aéronef", "aircraft_category"),
        "engine_type": get_field("catégorie_moteur", "engine_category"),
        "engine_manufacturer": get_field("fabricant_de_moteurs", "engine_manufacturer"),
        "max_weight_kg": get_field("poids_kg", "weight_kg"),
        "number_of_engines": get_field("nombre_moteurs", "num_engines"),
        "number_of_seats": get_field("nombre_de_sièges", "num_seats"),
        
        # Location/Operations
        "base_province": get_field("province_de_base", "base_province"),
        "city_airport": get_field("city_airport", "aéroport de la ville"),
        
        # Owner info (public record only)
        "owner_name": get_field("nom_du_propriétaire", "owner_name"),
        "owner_city": get_field("ville du propriétaire", "owner_city"),
        "owner_province": get_field("propriétaire_province", "owner_province"),
        
        # Purpose and status
        "purpose": get_field("but", "purpose"),
        "status": get_field("statut", "status"),
        "country_of_manufacture": get_field("pays_fabrication", "country_manufacture"),
        
        # Dates
        "issued_date": get_field("date d'émission", "date_manufacture"),
        "last_modified": get_field("date_modifiée", "updated_at"),
    }


# ============================================================
# RESPONSE MODELS - CANONICAL ENGLISH SCHEMA
# ============================================================

class TCLookupResponse(BaseModel):
    """
    TC Aircraft lookup response - CANONICAL ENGLISH SCHEMA.
    
    All fields mapped from French DB fields.
    If a field is absent in DB → null is returned.
    NO invented values.
    """
    # Primary identifiers
    registration: Optional[str] = None
    registration_norm: Optional[str] = None
    
    # Aircraft identification
    manufacturer: Optional[str] = None
    model: Optional[str] = None
    designator: Optional[str] = None
    serial_number: Optional[str] = None
    
    # Technical specifications
    year: Optional[str] = None
    category: Optional[str] = None
    engine_type: Optional[str] = None
    engine_manufacturer: Optional[str] = None
    max_weight_kg: Optional[float] = None
    number_of_engines: Optional[int] = None
    number_of_seats: Optional[int] = None
    
    # Location/Operations
    base_province: Optional[str] = None
    city_airport: Optional[str] = None
    
    # Owner info
    owner_name: Optional[str] = None
    owner_city: Optional[str] = None
    owner_province: Optional[str] = None
    
    # Purpose and status
    purpose: Optional[str] = None
    status: Optional[str] = None
    country_of_manufacture: Optional[str] = None
    
    # Dates
    issued_date: Optional[str] = None
    last_modified: Optional[str] = None
    
    # Alias for compatibility
    type_certificate: Optional[str] = None


class TCSearchResult(BaseModel):
    """
    TC Aircraft search result - MINIMAL SCHEMA.
    
    Contains only essential fields for search results.
    NO calculated fields. NO example values.
    """
    registration: Optional[str] = None
    manufacturer: Optional[str] = None
    model: Optional[str] = None


class TCLookupError(BaseModel):
    """Error response for lookup failures"""
    error: str
    registration: str
    suggestion: Optional[str] = None


# ============================================================
# NORMALIZATION
# ============================================================

def normalize_registration(registration: str) -> str:
    """
    Normalize registration for database lookup.
    
    Accepts:
    - C-FKZY, CFKZY, c-fkzy, cfkzy
    - C-GABC, CGABC
    - FGSO (assumes C- prefix)
    
    Returns: CFGSO (uppercase, no hyphen) for database lookup
    """
    if not registration:
        raise ValueError("Registration is required")
    
    # Clean input
    reg = registration.strip().upper()
    
    # Remove hyphen
    reg = reg.replace("-", "")
    
    # Add C prefix if missing
    if not reg.startswith("C"):
        reg = "C" + reg
    
    # Validate length (C + 3-4 letters = 4-5 chars)
    if len(reg) < 4 or len(reg) > 5:
        raise ValueError("Invalid registration length")
    
    # Check letters only after C
    letters_part = reg[1:]
    if not letters_part.isalpha():
        raise ValueError("Registration must contain only letters after 'C'")
    
    return reg


def format_registration(norm_reg: str) -> str:
    """
    Format normalized registration for display.
    
    Input: CFGSO
    Output: C-FGSO
    """
    if len(norm_reg) <= 1:
        return norm_reg
    return f"C-{norm_reg[1:]}"


def format_date(date_value) -> Optional[str]:
    """Format date to ISO string or return None."""
    if date_value is None:
        return None
    try:
        if hasattr(date_value, 'strftime'):
            return date_value.strftime("%Y-%m-%d")
        return str(date_value)
    except Exception:
        return None


# ============================================================
# ENDPOINTS
# ============================================================

@router.get(
    "/lookup",
    response_model=TCLookupResponse,
    responses={
        404: {"model": TCLookupError, "description": "Aircraft not found"},
        400: {"model": TCLookupError, "description": "Invalid registration format"},
    },
    summary="Lookup aircraft by registration",
    description="""
    Search the Transport Canada Civil Aircraft Register by registration.
    
    **Input formats accepted:**
    - C-FKZY (standard)
    - CFKZY (without hyphen)
    - c-fkzy (lowercase)
    - FKZY (without C- prefix)
    
    **Returns:** Public registration data from TC Registry.
    
    **FIELD MAPPING:** DB fields (French) are mapped to English API schema.
    """
)
async def lookup_aircraft(
    registration: str = Query(
        ...,
        description="Aircraft registration (e.g., C-FKZY, CFKZY, FKZY)",
        min_length=3,
        max_length=7,
        examples=["C-FKZY", "CFKZY", "C-GABC", "FGSO"]
    ),
    db: AsyncIOMotorDatabase = Depends(get_database)
):
    """
    Lookup an aircraft in the Transport Canada registry.
    
    Uses centralized FR→EN mapping.
    """
    # Validate and normalize registration
    try:
        registration_norm = normalize_registration(registration)
    except ValueError as e:
        logger.warning(f"[TC LOOKUP] Invalid format: {registration} - {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": str(e),
                "registration": registration,
                "suggestion": "Format: C-XXXX (e.g., C-FKZY) or XXXX (e.g., FKZY)"
            }
        )
    
    # Lookup in tc_aircraft collection
    # Try both French and English field names for registration_norm
    doc = await db.tc_aircraft.find_one({
        "$or": [
            {"norme d'enregistrement": registration_norm},
            {"registration_norm": registration_norm}
        ]
    })
    
    if not doc:
        display_reg = format_registration(registration_norm)
        logger.info(f"[TC LOOKUP] Not found: {display_reg}")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": "Aircraft not found in Transport Canada registry",
                "registration": display_reg,
                "suggestion": "Verify the registration or check Transport Canada directly"
            }
        )
    
    # Apply centralized FR→EN mapping
    mapped = map_tc_aircraft(doc)
    
    # Format dates
    if mapped.get("issued_date"):
        mapped["issued_date"] = format_date(mapped["issued_date"])
    if mapped.get("last_modified"):
        mapped["last_modified"] = format_date(mapped["last_modified"])
    
    # Set type_certificate alias
    mapped["type_certificate"] = mapped.get("designator")
    
    # AUDIT LOG - List non-null fields
    non_null_fields = [k for k, v in mapped.items() if v is not None]
    logger.info(
        f"[TC LOOKUP MAPPED] {mapped.get('registration')} | "
        f"non_null_fields={non_null_fields}"
    )
    
    return TCLookupResponse(**mapped)


@router.get(
    "/search",
    response_model=List[TCSearchResult],
    summary="Search aircraft by partial registration",
    description="""
    Search aircraft by registration prefix.
    
    **Example:** `/api/tc/search?prefix=C-FG` returns all aircraft starting with C-FG.
    
    **Returns:** Minimal fields only (registration, manufacturer, model).
    """
)
async def search_aircraft(
    prefix: str = Query(
        ...,
        description="Registration prefix (e.g., C-FG, CFG, FG)",
        min_length=1,
        max_length=5
    ),
    limit: int = Query(
        default=20,
        ge=1,
        le=50,
        description="Maximum results to return"
    ),
    db: AsyncIOMotorDatabase = Depends(get_database)
):
    """
    Search aircraft by registration prefix.
    Uses centralized FR→EN mapping.
    """
    # Normalize prefix for search
    prefix_norm = prefix.strip().upper().replace("-", "")
    
    # Add C if not present
    if not prefix_norm.startswith("C"):
        prefix_norm = "C" + prefix_norm
    
    # Search using both French and English field names
    pattern = f"^{re.escape(prefix_norm)}"
    
    cursor = db.tc_aircraft.find({
        "$or": [
            {"norme d'enregistrement": {"$regex": pattern}},
            {"registration_norm": {"$regex": pattern}}
        ]
    }).limit(limit)
    
    results = []
    async for doc in cursor:
        mapped = map_tc_aircraft(doc)
        results.append(TCSearchResult(
            registration=mapped.get("registration"),
            manufacturer=mapped.get("manufacturer"),
            model=mapped.get("model"),
        ))
    
    # Sort by registration
    results.sort(key=lambda x: x.registration or "")
    
    # AUDIT LOG
    logger.info(f"[TC SEARCH] prefix={prefix_norm} | results={len(results)}")
    
    return results


@router.get(
    "/stats",
    summary="Get TC registry statistics",
    description="Returns statistics about the tc_aircraft collection."
)
async def get_tc_stats(
    db: AsyncIOMotorDatabase = Depends(get_database)
):
    """
    Get statistics about the Transport Canada registry.
    """
    # Total count
    total = await db.tc_aircraft.count_documents({})
    
    # Get sample to check field format
    sample = await db.tc_aircraft.find_one({})
    
    # Detect if data is in French or English
    data_format = "unknown"
    if sample:
        if "fabricant" in sample:
            data_format = "french"
        elif "manufacturer" in sample:
            data_format = "english"
    
    # Get import date (try both field names)
    import_date = None
    if sample:
        date_val = sample.get("tc_import_date") or sample.get("date_importation")
        if date_val and hasattr(date_val, 'strftime'):
            import_date = date_val.strftime("%Y-%m-%d %H:%M")
    
    # Log audit
    logger.info(f"[TC STATS] total={total} | data_format={data_format}")
    
    # Top manufacturers (handle both FR and EN)
    manufacturer_field = "fabricant" if data_format == "french" else "manufacturer"
    pipeline = [
        {"$group": {"_id": f"${manufacturer_field}", "count": {"$sum": 1}}},
        {"$sort": {"count": -1}},
        {"$limit": 10}
    ]
    top_manufacturers = []
    async for doc in db.tc_aircraft.aggregate(pipeline):
        if doc["_id"]:
            top_manufacturers.append({"manufacturer": doc["_id"], "count": doc["count"]})
    
    return {
        "total_aircraft": total,
        "import_date": import_date,
        "data_format": data_format,
        "top_manufacturers": top_manufacturers,
        "note": "Source: Transport Canada Civil Aircraft Register"
    }
