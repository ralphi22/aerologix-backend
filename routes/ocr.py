"""
OCR Routes for AeroLogix AI
Handles document scanning and data extraction
"""

from fastapi import APIRouter, Depends, HTTPException, status, Body
from typing import List, Optional, Dict, Any
from datetime import datetime
from bson import ObjectId
from database.mongodb import get_database
from services.auth_deps import get_current_user
from services.ocr_service import ocr_service
from models.ocr_scan import (
    OCRScanCreate, OCRScan, OCRScanResponse, 
    OCRStatus, DocumentType, ExtractedMaintenanceData,
    DuplicateCheckResponse, DuplicateMatch, MatchType,
    ApplySelections, ItemAction, ItemSelection
)
from models.user import User, PlanTier, OCR_LIMITS_BY_PLAN
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ocr", tags=["ocr"])


# ============== DEDUPLICATION HELPERS ==============

async def find_duplicate_adsb(db, aircraft_id: str, user_id: str, reference_number: str) -> Optional[Dict]:
    """Find existing AD/SB by aircraft_id + reference_number"""
    return await db.adsb_records.find_one({
        "aircraft_id": aircraft_id,
        "user_id": user_id,
        "reference_number": reference_number
    })


async def find_duplicate_part(db, aircraft_id: str, user_id: str, part_number: str, serial_number: Optional[str] = None) -> tuple:
    """
    Find existing part by aircraft_id + part_number (+ serial_number if provided)
    Returns (existing_doc, match_type)
    """
    # First try exact match with serial number
    if serial_number:
        exact = await db.part_records.find_one({
            "aircraft_id": aircraft_id,
            "user_id": user_id,
            "part_number": part_number,
            "serial_number": serial_number
        })
        if exact:
            return exact, MatchType.EXACT
    
    # Try partial match (part_number only)
    partial = await db.part_records.find_one({
        "aircraft_id": aircraft_id,
        "user_id": user_id,
        "part_number": part_number
    })
    if partial:
        return partial, MatchType.PARTIAL
    
    return None, MatchType.NONE


async def find_duplicate_invoice(db, aircraft_id: str, user_id: str, invoice_data: Dict) -> tuple:
    """
    Find existing invoice by multiple criteria
    Returns (existing_doc, match_type)
    """
    invoice_number = invoice_data.get("invoice_number")
    supplier = invoice_data.get("supplier")
    total = invoice_data.get("total")
    invoice_date = invoice_data.get("invoice_date")
    
    # Try exact match: invoice_number + supplier
    if invoice_number and supplier:
        exact = await db.invoices.find_one({
            "aircraft_id": aircraft_id,
            "user_id": user_id,
            "invoice_number": invoice_number,
            "supplier": supplier
        })
        if exact:
            return exact, MatchType.EXACT
    
    # Try partial: invoice_number only
    if invoice_number:
        partial = await db.invoices.find_one({
            "aircraft_id": aircraft_id,
            "user_id": user_id,
            "invoice_number": invoice_number
        })
        if partial:
            return partial, MatchType.PARTIAL
    
    # Try partial: same date + total
    if total and invoice_date:
        try:
            date_obj = datetime.fromisoformat(invoice_date) if isinstance(invoice_date, str) else invoice_date
            partial = await db.invoices.find_one({
                "aircraft_id": aircraft_id,
                "user_id": user_id,
                "total": total,
                "invoice_date": date_obj
            })
            if partial:
                return partial, MatchType.PARTIAL
        except:
            pass
    
    return None, MatchType.NONE


async def find_duplicate_stc(db, aircraft_id: str, user_id: str, stc_number: str) -> Optional[Dict]:
    """Find existing STC by aircraft_id + stc_number"""
    return await db.stc_records.find_one({
        "aircraft_id": aircraft_id,
        "user_id": user_id,
        "stc_number": stc_number
    })


def serialize_mongo_doc(doc: Dict) -> Dict:
    """Convert MongoDB document to JSON-serializable dict"""
    if doc is None:
        return None
    result = {}
    for key, value in doc.items():
        if isinstance(value, ObjectId):
            result[key] = str(value)
        elif isinstance(value, datetime):
            result[key] = value.isoformat()
        elif isinstance(value, dict):
            result[key] = serialize_mongo_doc(value)
        elif isinstance(value, list):
            result[key] = [serialize_mongo_doc(v) if isinstance(v, dict) else v for v in value]
        else:
            result[key] = value
    return result


def get_ocr_limit_for_plan(plan: str) -> int:
    """Get OCR limit based on user's subscription plan"""
    try:
        plan_tier = PlanTier(plan.upper()) if plan else PlanTier.BASIC
    except ValueError:
        plan_tier = PlanTier.BASIC
    
    return OCR_LIMITS_BY_PLAN.get(plan_tier, 5)


