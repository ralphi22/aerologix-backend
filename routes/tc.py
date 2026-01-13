"""
Transport Canada Aircraft Registry Lookup API

Provides read-only access to the tc_aircraft collection.
Privacy-compliant: Only public registration data is exposed.

Endpoints:
- GET /api/tc/lookup?registration=C-FKZY
- GET /api/tc/search?prefix=C-FG
- GET /api/tc/stats
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
# RESPONSE MODELS
# ============================================================

class TCAircraftResponse(BaseModel):
    """
    TC Aircraft lookup response.
    
    Contains only public registration data.
    NO personal addresses exposed.
    """
    registration: str
    manufacturer: Optional[str] = None
    model: Optional[str] = None
    designator: Optional[str] = None
    serial_number: Optional[str] = None
    owner_name: Optional[str] = None
    owner_city: Optional[str] = None
    owner_province: Optional[str] = None
    aircraft_category: Optional[str] = None
    num_engines: Optional[int] = None
    num_seats: Optional[int] = None
    status: Optional[str] = None


class TCAircraftDetailResponse(TCAircraftResponse):
    """Detailed response with additional fields"""
    engine_manufacturer: Optional[str] = None
    engine_category: Optional[str] = None
    weight_kg: Optional[float] = None
    country_manufacture: Optional[str] = None
    date_manufacture: Optional[str] = None
    base_province: Optional[str] = None
    city_airport: Optional[str] = None
    purpose: Optional[str] = None


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


# ============================================================
# ENDPOINTS
# ============================================================

@router.get(
    "/lookup",
    response_model=TCAircraftDetailResponse,
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
    
    **Returns:** Public registration data (manufacturer, model, serial, owner name).
    
    **Privacy:** No personal addresses are returned.
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
        logger.warning(f"TC Lookup - Invalid format: {registration} - {e}")
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
        logger.info(f"TC Lookup - Not found: {display_reg}")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": "Aircraft not found in Transport Canada registry",
                "registration": display_reg,
                "suggestion": "Verify the registration or check Transport Canada directly"
            }
        )
    
    logger.info(f"TC Lookup - Found: {aircraft.get('registration')} ({aircraft.get('manufacturer')} {aircraft.get('model')})")
    
    # Format date for response
    date_manufacture = None
    if aircraft.get("date_manufacture"):
        date_manufacture = aircraft["date_manufacture"].strftime("%Y-%m-%d")
    
    # Return response
    return TCAircraftDetailResponse(
        registration=aircraft.get("registration"),
        manufacturer=aircraft.get("manufacturer"),
        model=aircraft.get("model"),
        designator=aircraft.get("designator"),
        serial_number=aircraft.get("serial_number"),
        owner_name=aircraft.get("owner_name"),
        owner_city=aircraft.get("owner_city"),
        owner_province=aircraft.get("owner_province"),
        aircraft_category=aircraft.get("aircraft_category"),
        num_engines=aircraft.get("num_engines"),
        num_seats=aircraft.get("num_seats"),
        status=aircraft.get("status"),
        engine_manufacturer=aircraft.get("engine_manufacturer"),
        engine_category=aircraft.get("engine_category"),
        weight_kg=aircraft.get("weight_kg"),
        country_manufacture=aircraft.get("country_manufacture"),
        date_manufacture=date_manufacture,
        base_province=aircraft.get("base_province"),
        city_airport=aircraft.get("city_airport"),
        purpose=aircraft.get("purpose"),
    )


@router.get(
    "/search",
    response_model=List[TCAircraftResponse],
    summary="Search aircraft by partial registration",
    description="""
    Search aircraft by registration prefix.
    
    **Example:** `/api/tc/search?prefix=C-FG` returns all aircraft starting with C-FG.
    
    **Input formats:**
    - C-FG, CFG, FG (all equivalent)
    - Case insensitive
    
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
    """
    # Normalize prefix for search
    prefix_norm = prefix.strip().upper().replace("-", "")
    
    # Add C if not present
    if not prefix_norm.startswith("C"):
        prefix_norm = "C" + prefix_norm
    
    # Search using registration_norm with regex prefix match
    pattern = f"^{re.escape(prefix_norm)}"
    
    cursor = db.tc_aircraft.find(
        {"registration_norm": {"$regex": pattern}},
        {
            "_id": 0,
            "registration": 1,
            "registration_norm": 1,
            "manufacturer": 1,
            "model": 1,
            "designator": 1,
            "serial_number": 1,
            "owner_name": 1,
            "owner_city": 1,
            "owner_province": 1,
            "aircraft_category": 1,
            "num_engines": 1,
            "num_seats": 1,
            "status": 1,
        }
    ).sort("registration_norm", 1).limit(limit)
    
    results = []
    async for doc in cursor:
        results.append(TCAircraftResponse(
            registration=doc.get("registration"),
            manufacturer=doc.get("manufacturer"),
            model=doc.get("model"),
            designator=doc.get("designator"),
            serial_number=doc.get("serial_number"),
            owner_name=doc.get("owner_name"),
            owner_city=doc.get("owner_city"),
            owner_province=doc.get("owner_province"),
            aircraft_category=doc.get("aircraft_category"),
            num_engines=doc.get("num_engines"),
            num_seats=doc.get("num_seats"),
            status=doc.get("status"),
        ))
    
    logger.info(f"TC Search - Prefix: {prefix_norm} - Found: {len(results)}")
    
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
    
    # Top manufacturers
    pipeline = [
        {"$group": {"_id": "$manufacturer", "count": {"$sum": 1}}},
        {"$sort": {"count": -1}},
        {"$limit": 10}
    ]
    top_manufacturers = []
    async for doc in db.tc_aircraft.aggregate(pipeline):
        top_manufacturers.append({"manufacturer": doc["_id"], "count": doc["count"]})
    
    # Aircraft categories
    cat_pipeline = [
        {"$group": {"_id": "$aircraft_category", "count": {"$sum": 1}}},
        {"$sort": {"count": -1}}
    ]
    categories = []
    async for doc in db.tc_aircraft.aggregate(cat_pipeline):
        categories.append({"category": doc["_id"], "count": doc["count"]})
    
    return {
        "total_aircraft": total,
        "import_date": import_date,
        "top_manufacturers": top_manufacturers,
        "aircraft_categories": categories
    }
