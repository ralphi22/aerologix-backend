"""
Transport Canada Aircraft Registry Lookup API

Provides read-only access to the tc_aircraft collection.
Privacy-compliant: Only public registration data is exposed.

CANONICAL TC REGISTRY ENDPOINTS:
- GET /api/tc/lookup?registration=C-FKZY
- GET /api/tc/search?prefix=C-FG
- GET /api/tc/stats

DATA SOURCE: MongoDB tc_aircraft collection (~34,000 aircraft)

STRICT RULES:
- If a field is absent in DB → return null
- NO invented/calculated/deduced values
- NO silent fallbacks
- Backend = single source of truth for TC data
"""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from motor.motor_asyncio import AsyncIOMotorDatabase
from pydantic import BaseModel
from typing import Optional, List
import re
import logging

from database.mongodb import get_database

router = APIRouter(prefix="/api/tc", tags=["transport-canada"])
logger = logging.getLogger(__name__)


# ============================================================
# RESPONSE MODELS - NORMALIZED SCHEMA
# ============================================================
# These models define the EXACT fields returned by the API
# All fields are Optional - if absent in DB, return null

class TCLookupResponse(BaseModel):
    """
    TC Aircraft lookup response - NORMALIZED SCHEMA.
    
    Contains only fields that exist in the tc_aircraft collection.
    If a field is absent in DB → null is returned.
    NO invented values.
    """
    # Primary identifiers
    registration: str
    registration_norm: Optional[str] = None
    
    # Aircraft identification
    manufacturer: Optional[str] = None
    model: Optional[str] = None
    designator: Optional[str] = None  # Type certificate (e.g., "3A19")
    serial_number: Optional[str] = None
    
    # Technical specifications
    year: Optional[int] = None
    category: Optional[str] = None  # Aircraft category (e.g., "Normal")
    engine_type: Optional[str] = None  # Engine type (e.g., "Piston")
    max_weight_kg: Optional[float] = None
    num_engines: Optional[int] = None
    num_seats: Optional[int] = None
    
    # Location/Operations
    base_of_operations: Optional[str] = None  # e.g., "CSG3 - Joliette, Québec, CANADA"
    
    # Owner info (public record only)
    owner_name: Optional[str] = None
    
    # Type certificate reference
    type_certificate: Optional[str] = None  # Alias for designator
    
    # Status
    status: Optional[str] = None


class TCSearchResult(BaseModel):
    """
    TC Aircraft search result - MINIMAL SCHEMA.
    
    Contains only essential fields for search results.
    NO calculated fields. NO example values.
    """
    registration: str
    manufacturer: Optional[str] = None
    model: Optional[str] = None


class TCLookupError(BaseModel):
    """Error response for lookup failures"""
    error: str
    registration: str
    suggestion: Optional[str] = None


# ============================================================
# FIELD MAPPING - DB TO API
# ============================================================
# Maps database field names to API response field names
# This ensures stable API contracts even if DB schema changes

DB_TO_API_FIELD_MAP = {
    # Direct mappings
    "registration": "registration",
    "registration_norm": "registration_norm",
    "manufacturer": "manufacturer",
    "model": "model",
    "designator": "designator",
    "serial_number": "serial_number",
    "owner_name": "owner_name",
    "status": "status",
    "num_engines": "num_engines",
    "num_seats": "num_seats",
    
    # Mapped names (DB name → API name)
    "aircraft_category": "category",
    "engine_category": "engine_type",
    "weight_kg": "max_weight_kg",
    "date_manufacture": "year",  # Will be converted to year integer
    "city_airport": "base_of_operations",
}


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