async def check_and_reset_ocr_usage(db, user_id: str, user_doc: dict) -> dict:
    """
    Check if OCR usage needs to be reset (new month).
    Returns updated user_doc.
    """
    now = datetime.utcnow()
    ocr_usage = user_doc.get("ocr_usage", {})
    reset_date = ocr_usage.get("reset_date")
    
    needs_reset = False
    
    if reset_date is None:
        # First time - initialize
        needs_reset = True
    elif isinstance(reset_date, datetime):
        # Check if we're in a new month
        if reset_date.year != now.year or reset_date.month != now.month:
            needs_reset = True
    
    if needs_reset:
        # Reset usage for new month
        await db.users.update_one(
            {"_id": user_id},
            {
                "$set": {
                    "ocr_usage.scans_used": 0,
                    "ocr_usage.reset_date": now
                }
            }
        )
        logger.info(f"Reset OCR usage for user {user_id} (new month)")
        
        # Return updated values
        user_doc["ocr_usage"] = {"scans_used": 0, "reset_date": now}
    
    return user_doc


async def increment_ocr_usage(db, user_id: str):
    """Increment OCR usage counter after successful scan"""
    await db.users.update_one(
        {"_id": user_id},
        {"$inc": {"ocr_usage.scans_used": 1}}
    )
    logger.info(f"Incremented OCR usage for user {user_id}")


# ============== CHECK DUPLICATES ENDPOINT ==============

@router.get("/check-duplicates/{scan_id}", response_model=DuplicateCheckResponse)
async def check_duplicates(
    scan_id: str,
    current_user: User = Depends(get_current_user),
    db=Depends(get_database)
):
    """
    Check for potential duplicates before applying OCR results.
    Returns existing matches for AD/SB, parts, invoices, and STCs.
    
    Call this BEFORE /apply/{scan_id} to let user decide what to do.
    """
    
    # Get OCR scan
    scan = await db.ocr_scans.find_one({
        "_id": scan_id,
        "user_id": current_user.id
    })
    
    if not scan:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="OCR scan not found"
        )
    
    if scan.get("status") != OCRStatus.COMPLETED.value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Scan must be completed before checking duplicates"
        )
    
    extracted_data = scan.get("extracted_data", {})
    aircraft_id = scan["aircraft_id"]
    
    duplicates = {
        "ad_sb": [],
        "parts": [],
        "invoices": [],
        "stc": []
    }
    new_items = {
        "ad_sb": [],
        "parts": [],
        "invoices": [],
        "stc": []
    }
    
    # Check AD/SB duplicates
    for idx, adsb in enumerate(extracted_data.get("ad_sb_references", [])):
        ref_num = adsb.get("reference_number")
        if not ref_num:
            continue
        
        existing = await find_duplicate_adsb(db, aircraft_id, current_user.id, ref_num)
        
        if existing:
            duplicates["ad_sb"].append(DuplicateMatch(
                index=idx,
                extracted=adsb,
                existing=serialize_mongo_doc(existing),
                existing_id=str(existing["_id"]),
                match_type=MatchType.EXACT
            ))
        else:
            new_items["ad_sb"].append({"index": idx, **adsb})
    
    # Check Parts duplicates
    for idx, part in enumerate(extracted_data.get("parts_replaced", [])):
        part_num = part.get("part_number")
        if not part_num:
            continue
        
        existing, match_type = await find_duplicate_part(
            db, aircraft_id, current_user.id, 
            part_num, part.get("serial_number")
        )
        
        if existing:
            duplicates["parts"].append(DuplicateMatch(
                index=idx,
                extracted=part,
                existing=serialize_mongo_doc(existing),
                existing_id=str(existing["_id"]),
                match_type=match_type
            ))
        else:
            new_items["parts"].append({"index": idx, **part})
    
    # Check Invoice duplicates (for invoice document type)
    document_type = scan.get("document_type", "")
    if document_type == "invoice":
        invoice_data = {
            "invoice_number": extracted_data.get("invoice_number"),
            "supplier": extracted_data.get("supplier"),
            "total": extracted_data.get("total"),
            "invoice_date": extracted_data.get("invoice_date")
        }
        
        existing, match_type = await find_duplicate_invoice(
            db, aircraft_id, current_user.id, invoice_data
        )
        
        if existing:
            duplicates["invoices"].append(DuplicateMatch(
                index=0,
                extracted=invoice_data,
                existing=serialize_mongo_doc(existing),
                existing_id=str(existing["_id"]),
                match_type=match_type
            ))
        else:
            new_items["invoices"].append({"index": 0, **invoice_data})
    
    # Check STC duplicates
    for idx, stc in enumerate(extracted_data.get("stc_references", [])):
        stc_num = stc.get("stc_number")
        if not stc_num:
            continue
        
        existing = await find_duplicate_stc(db, aircraft_id, current_user.id, stc_num)
        
        if existing:
            duplicates["stc"].append(DuplicateMatch(
                index=idx,
                extracted=stc,
                existing=serialize_mongo_doc(existing),
                existing_id=str(existing["_id"]),
                match_type=MatchType.EXACT
            ))
        else:
            new_items["stc"].append({"index": idx, **stc})
    
    # Build summary
    summary = {
        "ad_sb": {"duplicates": len(duplicates["ad_sb"]), "new": len(new_items["ad_sb"])},
        "parts": {"duplicates": len(duplicates["parts"]), "new": len(new_items["parts"])},
        "invoices": {"duplicates": len(duplicates["invoices"]), "new": len(new_items["invoices"])},
        "stc": {"duplicates": len(duplicates["stc"]), "new": len(new_items["stc"])}
    }
    
    logger.info(f"Duplicate check for scan {scan_id}: {summary}")
    
    return DuplicateCheckResponse(
        scan_id=scan_id,
        duplicates=duplicates,
        new_items=new_items,
        summary=summary
    )


