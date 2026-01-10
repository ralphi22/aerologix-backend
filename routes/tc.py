"""
Transport Canada Aircraft Registry Lookup API

Provides read-only access to the TC_Aeronefs collection.
Privacy-compliant: Only public registration data is exposed.

Endpoints:
- GET /api/tc/lookup?registration=C-FKZY
"""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from motor.motor_asyncio import AsyncIOMotorDatabase
from pydantic import BaseModel
from typing import Optional
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
    NO personal information (addresses, etc.)
    """
    registration: str
    manufacturer: Optional[str] = None
    model: Optional[str] = None
    designator: Optional[str] = None
    first_owner_given_name: Optional[str] = None
    first_owner_family_name: Optional[str] = None


class TCLookupError(BaseModel):
    """Error response for lookup failures"""
    error: str
    registration: str
    suggestion: Optional[str] = None


# ============================================================
# VALIDATION
# ============================================================

# Canadian registration pattern: C-XXXX (4 letters after C-)
REGISTRATION_PATTERN = re.compile(r'^C-?[A-Z]{3,4}$', re.IGNORECASE)


def normalize_registration(registration: str) -> str:
    """
    Normalize registration input.
    
    Accepts:
    - C-FKZY
    - CFKZY
    - c-fkzy
    - cfkzy
    
    Returns: C-FKZY (uppercase with hyphen)
    """
    if not registration:
        raise ValueError("Registration is required")
    
    # Clean input
    reg = registration.strip().upper()
    
    # Remove hyphen for normalization
    reg_clean = reg.replace("-", "")
    
    # Validate format
    if not reg_clean.startswith("C"):
        raise ValueError("Canadian registrations must start with 'C'")
    
    if len(reg_clean) < 4 or len(reg_clean) > 5:
        raise ValueError("Invalid registration length")
    
    # Check if it matches pattern (C + 3-4 letters)
    letters_part = reg_clean[1:]
    if not letters_part.isalpha():
        raise ValueError("Registration must contain only letters after 'C'")
    
    # Return normalized format: C-XXXX
    return f"C-{letters_part}"


def get_document_id(registration: str) -> str:
    """
    Get MongoDB document ID from registration.
    
    Document IDs are stored as CXXXX (no hyphen, uppercase)
    """
    return registration.replace("-", "").upper()


# ============================================================
# ENDPOINTS
# ============================================================

@router.get(
    "/lookup",
    response_model=TCAircraftResponse,
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
    
    **Returns:** Public registration data only (manufacturer, model, owner name).
    
    **Privacy:** No personal information (addresses, serial numbers) is returned.
    """
)
async def lookup_aircraft(
    registration: str = Query(
        ...,
        description="Aircraft registration (e.g., C-FKZY or CFKZY)",
        min_length=4,
        max_length=7,
        examples=["C-FKZY", "CFKZY", "C-GABC"]
    ),
    db: AsyncIOMotorDatabase = Depends(get_database)
):
    """
    Lookup an aircraft in the Transport Canada registry.
    
    Read-only endpoint. No authentication required.
    """
    # Validate and normalize registration
    try:
        normalized_reg = normalize_registration(registration)
    except ValueError as e:
        logger.warning(f"TC Lookup - Invalid format: {registration} - {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": str(e),
                "registration": registration,
                "suggestion": "Format: C-XXXX (e.g., C-FKZY)"
            }
        )
    
    # Get document ID
    doc_id = get_document_id(normalized_reg)
    
    # Lookup in TC_Aeronefs collection
    aircraft = await db.tc_aeronefs.find_one({"_id": doc_id})
    
    if not aircraft:
        # Try alternate lookup by registration field
        aircraft = await db.tc_aeronefs.find_one({"registration": normalized_reg})
    
    if not aircraft:
        logger.info(f"TC Lookup - Not found: {normalized_reg}")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": "Aircraft not found in Transport Canada registry",
                "registration": normalized_reg,
                "suggestion": "Verify the registration or check Transport Canada directly"
            }
        )
    
    logger.info(f"TC Lookup - Found: {normalized_reg} ({aircraft.get('manufacturer')} {aircraft.get('model')})")
    
    # Return only allowed fields (privacy compliant)
    return TCAircraftResponse(
        registration=aircraft.get("registration", normalized_reg),
        manufacturer=aircraft.get("manufacturer"),
        model=aircraft.get("model"),
        designator=aircraft.get("designator"),
        first_owner_given_name=aircraft.get("first_owner_given_name"),
        first_owner_family_name=aircraft.get("first_owner_family_name"),
    )


@router.get(
    "/search",
    response_model=list[TCAircraftResponse],
    summary="Search aircraft by partial registration",
    description="""
    Search aircraft by partial registration prefix.
    
    **Example:** `/api/tc/search?prefix=C-FK` returns all aircraft starting with C-FK.
    
    **Limit:** Maximum 20 results.
    """
)
async def search_aircraft(
    prefix: str = Query(
        ...,
        description="Registration prefix (e.g., C-FK)",
        min_length=2,
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
    # Normalize prefix
    prefix_clean = prefix.strip().upper()
    if not prefix_clean.startswith("C"):
        prefix_clean = f"C-{prefix_clean}"
    if "-" not in prefix_clean and len(prefix_clean) > 1:
        prefix_clean = f"C-{prefix_clean[1:]}"
    
    # Build regex pattern
    pattern = f"^{re.escape(prefix_clean)}"
    
    # Search
    cursor = db.tc_aeronefs.find(
        {"registration": {"$regex": pattern, "$options": "i"}},
        {"_id": 0, "registration": 1, "manufacturer": 1, "model": 1, 
         "designator": 1, "first_owner_given_name": 1, "first_owner_family_name": 1}
    ).limit(limit)
    
    results = []
    async for doc in cursor:
        results.append(TCAircraftResponse(**doc))
    
    logger.info(f"TC Search - Prefix: {prefix_clean} - Found: {len(results)}")
    
    return results


@router.get(
    "/stats",
    summary="Get TC registry statistics",
    description="Returns statistics about the TC_Aeronefs collection."
)
async def get_tc_stats(
    db: AsyncIOMotorDatabase = Depends(get_database)
):
    """
    Get statistics about the Transport Canada registry.
    """
    # Total count
    total = await db.tc_aeronefs.count_documents({})
    
    # Get current version
    sample = await db.tc_aeronefs.find_one({}, {"tc_version": 1})
    version = sample.get("tc_version") if sample else "unknown"
    
    # Top manufacturers
    pipeline = [
        {"$group": {"_id": "$manufacturer", "count": {"$sum": 1}}},
        {"$sort": {"count": -1}},
        {"$limit": 5}
    ]
    top_manufacturers = []
    async for doc in db.tc_aeronefs.aggregate(pipeline):
        top_manufacturers.append({"manufacturer": doc["_id"], "count": doc["count"]})
    
    return {
        "total_aircraft": total,
        "tc_version": version,
        "top_manufacturers": top_manufacturers
    }