def extract_year_from_date(date_value) -> Optional[int]:
    """
    Extract year from date field.
    
    Returns integer year or None if invalid/absent.
    """
    if date_value is None:
        return None
    
    try:
        if hasattr(date_value, 'year'):
            return date_value.year
        if isinstance(date_value, str) and len(date_value) >= 4:
            return int(date_value[:4])
    except (ValueError, TypeError):
        pass
    
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
    
    **STRICT RULES:**
    - If a field is absent in DB → null is returned
    - NO invented/calculated values
    - NO silent fallbacks
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
    
    Read-only endpoint. No authentication required.
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
    
    # Lookup in tc_aircraft collection by registration_norm
    aircraft = await db.tc_aircraft.find_one(
        {"registration_norm": registration_norm},
        {"_id": 0}  # Exclude MongoDB _id
    )
    
    if not aircraft:
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
    
    # Build response from DB fields - NO INVENTED VALUES
    fields_returned = []
    
    # Extract values from DB document
    reg = aircraft.get("registration")
    reg_norm = aircraft.get("registration_norm")
    manufacturer = aircraft.get("manufacturer")
    model = aircraft.get("model")
    designator = aircraft.get("designator")
    serial_number = aircraft.get("serial_number")
    owner_name = aircraft.get("owner_name")
    status_val = aircraft.get("status")
    num_engines = aircraft.get("num_engines")
    num_seats = aircraft.get("num_seats")
    
    # Mapped fields
    category = aircraft.get("aircraft_category")
    engine_type = aircraft.get("engine_category")
    max_weight_kg = aircraft.get("weight_kg")
    base_of_operations = aircraft.get("city_airport")
    
    # Year extraction from date_manufacture
    year = extract_year_from_date(aircraft.get("date_manufacture"))
    
    # Track which fields have values
    for field, value in [
        ("registration", reg),
        ("registration_norm", reg_norm),
        ("manufacturer", manufacturer),
        ("model", model),
        ("designator", designator),
        ("serial_number", serial_number),
        ("owner_name", owner_name),
        ("status", status_val),
        ("num_engines", num_engines),
        ("num_seats", num_seats),
        ("category", category),
        ("engine_type", engine_type),
        ("max_weight_kg", max_weight_kg),
        ("base_of_operations", base_of_operations),
        ("year", year),
    ]:
        if value is not None:
            fields_returned.append(field)
    
    # AUDIT LOG - List all fields returned
    logger.info(
        f"[TC LOOKUP] registration={reg} | "
        f"fields_returned=[{', '.join(fields_returned)}]"
    )
    
    # Build response - type_certificate is alias for designator
    return TCLookupResponse(
        registration=reg,
        registration_norm=reg_norm,
        manufacturer=manufacturer,
        model=model,
        designator=designator,
        serial_number=serial_number,
        year=year,
        category=category,
        engine_type=engine_type,
        max_weight_kg=max_weight_kg,
        num_engines=num_engines,
        num_seats=num_seats,
        base_of_operations=base_of_operations,
        owner_name=owner_name,
        type_certificate=designator,  # Alias for designator
        status=status_val,
    )


@router.get(
    "/search",
    response_model=List[TCSearchResult],
    summary="Search aircraft by partial registration",
    description="""
    Search aircraft by registration prefix.
    
    **Example:** `/api/tc/search?prefix=C-FG` returns all aircraft starting with C-FG.
    
    **Input formats:**
    - C-FG, CFG, FG (all equivalent)
    - Case insensitive
    
    **Returns:** Minimal fields only (registration, manufacturer, model).
    NO calculated fields. NO example values.
    
    **Limit:** Maximum 50 results (default 20).
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
    
    Read-only endpoint. No authentication required.
    Returns MINIMAL fields only.
    """
    # Normalize prefix for search
    prefix_norm = prefix.strip().upper().replace("-", "")
    
    # Add C if not present
    if not prefix_norm.startswith("C"):
        prefix_norm = "C" + prefix_norm
    
    # Search using registration_norm with regex prefix match
    pattern = f"^{re.escape(prefix_norm)}"
    
    # Fetch ONLY minimal fields - NO extra data
    cursor = db.tc_aircraft.find(
        {"registration_norm": {"$regex": pattern}},
        {
            "_id": 0,
            "registration": 1,
            "manufacturer": 1,
            "model": 1,
        }
    ).sort("registration_norm", 1).limit(limit)
    
    results = []
    async for doc in cursor:
        results.append(TCSearchResult(
            registration=doc.get("registration"),
            manufacturer=doc.get("manufacturer"),
            model=doc.get("model"),
        ))
    
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
    
    # Get import date
    sample = await db.tc_aircraft.find_one({}, {"tc_import_date": 1})
    import_date = None
    if sample and sample.get("tc_import_date"):
        import_date = sample["tc_import_date"].strftime("%Y-%m-%d %H:%M")
    
    # Log audit
    logger.info(f"[TC STATS] total_aircraft={total}")
    
    # Top manufacturers
    pipeline = [
        {"$group": {"_id": "$manufacturer", "count": {"$sum": 1}}},
        {"$sort": {"count": -1}},
        {"$limit": 10}
    ]
    top_manufacturers = []
    async for doc in db.tc_aircraft.aggregate(pipeline):
        if doc["_id"]:  # Skip null manufacturers
            top_manufacturers.append({"manufacturer": doc["_id"], "count": doc["count"]})
    
    # Aircraft categories
    cat_pipeline = [
        {"$group": {"_id": "$aircraft_category", "count": {"$sum": 1}}},
        {"$sort": {"count": -1}}
    ]
    categories = []
    async for doc in db.tc_aircraft.aggregate(cat_pipeline):
        if doc["_id"]:  # Skip null categories
            categories.append({"category": doc["_id"], "count": doc["count"]})
    
    return {
        "total_aircraft": total,
        "import_date": import_date,
        "top_manufacturers": top_manufacturers,
        "aircraft_categories": categories,
        "note": "Source: Transport Canada Civil Aircraft Register"
    }