@router.post("/scan", response_model=OCRScanResponse)
async def scan_document(
    scan_request: OCRScanCreate,
    current_user: User = Depends(get_current_user),
    db=Depends(get_database)
):
    """
    Scan a document image and extract structured data using AI Vision
    
    - **aircraft_id**: ID of the aircraft this document belongs to
    - **document_type**: Type of document (maintenance_report, stc, invoice)
    - **image_base64**: Base64 encoded image
    """
    
    # Verify aircraft belongs to user
    aircraft = await db.aircrafts.find_one({
        "_id": scan_request.aircraft_id,
        "user_id": current_user.id
    })
    
    if not aircraft:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Aircraft not found"
        )
    
    # Get user document for quota check
    user_doc = await db.users.find_one({"_id": current_user.id})
    if not user_doc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )
    
    # Check and reset OCR usage if new month
    user_doc = await check_and_reset_ocr_usage(db, current_user.id, user_doc)
    
    # Get OCR limit based on subscription plan
    user_plan = user_doc.get("subscription", {}).get("plan", "BASIC")
    ocr_limit = get_ocr_limit_for_plan(user_plan)
    
    # Get current usage
    ocr_usage = user_doc.get("ocr_usage", {})
    scans_used = ocr_usage.get("scans_used", 0)
    
    # CHECK LIMIT BEFORE calling OpenAI Vision
    if scans_used >= ocr_limit:
        logger.warning(f"OCR limit reached for user {current_user.id}: {scans_used}/{ocr_limit}")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Limite de scans atteinte pour votre forfait"
        )
    
    # Create OCR scan record
    now = datetime.utcnow()
    scan_doc = {
        "user_id": current_user.id,
        "aircraft_id": scan_request.aircraft_id,
        "document_type": scan_request.document_type.value,
        "status": OCRStatus.PROCESSING.value,
        "raw_text": None,
        "extracted_data": None,
        "error_message": None,
        "applied_maintenance_id": None,
        "applied_adsb_ids": [],
        "applied_part_ids": [],
        "applied_stc_ids": [],
        "created_at": now,
        "updated_at": now
    }
    
    # Generate string ID before insertion
    scan_id = str(datetime.utcnow().timestamp()).replace(".", "")
    scan_doc["_id"] = scan_id
    
    await db.ocr_scans.insert_one(scan_doc)
    
    try:
        # Analyze image with OCR service (OpenAI Vision)
        logger.info(f"Processing OCR scan {scan_id} for user {current_user.id} ({scans_used + 1}/{ocr_limit})")
        
        ocr_result = await ocr_service.analyze_image(
            image_base64=scan_request.image_base64,
            document_type=scan_request.document_type.value
        )
        
        if ocr_result["success"]:
            # Increment OCR usage counter AFTER successful scan
            await increment_ocr_usage(db, current_user.id)
            
            # Update scan with results
            await db.ocr_scans.update_one(
                {"_id": scan_id},
                {
                    "$set": {
                        "status": OCRStatus.COMPLETED.value,
                        "raw_text": ocr_result["raw_text"],
                        "extracted_data": ocr_result["extracted_data"],
                        "updated_at": datetime.utcnow()
                    }
                }
            )
            
            logger.info(f"OCR scan {scan_id} completed successfully")
            
            return OCRScanResponse(
                id=scan_id,
                status=OCRStatus.COMPLETED,
                document_type=scan_request.document_type,
                raw_text=ocr_result["raw_text"],
                extracted_data=ExtractedMaintenanceData(**ocr_result["extracted_data"]) if ocr_result["extracted_data"] else None,
                error_message=None,
                created_at=now
            )
        else:
            # Update scan with error (don't increment counter for failed scans)
            await db.ocr_scans.update_one(
                {"_id": scan_id},
                {
                    "$set": {
                        "status": OCRStatus.FAILED.value,
                        "error_message": ocr_result.get("error", "Unknown error"),
                        "updated_at": datetime.utcnow()
                    }
                }
            )
            
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"OCR analysis failed: {ocr_result.get('error')}"
            )
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"OCR scan {scan_id} failed: {str(e)}")
        
        await db.ocr_scans.update_one(
            {"_id": scan_id},
            {
                "$set": {
                    "status": OCRStatus.FAILED.value,
                    "error_message": str(e),
                    "updated_at": datetime.utcnow()
                }
            }
        )
        
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"OCR processing failed: {str(e)}"
        )


@router.get("/history/{aircraft_id}", response_model=List[OCRScanResponse])
async def get_ocr_history(
    aircraft_id: str,
    limit: int = 20,
    current_user: User = Depends(get_current_user),
    db=Depends(get_database)
):
    """
    Get OCR scan history for an aircraft
    """
    
    # Verify aircraft belongs to user
    aircraft = await db.aircrafts.find_one({
        "_id": aircraft_id,
        "user_id": current_user.id
    })
    
    if not aircraft:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Aircraft not found"
        )
    
    # Get OCR scans
    cursor = db.ocr_scans.find({
        "aircraft_id": aircraft_id,
        "user_id": current_user.id
    }).sort("created_at", -1).limit(limit)
    
    scans = []
    async for scan in cursor:
        extracted_data = None
        if scan.get("extracted_data"):
            try:
                extracted_data = ExtractedMaintenanceData(**scan["extracted_data"])
            except:
                pass
        
        scans.append(OCRScanResponse(
            id=str(scan["_id"]),
            status=OCRStatus(scan["status"]),
            document_type=DocumentType(scan["document_type"]),
            raw_text=scan.get("raw_text"),
            extracted_data=extracted_data,
            error_message=scan.get("error_message"),
            created_at=scan["created_at"]
        ))
    
    return scans


@router.get("/{scan_id}", response_model=OCRScanResponse)
async def get_ocr_scan(
    scan_id: str,
    current_user: User = Depends(get_current_user),
    db=Depends(get_database)
):
    """
    Get a specific OCR scan by ID
    """
    
    scan = await db.ocr_scans.find_one({
        "_id": scan_id,
        "user_id": current_user.id
    })
    
    if not scan:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="OCR scan not found"
        )
    
    extracted_data = None
    if scan.get("extracted_data"):
        try:
            extracted_data = ExtractedMaintenanceData(**scan["extracted_data"])
        except:
            pass
    
    return OCRScanResponse(
        id=str(scan["_id"]),
        status=OCRStatus(scan["status"]),
        document_type=DocumentType(scan["document_type"]),
        raw_text=scan.get("raw_text"),
        extracted_data=extracted_data,
        error_message=scan.get("error_message"),
        created_at=scan["created_at"]
    )


@router.post("/apply/{scan_id}")
async def apply_ocr_results(
    scan_id: str,
    update_aircraft_hours: bool = True,
    selections: Optional[ApplySelections] = Body(default=None),
    current_user: User = Depends(get_current_user),
    db=Depends(get_database)
):
    """
    Apply OCR extracted data to the system with deduplication support.
    
    - If `selections` is provided, use user choices for duplicates
    - If `selections` is None, auto-create all new items (backward compatible)
    
    Selection actions:
    - CREATE: Create new record
    - LINK: Update existing record with OCR data
    - SKIP: Do nothing for this item
    """
    
    # Get OCR scan
    scan = await db.ocr_scans.find_one({
        "_id": scan_id,
        "user_id": current_user.id
    })
    
    if not scan:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="OCR scan not found"
        )
    
    # Check if scan can be applied
    scan_status = scan.get("status", "")
    if scan_status == OCRStatus.APPLIED.value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Ce scan a déjà été appliqué"
        )
    
    if scan_status != OCRStatus.COMPLETED.value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Seuls les scans complétés peuvent être appliqués"
        )
    
    extracted_data = scan.get("extracted_data", {})
    aircraft_id = scan["aircraft_id"]
    now = datetime.utcnow()
    
    applied_ids = {
        "maintenance_id": None,
        "adsb_ids": [],
        "part_ids": [],
        "stc_ids": []
    }
    
    # Determine document type - critical for business rules
    document_type = scan.get("document_type", "maintenance_report")
    is_maintenance_report = document_type == "maintenance_report"
    is_invoice = document_type == "invoice"
    
    try:
        # ===== RÈGLE MÉTIER: Seul "Rapport" peut créer maintenance/pièces =====
        
        # 1. Update aircraft hours if provided (ONLY FOR RAPPORT)
        if is_maintenance_report and update_aircraft_hours:
            hours_update = {}
            if extracted_data.get("airframe_hours"):
                hours_update["airframe_hours"] = extracted_data["airframe_hours"]
            if extracted_data.get("engine_hours"):
                hours_update["engine_hours"] = extracted_data["engine_hours"]
            if extracted_data.get("propeller_hours"):
                hours_update["propeller_hours"] = extracted_data["propeller_hours"]
            
            if hours_update:
                hours_update["updated_at"] = now
                await db.aircrafts.update_one(
                    {"_id": aircraft_id},
                    {"$set": hours_update}
                )
                logger.info(f"Updated aircraft {aircraft_id} hours: {hours_update}")
        
        # 2. Create maintenance record (ONLY FOR RAPPORT)
        if is_maintenance_report and (extracted_data.get("description") or extracted_data.get("work_order_number")):
            maintenance_date = now
            if extracted_data.get("date"):
                try:
                    maintenance_date = datetime.fromisoformat(extracted_data["date"])
                except:
                    pass
            
            maintenance_doc = {
                "user_id": current_user.id,
                "aircraft_id": aircraft_id,
                "maintenance_type": "ROUTINE",
                "date": maintenance_date,
                "description": extracted_data.get("description", "OCR Extracted Maintenance"),
                "ame_name": extracted_data.get("ame_name"),
                "amo_name": extracted_data.get("amo_name"),
                "ame_license": extracted_data.get("ame_license"),
                "work_order_number": extracted_data.get("work_order_number"),
                "airframe_hours": extracted_data.get("airframe_hours"),
                "engine_hours": extracted_data.get("engine_hours"),
                "propeller_hours": extracted_data.get("propeller_hours"),
                "remarks": extracted_data.get("remarks"),
                "labor_cost": extracted_data.get("labor_cost"),
                "parts_cost": extracted_data.get("parts_cost"),
                "total_cost": extracted_data.get("total_cost"),
                "parts_replaced": [p.get("part_number", "") for p in extracted_data.get("parts_replaced", [])],
                "regulatory_references": [r.get("reference_number", "") for r in extracted_data.get("ad_sb_references", [])],
                "source": "ocr",
                "ocr_scan_id": scan_id,
                "created_at": now,
                "updated_at": now
            }
            
            result = await db.maintenance_records.insert_one(maintenance_doc)
            applied_ids["maintenance_id"] = str(result.inserted_id)
            logger.info(f"Created maintenance record {applied_ids['maintenance_id']}")
        
        # 3. Create/Link AD/SB records (ONLY FOR RAPPORT) with deduplication
        if is_maintenance_report:
            ad_sb_selections = {s.index: s for s in (selections.ad_sb or [])} if selections else {}
            
            for idx, adsb in enumerate(extracted_data.get("ad_sb_references", [])):
                if not adsb.get("reference_number"):
                    continue
                
                # Check user selection if provided
                selection = ad_sb_selections.get(idx)
                
                if selection:
                    if selection.action == ItemAction.SKIP:
                        logger.info(f"Skipping AD/SB {adsb.get('reference_number')} (user choice)")
                        continue
                    elif selection.action == ItemAction.LINK and selection.existing_id:
                        # Update existing record
                        update_data = {
                            "status": adsb.get("status", "UNKNOWN"),
                            "source": "ocr",
                            "ocr_scan_id": scan_id,
                            "updated_at": now
                        }
                        if adsb.get("compliance_date"):
                            try:
                                update_data["compliance_date"] = datetime.fromisoformat(adsb["compliance_date"])
                            except:
                                pass
                        if adsb.get("airframe_hours"):
                            update_data["compliance_airframe_hours"] = adsb["airframe_hours"]
                        
                        await db.adsb_records.update_one(
                            {"_id": selection.existing_id, "user_id": current_user.id},
                            {"$set": update_data}
                        )
                        applied_ids["adsb_ids"].append(selection.existing_id)
                        logger.info(f"Linked AD/SB {adsb.get('reference_number')} to existing {selection.existing_id}")
                        continue
                
                # Default: CREATE new record (or no selection provided)
                compliance_date = None
                if adsb.get("compliance_date"):
                    try:
                        compliance_date = datetime.fromisoformat(adsb["compliance_date"])
                    except:
                        pass
                
                # Auto-dedupe: check if exists when no selections provided
                if not selections:
                    existing = await find_duplicate_adsb(db, aircraft_id, current_user.id, adsb["reference_number"])
                    if existing:
                        # Update existing instead of creating duplicate
                        update_data = {
                            "status": adsb.get("status", "UNKNOWN"),
                            "source": "ocr",
                            "ocr_scan_id": scan_id,
                            "updated_at": now
                        }
                        if compliance_date:
                            update_data["compliance_date"] = compliance_date
                        
                        await db.adsb_records.update_one(
                            {"_id": existing["_id"]},
                            {"$set": update_data}
                        )
                        applied_ids["adsb_ids"].append(str(existing["_id"]))
                        logger.info(f"Auto-linked AD/SB {adsb['reference_number']} to existing (dedup)")
                        continue
                
                adsb_doc = {
                    "user_id": current_user.id,
                    "aircraft_id": aircraft_id,
                    "adsb_type": adsb.get("adsb_type", "AD"),
                    "reference_number": adsb["reference_number"],
                    "title": adsb.get("description"),
                    "description": adsb.get("description"),
                    "status": adsb.get("status", "UNKNOWN"),
                    "compliance_date": compliance_date,
                    "compliance_airframe_hours": adsb.get("airframe_hours"),
                    "compliance_engine_hours": adsb.get("engine_hours"),
                    "compliance_propeller_hours": adsb.get("propeller_hours"),
                    "source": "ocr",
                    "ocr_scan_id": scan_id,
                    "created_at": now,
                    "updated_at": now
                }
                
                result = await db.adsb_records.insert_one(adsb_doc)
                applied_ids["adsb_ids"].append(str(result.inserted_id))
        
        logger.info(f"Created {len(applied_ids['adsb_ids'])} AD/SB records")
        
        # 4. Create/Link part records (ONLY FOR RAPPORT) with deduplication
        if is_maintenance_report:
            parts_selections = {s.index: s for s in (selections.parts or [])} if selections else {}
            
            for idx, part in enumerate(extracted_data.get("parts_replaced", [])):
                if not part.get("part_number"):
                    continue
                
                # Check user selection if provided
                selection = parts_selections.get(idx)
                
                if selection:
                    if selection.action == ItemAction.SKIP:
                        logger.info(f"Skipping part {part.get('part_number')} (user choice)")
                        continue
                    elif selection.action == ItemAction.LINK and selection.existing_id:
                        # Update existing record
                        update_data = {
                            "source": "ocr",
                            "ocr_scan_id": scan_id,
                            "updated_at": now
                        }
                        if part.get("serial_number"):
                            update_data["serial_number"] = part["serial_number"]
                        if part.get("price"):
                            update_data["purchase_price"] = part["price"]
                        
                        await db.part_records.update_one(
                            {"_id": selection.existing_id, "user_id": current_user.id},
                            {"$set": update_data}
                        )
                        applied_ids["part_ids"].append(selection.existing_id)
                        logger.info(f"Linked part {part.get('part_number')} to existing {selection.existing_id}")
                        continue
                
                # Auto-dedupe: check if exists when no selections provided
                if not selections:
                    existing, match_type = await find_duplicate_part(
                        db, aircraft_id, current_user.id,
                        part["part_number"], part.get("serial_number")
                    )
                    if existing and match_type == MatchType.EXACT:
                        # Update existing instead of creating duplicate
                        update_data = {
                            "source": "ocr",
                            "ocr_scan_id": scan_id,
                            "updated_at": now
                        }
                        if part.get("price"):
                            update_data["purchase_price"] = part["price"]
                        
                        await db.part_records.update_one(
                            {"_id": existing["_id"]},
                            {"$set": update_data}
                        )
                        applied_ids["part_ids"].append(str(existing["_id"]))
                        logger.info(f"Auto-linked part {part['part_number']} to existing (exact dedup)")
                        continue
                
                # Default: CREATE new record
                part_doc = {
                    "user_id": current_user.id,
                    "aircraft_id": aircraft_id,
                    "part_number": part["part_number"],
                    "name": part.get("name", part["part_number"]),
                    "serial_number": part.get("serial_number"),
                    "quantity": part.get("quantity", 1),
                    "purchase_price": part.get("price"),
                    "supplier": part.get("supplier"),
                    "installation_date": now,
                    "installation_airframe_hours": extracted_data.get("airframe_hours"),
                    "installed_on_aircraft": True,
                    "source": "ocr",
                    "ocr_scan_id": scan_id,
                    "confirmed": False,  # OCR parts are NOT confirmed by default
                    "created_at": now,
                    "updated_at": now
                }
                
                result = await db.part_records.insert_one(part_doc)
                applied_ids["part_ids"].append(str(result.inserted_id))
        
        logger.info(f"Created {len(applied_ids['part_ids'])} part records")
        
        # 5. Create/Link STC records (ONLY FOR RAPPORT) with deduplication
        if is_maintenance_report:
            stc_selections = {s.index: s for s in (selections.stc or [])} if selections else {}
            
            for idx, stc in enumerate(extracted_data.get("stc_references", [])):
                if not stc.get("stc_number"):
                    continue
                
                # Check user selection if provided
                selection = stc_selections.get(idx)
                
                if selection:
                    if selection.action == ItemAction.SKIP:
                        logger.info(f"Skipping STC {stc.get('stc_number')} (user choice)")
                        continue
                    elif selection.action == ItemAction.LINK and selection.existing_id:
                        # Update existing record
                        update_data = {
                            "source": "ocr",
                            "ocr_scan_id": scan_id,
                            "updated_at": now
                        }
                        if stc.get("title"):
                            update_data["title"] = stc["title"]
                        if stc.get("description"):
                            update_data["description"] = stc["description"]
                        
                        await db.stc_records.update_one(
                            {"_id": selection.existing_id, "user_id": current_user.id},
                            {"$set": update_data}
                        )
                        applied_ids["stc_ids"].append(selection.existing_id)
                        logger.info(f"Linked STC {stc.get('stc_number')} to existing {selection.existing_id}")
                        continue
                
                # Auto-dedupe: check if exists when no selections provided
                if not selections:
                    existing = await find_duplicate_stc(db, aircraft_id, current_user.id, stc["stc_number"])
                    if existing:
                        # Update existing instead of creating duplicate
                        update_data = {
                            "source": "ocr",
                            "ocr_scan_id": scan_id,
                            "updated_at": now
                        }
                        if stc.get("title"):
                            update_data["title"] = stc["title"]
                        
                        await db.stc_records.update_one(
                            {"_id": existing["_id"]},
                            {"$set": update_data}
                        )
                        applied_ids["stc_ids"].append(str(existing["_id"]))
                        logger.info(f"Auto-linked STC {stc['stc_number']} to existing (dedup)")
                        continue
                
                # Default: CREATE new record
                installation_date = None
                if stc.get("installation_date"):
                    try:
                        installation_date = datetime.fromisoformat(stc["installation_date"])
                    except:
                        pass
                
                stc_doc = {
                    "user_id": current_user.id,
                    "aircraft_id": aircraft_id,
                    "stc_number": stc["stc_number"],
                    "title": stc.get("title"),
                    "description": stc.get("description"),
                    "holder": stc.get("holder"),
                    "applicable_models": stc.get("applicable_models", []),
                    "installation_date": installation_date or now,
                    "installation_airframe_hours": stc.get("installation_airframe_hours") or extracted_data.get("airframe_hours"),
                    "installed_by": stc.get("installed_by") or extracted_data.get("ame_name"),
                    "source": "ocr",
                    "ocr_scan_id": scan_id,
                    "created_at": now,
                    "updated_at": now
                }
                
                result = await db.stc_records.insert_one(stc_doc)
                applied_ids["stc_ids"].append(str(result.inserted_id))
        
        logger.info(f"Created {len(applied_ids['stc_ids'])} STC records")
        
        # 6. Create/Update ELT record if detected (ONLY FOR RAPPORT)
        elt_data = extracted_data.get("elt_data", {})
        elt_created = False
        if is_maintenance_report and elt_data and elt_data.get("detected"):
            # Check if ELT exists
            existing_elt = await db.elt_records.find_one({
                "aircraft_id": aircraft_id,
                "user_id": current_user.id
            })
            
            elt_doc = {
                "brand": elt_data.get("brand"),
                "model": elt_data.get("model"),
                "serial_number": elt_data.get("serial_number"),
                "beacon_hex_id": elt_data.get("beacon_hex_id"),
                "source": "ocr",
                "ocr_scan_id": scan_id,
                "updated_at": now
            }
            
            # Parse dates
            for date_field in ["installation_date", "certification_date", "battery_expiry_date", "battery_install_date"]:
                if elt_data.get(date_field):
                    try:
                        elt_doc[date_field] = datetime.fromisoformat(elt_data[date_field])
                    except:
                        pass
            
            if elt_data.get("battery_interval_months"):
                elt_doc["battery_interval_months"] = elt_data["battery_interval_months"]
            
            if existing_elt:
                # Update existing
                await db.elt_records.update_one(
                    {"_id": existing_elt["_id"]},
                    {"$set": elt_doc}
                )
                applied_ids["elt_id"] = str(existing_elt["_id"])
                logger.info(f"Updated ELT record for aircraft {aircraft_id}")
            else:
                # Create new
                elt_doc["user_id"] = current_user.id
                elt_doc["aircraft_id"] = aircraft_id
                elt_doc["created_at"] = now
                result = await db.elt_records.insert_one(elt_doc)
                applied_ids["elt_id"] = str(result.inserted_id)
                logger.info(f"Created ELT record for aircraft {aircraft_id}")
            
            elt_created = True
        
        # 7. Create Invoice record if document type is invoice (WITH DEDUPLICATION)
        invoice_created = False
        if is_invoice:
            # Préparer les données de la facture
            invoice_total = extracted_data.get("total")
            invoice_date = None
            if extracted_data.get("invoice_date"):
                try:
                    invoice_date = datetime.fromisoformat(extracted_data["invoice_date"])
                except:
                    pass
            
            # DÉDUPLICATION: vérifier si une facture similaire existe déjà
            existing_invoice = None
            if invoice_total and invoice_date:
                # Chercher une facture avec même avion, même date, même montant
                existing_invoice = await db.invoices.find_one({
                    "aircraft_id": aircraft_id,
                    "user_id": current_user.id,
                    "total": invoice_total,
                    "invoice_date": invoice_date
                })
            
            if existing_invoice:
                # Facture similaire existe - mettre à jour au lieu de créer
                update_fields = {
                    "updated_at": now,
                    "invoice_number": extracted_data.get("invoice_number") or existing_invoice.get("invoice_number"),
                    "supplier": extracted_data.get("supplier") or existing_invoice.get("supplier"),
                }
                await db.invoices.update_one(
                    {"_id": existing_invoice["_id"]},
                    {"$set": update_fields}
                )
                applied_ids["invoice_id"] = str(existing_invoice["_id"])
                invoice_created = True
                logger.info(f"Updated existing invoice {existing_invoice['_id']} for aircraft {aircraft_id} (deduplication)")
            else:
                # Créer nouvelle facture (SANS créer de pièces!)
                invoice_doc = {
                    "user_id": current_user.id,
                    "aircraft_id": aircraft_id,
                    "invoice_number": extracted_data.get("invoice_number"),
                    "invoice_date": invoice_date,
                    "supplier": extracted_data.get("supplier"),
                    "subtotal": extracted_data.get("subtotal"),
                    "tax": extracted_data.get("tax"),
                    "total": invoice_total,
                    "currency": extracted_data.get("currency", "CAD"),
                    "source": "ocr",
                    "ocr_scan_id": scan_id,
                    # Stocker les pièces dans la facture (pour référence coûts), mais NE PAS créer de part_records
                    "parts": extracted_data.get("parts", []) + extracted_data.get("parts_replaced", []),
                    "created_at": now,
                    "updated_at": now
                }
                
                result = await db.invoices.insert_one(invoice_doc)
                applied_ids["invoice_id"] = str(result.inserted_id)
                invoice_created = True
                logger.info(f"Created invoice record for aircraft {aircraft_id}")
        
        # Update OCR scan status to APPLIED
        await db.ocr_scans.update_one(
            {"_id": scan_id},
            {
                "$set": {
                    "status": OCRStatus.APPLIED.value,
                    "applied_maintenance_id": applied_ids["maintenance_id"],
                    "applied_adsb_ids": applied_ids["adsb_ids"],
                    "applied_part_ids": applied_ids["part_ids"],
                    "applied_stc_ids": applied_ids["stc_ids"],
                    "applied_elt_id": applied_ids.get("elt_id"),
                    "updated_at": now
                }
            }
        )
        
        return {
            "message": "OCR results applied successfully",
            "applied": {
                "maintenance_record": applied_ids["maintenance_id"],
                "adsb_records": len(applied_ids["adsb_ids"]),
                "part_records": len(applied_ids["part_ids"]),
                "stc_records": len(applied_ids["stc_ids"]),
                "elt_updated": elt_created,
                "invoice_created": invoice_created
            }
        }
        
    except Exception as e:
        logger.error(f"Failed to apply OCR results: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to apply OCR results: {str(e)}"
        )


@router.get("/quota/status")
async def get_ocr_quota_status(
    current_user: User = Depends(get_current_user),
    db=Depends(get_database)
):
    """
    Get current OCR quota status for the user.
    Returns only the plan limit (not usage details for frontend).
    """
    
    user_doc = await db.users.find_one({"_id": current_user.id})
    if not user_doc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )
    
    # Check and reset if needed
    user_doc = await check_and_reset_ocr_usage(db, current_user.id, user_doc)
    
    # Get limit based on plan
    user_plan = user_doc.get("subscription", {}).get("plan", "BASIC")
    ocr_limit = get_ocr_limit_for_plan(user_plan)
    
    # Return only the limit (frontend doesn't need usage details)
    return {
        "limit": ocr_limit,
        "plan": user_plan
    }


@router.delete("/{scan_id}")
async def delete_ocr_scan(
    scan_id: str,
    current_user: User = Depends(get_current_user),
    db=Depends(get_database)
):
    """
    Delete an OCR scan from history
    """
    
    # Try to find by string ID first, then by ObjectId
    scan = await db.ocr_scans.find_one({
        "_id": scan_id,
        "user_id": current_user.id
    })
    
    if not scan:
        # Try with ObjectId
        try:
            scan = await db.ocr_scans.find_one({
                "_id": ObjectId(scan_id),
                "user_id": current_user.id
            })
        except:
            pass
    
    if not scan:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="OCR scan not found"
        )
    
    # Delete the scan
    if isinstance(scan["_id"], ObjectId):
        await db.ocr_scans.delete_one({"_id": scan["_id"]})
    else:
        await db.ocr_scans.delete_one({"_id": scan_id})
    
    logger.info(f"Deleted OCR scan {scan_id} for user {current_user.id}")
    
    return {"message": "OCR scan deleted successfully"}
