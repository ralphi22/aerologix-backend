"""
OCR Service for AeroLogix AI
Uses OpenAI Vision directly via Responses API
Note: OCR uses OpenAI directly (no Emergent proxy)
      EKO continues to use Emergent separately
"""

import os
import json
import logging
import re
from typing import Optional, Dict, Any
from datetime import datetime
from openai import OpenAI
from dotenv import load_dotenv

# Import report classifier
from services.report_classifier import classify_report_type

load_dotenv()

logger = logging.getLogger(__name__)

# Initialize OpenAI client directly for OCR (no Emergent proxy)
# Uses OPENAI_API_KEY from environment
client = OpenAI(
    api_key=os.getenv("OPENAI_API_KEY"),
    timeout=60.0  # 60 second timeout to prevent hanging
)

# Prompts spécialisés par type de document
MAINTENANCE_REPORT_PROMPT = """You are an aviation maintenance document analysis assistant.

The user provides an image of an aircraft maintenance report
(e.g. annual inspection, scheduled maintenance, AME/AMO report).

IMPORTANT RULES (MANDATORY):
- Do NOT invent or guess any value.
- If information is unclear, unreadable, or missing, return null.
- Do NOT infer compliance, airworthiness, or safety status.
- Do NOT calculate or extrapolate hours.
- Extract only what is EXPLICITLY written in the document.
- Dates must be returned as ISO format YYYY-MM-DD if readable.
- Numbers may use spaces or commas (e.g. "6 344,6"); return them as numbers.
- This data is informational and must be validated by the user.

OUTPUT FORMAT:
Return a SINGLE valid JSON object following EXACTLY this structure.

{
  "document_type": "maintenance_report",

  "report_date": string | null,
  "amo_name": string | null,
  "ame_name": string | null,
  "ame_license": string | null,
  "work_order_number": string | null,

  "airframe_hours": number | null,
  "engine_hours": number | null,
  "propeller_hours": number | null,

  "work_performed": string | null,

  "limitations_or_notes": [
    {
      "text": string,
      "confidence": number
    }
  ],

  "parts_replaced": [
    {
      "part_number": string | null,
      "description": string | null,
      "quantity": number | null,
      "unit_price": number | null,
      "confidence": number
    }
  ],

  "ad_sb_references": [
    {
      "reference_number": string | null,
      "description": string | null,
      "confidence": number
    }
  ],

  "stc_references": [
    {
      "stc_number": string | null,
      "description": string | null,
      "confidence": number
    }
  ],

  "elt_data": {
    "elt_type": string | null,
    "elt_frequency": string | null,
    "battery_expiry": string | null,
    "confidence": number
  }
}

CONFIDENCE:
- confidence must be a number between 0.0 and 1.0
- Use lower confidence if text is faint, partial, or inferred from context
- If a field is returned as null, omit confidence for that field

FINAL CHECK:
- Return ONLY valid JSON
- Do NOT include explanations or comments
"""

STC_PROMPT = """Tu es un expert en certification aéronautique. Analyse cette image d'un document STC (Supplemental Type Certificate) et extrait les informations structurées.

IMPORTANT: Réponds UNIQUEMENT avec un JSON valide, sans texte avant ou après.

Structure JSON attendue:
{
    "stc_number": "Numéro STC (ex: SA02345NY)",
    "title": "Titre du STC",
    "description": "Description détaillée de la modification",
    "holder": "Détenteur du STC (entreprise)",
    "applicable_models": ["Liste des modèles d'avions applicables"],
    "installation_date": "YYYY-MM-DD ou null",
    "installation_airframe_hours": nombre ou null,
    "installed_by": "AME/AMO qui a installé ou null",
    "work_order_reference": "Référence Work Order ou null",
    "remarks": "Remarques ou null"
}

Analyse l'image maintenant:"""

INVOICE_PROMPT = """You are an aviation maintenance invoice analysis assistant.

The user provides an image of an aircraft maintenance invoice.

IMPORTANT RULES (MANDATORY):
- Do NOT invent or guess any value.
- If information is unclear, unreadable, or missing, return null.
- Do NOT assume parts are installed on the aircraft.
- Do NOT infer labor rates or quantities.
- Extract only what is explicitly written on the invoice.
- Dates must be returned as ISO format YYYY-MM-DD if readable.
- Numbers may use spaces or commas (e.g. "6 083,17"); return them as numbers.
- This data is informational and must be validated by the user.

OUTPUT FORMAT:
Return a SINGLE valid JSON object following EXACTLY this structure.

{
  "document_type": "invoice",

  "invoice_number": string | null,
  "invoice_date": string | null,
  "vendor_name": string | null,

  "labor_hours": number | null,
  "labor_cost": number | null,

  "parts_cost": number | null,
  "total_cost": number | null,

  "parts_replaced": [
    {
      "part_number": string | null,
      "description": string | null,
      "quantity": number | null,
      "unit_price": number | null,
      "line_total": number | null,
      "confidence": number
    }
  ]
}

CONFIDENCE:
- confidence must be a number between 0.0 and 1.0
- Use lower confidence if text is faint, partial, or ambiguous
- If a field is returned as null, omit confidence for that field

FINAL CHECK:
- Return ONLY valid JSON
- Do NOT include explanations or comments
"""


class OCRService:
    """Service for processing aviation documents with OpenAI Vision"""
    
    def __init__(self):
        self.client = client
    
    def _get_prompt_for_document_type(self, document_type: str) -> str:
        """Get specialized prompt based on document type"""
        prompts = {
            "maintenance_report": MAINTENANCE_REPORT_PROMPT,
            "stc": STC_PROMPT,
            "invoice": INVOICE_PROMPT,
        }
        return prompts.get(document_type, MAINTENANCE_REPORT_PROMPT)
    
    def _clean_json_response(self, response: str) -> str:
        """Clean the response to extract valid JSON"""
        # Remove markdown code blocks if present
        response = re.sub(r'```json\s*', '', response)
        response = re.sub(r'```\s*', '', response)
        response = response.strip()
        
        # Find JSON object
        start = response.find('{')
        end = response.rfind('}')
        
        if start != -1 and end != -1:
            return response[start:end+1]
        
        return response
    
    def _normalize_ocr_keys(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Normalize OCR response keys from French to English.
        Ensures all keys match the expected backend schema.
        """
        # Mapping des clés françaises vers anglaises
        KEY_MAPPING = {
            # ========== INVOICE KEYS (FR -> EN) ==========
            # CRITICAL: "date" alone must map to "invoice_date" for invoices
            "date": "invoice_date",
            "numéro_de_facture": "invoice_number",
            "numero_de_facture": "invoice_number",
            "numéro_facture": "invoice_number",
            "numero_facture": "invoice_number",
            "date_facture": "invoice_date",
            "fournisseur": "supplier",
            "vendeur": "vendor_name",
            "coût_total": "total",
            "cout_total": "total",
            "total_facture": "total",
            "montant_total": "total",
            "devise": "currency",
            # CRITICAL: pièces_remplacées -> "parts" (not parts_replaced) for invoices
            "pièces_remplacées": "parts",
            "pieces_remplacees": "parts",
            "pièces": "parts",
            "pieces": "parts",
            "références_ad_sb": "ad_sb_references",
            "references_ad_sb": "ad_sb_references",
            "références_stc": "stc_references",
            "references_stc": "stc_references",
            # ========== MAINTENANCE REPORT KEYS (FR -> EN) ==========
            "date_rapport": "report_date",
            "heures_cellule": "airframe_hours",
            "heures_moteur": "engine_hours",
            "heures_hélice": "propeller_hours",
            "heures_helice": "propeller_hours",
            "travaux_effectués": "work_performed",
            "travaux_effectues": "work_performed",
            "description_travaux": "description",
            "numéro_bon_travail": "work_order_number",
            "numero_bon_travail": "work_order_number",
            "nom_ame": "ame_name",
            "licence_ame": "ame_license",
            "nom_amo": "amo_name",
            "coût_main_oeuvre": "labor_cost",
            "cout_main_oeuvre": "labor_cost",
            "heures_main_oeuvre": "labor_hours",
            "coût_pièces": "parts_cost",
            "cout_pieces": "parts_cost",
            "remarques": "remarks",
            "limitations": "limitations_or_notes",
            # ========== PART KEYS (FR -> EN) ==========
            "numéro_pièce": "part_number",
            "numero_piece": "part_number",
            "numéro_série": "serial_number",
            "numero_serie": "serial_number",
            "quantité": "quantity",
            "quantite": "quantity",
            "prix_unitaire": "unit_price",
            "prix": "price",
            "total_ligne": "line_total",
            "nom": "name",
            "description": "description",
            "fabricant": "manufacturer",
        }
        
        def normalize_dict(d: Dict[str, Any]) -> Dict[str, Any]:
            """Recursively normalize dictionary keys"""
            if not isinstance(d, dict):
                return d
            
            normalized = {}
            for key, value in d.items():
                # Normalize key using mapping (keep original if not in mapping)
                normalized_key = KEY_MAPPING.get(key, key)
                
                # Recursively normalize nested structures
                if isinstance(value, dict):
                    normalized[normalized_key] = normalize_dict(value)
                elif isinstance(value, list):
                    normalized[normalized_key] = [
                        normalize_dict(item) if isinstance(item, dict) else item
                        for item in value
                    ]
                else:
                    normalized[normalized_key] = value
            
            return normalized
        
        normalized_data = normalize_dict(data)
        
        # Log normalized keys for debugging
        logger.info(f"OCR NORMALIZED KEYS = {list(normalized_data.keys())}")
        
        return normalized_data
    
    def _normalize_parts(self, data: Dict[str, Any], document_type: str) -> Dict[str, Any]:
        """
        Normalize parts data - USE SINGLE SOURCE to prevent duplication.
        For invoices: prefer 'parts', fallback to 'parts_replaced'
        """
        # Search for parts in this priority order - USE FIRST FOUND ONLY
        normalized_parts = None
        source_key = None
        
        for key in ["parts", "pièces", "pieces", "parts_replaced", "pièces_remplacées", "pieces_remplacees"]:
            if key in data and data[key] and len(data[key]) > 0:
                normalized_parts = data[key]
                source_key = key
                break  # STOP at first found - don't concatenate
        
        # If no parts found, set empty list
        if normalized_parts is None:
            normalized_parts = []
        
        # If parts is a list of strings, convert to list of objects
        if isinstance(normalized_parts, list) and len(normalized_parts) > 0:
            converted_parts = []
            for item in normalized_parts:
                if isinstance(item, str):
                    # Convert string to object format
                    converted_parts.append({
                        "part_number": None,
                        "description": item,
                        "name": item,
                        "quantity": None,
                        "unit_price": None,
                        "line_total": None
                    })
                elif isinstance(item, dict):
                    # Already an object, ensure all expected keys exist
                    converted_parts.append({
                        "part_number": item.get("part_number"),
                        "description": item.get("description") or item.get("name"),
                        "name": item.get("name") or item.get("description"),
                        "serial_number": item.get("serial_number"),
                        "quantity": item.get("quantity"),
                        "unit_price": item.get("unit_price") or item.get("prix_unitaire"),
                        "price": item.get("price") or item.get("prix"),
                        "line_total": item.get("line_total") or item.get("total_ligne"),
                        "manufacturer": item.get("manufacturer") or item.get("fabricant")
                    })
                else:
                    converted_parts.append(item)
            normalized_parts = converted_parts
        
        # Write SAME data to both keys (for backward compat) - NOT DUPLICATED
        data["parts"] = normalized_parts
        data["parts_replaced"] = normalized_parts  # Same reference, not concatenated
        
        # Log parts normalization
        logger.info(
            f"OCR PARTS NORMALIZED | doc_type={document_type} | "
            f"source_key={source_key} | count={len(normalized_parts)}"
        )
        
        return data
    
    async def analyze_image(
        self, 
        image_base64: str, 
        document_type: str
    ) -> Dict[str, Any]:
        """
        Analyze an image using OpenAI Vision
        
        Args:
            image_base64: Base64 encoded image
            document_type: Type of document (maintenance_report, stc, invoice)
            
        Returns:
            Dictionary with raw_text and extracted_data
        """
        try:
            # Get appropriate prompt
            prompt = self._get_prompt_for_document_type(document_type)
            
            # Prepare image URL (handle both with and without data URI prefix)
            if not image_base64.startswith('data:'):
                image_url = f"data:image/jpeg;base64,{image_base64}"
            else:
                image_url = image_base64
            
            logger.info(f"Analyzing {document_type} document with OpenAI Responses API")
            
            # Call OpenAI Responses API (direct, no Emergent proxy)
            response = self.client.responses.create(
                model="gpt-4.1-mini",
                input=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "input_text",
                                "text": prompt
                            },
                            {
                                "type": "input_image",
                                "image_url": image_url
                            }
                        ]
                    }
                ]
            )
            
            # Extract response using Responses API output_text
            raw_response = (response.output_text or "").strip()
            logger.info(f"OCR Response received: {len(raw_response)} characters")
            
            # Clean and parse JSON
            cleaned_json = self._clean_json_response(raw_response)
            
            try:
                extracted_data = json.loads(cleaned_json)
                # Normalize keys from French to English (if any)
                extracted_data = self._normalize_ocr_keys(extracted_data)
                # Normalize parts to ensure both 'parts' and 'parts_replaced' exist
                extracted_data = self._normalize_parts(extracted_data, document_type)
            except json.JSONDecodeError as e:
                logger.warning(f"Failed to parse JSON: {e}")
                # Return raw text if JSON parsing fails
                extracted_data = {
                    "raw_response": raw_response,
                    "parse_error": str(e)
                }
            
            # Transform to standard format based on document type
            structured_data = self._transform_to_standard_format(
                extracted_data, 
                document_type
            )
            
            # ============================================================
            # REPORT TYPE CLASSIFICATION (TC-SAFE: Suggestion only)
            # ============================================================
            try:
                # Classify report type from raw OCR text
                classification_result = classify_report_type(raw_response)
                
                # Add classification to structured data (as optional field)
                structured_data["report_classification"] = classification_result.to_dict()
                
                logger.info(
                    f"OCR CLASSIFICATION ADDED | type={classification_result.suggested_report_type} | "
                    f"confidence={classification_result.confidence:.2f}"
                )
            except Exception as class_error:
                logger.warning(f"Report classification failed (non-blocking): {class_error}")
                # Classification failure should not block OCR - just skip it
                structured_data["report_classification"] = None
            
            return {
                "success": True,
                "raw_text": raw_response,
                "extracted_data": structured_data
            }
            
        except Exception as e:
            logger.error(f"OCR analysis failed: {str(e)}")
            return {
                "success": False,
                "error": str(e),
                "raw_text": None,
                "extracted_data": None
            }
    
    def _transform_to_standard_format(
        self, 
        data: Dict[str, Any], 
        document_type: str
    ) -> Dict[str, Any]:
        """Transform extracted data to standard format"""
        
        if document_type == "maintenance_report":
            return self._transform_maintenance_report(data)
        elif document_type == "stc":
            return self._transform_stc(data)
        elif document_type == "invoice":
            return self._transform_invoice(data)
        else:
            return data
    
    def _transform_maintenance_report(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Transform maintenance report data"""
        # Handle ELT data
        elt_data = data.get("elt_data", {})
        if not isinstance(elt_data, dict):
            elt_data = {}
        
        # Handle component work data
        component_work = data.get("component_work", {})
        if not isinstance(component_work, dict):
            component_work = {}
        
        return {
            "date": data.get("date"),
            "ame_name": data.get("ame_name"),
            "amo_name": data.get("amo_name"),
            "ame_license": data.get("ame_license"),
            "work_order_number": data.get("work_order_number"),
            "description": data.get("description"),
            "airframe_hours": self._safe_float(data.get("airframe_hours")),
            "engine_hours": self._safe_float(data.get("engine_hours")),
            "propeller_hours": self._safe_float(data.get("propeller_hours")),
            "remarks": data.get("remarks"),
            "labor_cost": self._safe_float(data.get("labor_cost")),
            "parts_cost": self._safe_float(data.get("parts_cost")),
            "total_cost": self._safe_float(data.get("total_cost")),
            "component_work": {
                "propeller": {
                    "detected": component_work.get("propeller", {}).get("detected", False),
                    "type": component_work.get("propeller", {}).get("type"),
                    "manufacturer": component_work.get("propeller", {}).get("manufacturer"),
                    "model": component_work.get("propeller", {}).get("model"),
                    "work_type": component_work.get("propeller", {}).get("work_type"),
                    "hours_since_work": self._safe_float(component_work.get("propeller", {}).get("hours_since_work")),
                    "work_date": component_work.get("propeller", {}).get("work_date")
                },
                "magnetos": {
                    "detected": component_work.get("magnetos", {}).get("detected", False),
                    "manufacturer": component_work.get("magnetos", {}).get("manufacturer"),
                    "model": component_work.get("magnetos", {}).get("model"),
                    "work_type": component_work.get("magnetos", {}).get("work_type"),
                    "hours_since_work": self._safe_float(component_work.get("magnetos", {}).get("hours_since_work")),
                    "work_date": component_work.get("magnetos", {}).get("work_date")
                },
                "avionics_certification": {
                    "detected": component_work.get("avionics_certification", {}).get("detected", False),
                    "type": component_work.get("avionics_certification", {}).get("type"),
                    "certification_date": component_work.get("avionics_certification", {}).get("certification_date"),
                    "next_due_date": component_work.get("avionics_certification", {}).get("next_due_date")
                },
                "vacuum_pump": {
                    "detected": component_work.get("vacuum_pump", {}).get("detected", False),
                    "manufacturer": component_work.get("vacuum_pump", {}).get("manufacturer"),
                    "model": component_work.get("vacuum_pump", {}).get("model"),
                    "work_type": component_work.get("vacuum_pump", {}).get("work_type"),
                    "hours_since_work": self._safe_float(component_work.get("vacuum_pump", {}).get("hours_since_work")),
                    "work_date": component_work.get("vacuum_pump", {}).get("work_date")
                },
                "engine": {
                    "detected": component_work.get("engine", {}).get("detected", False),
                    "model": component_work.get("engine", {}).get("model"),
                    "work_type": component_work.get("engine", {}).get("work_type"),
                    "hours_since_work": self._safe_float(component_work.get("engine", {}).get("hours_since_work")),
                    "work_date": component_work.get("engine", {}).get("work_date")
                }
            },
            "ad_sb_references": [
                {
                    "adsb_type": ref.get("adsb_type", "AD"),
                    "reference_number": ref.get("reference_number", ""),
                    "status": ref.get("status", "UNKNOWN"),
                    "compliance_date": ref.get("compliance_date"),
                    "airframe_hours": self._safe_float(ref.get("airframe_hours")),
                    "engine_hours": self._safe_float(ref.get("engine_hours")),
                    "propeller_hours": self._safe_float(ref.get("propeller_hours")),
                    "description": ref.get("description")
                }
                for ref in data.get("ad_sb_references", [])
            ],
            "parts_replaced": [
                {
                    "part_number": part.get("part_number", ""),
                    "name": part.get("name"),
                    "serial_number": part.get("serial_number"),
                    "quantity": part.get("quantity", 1),
                    "price": self._safe_float(part.get("price")),
                    "supplier": part.get("supplier")
                }
                for part in data.get("parts_replaced", [])
            ],
            "stc_references": [
                {
                    "stc_number": stc.get("stc_number", ""),
                    "title": stc.get("title"),
                    "description": stc.get("description"),
                    "installation_date": stc.get("installation_date")
                }
                for stc in data.get("stc_references", [])
            ],
            "elt_data": {
                "detected": elt_data.get("detected", False),
                "brand": elt_data.get("brand"),
                "model": elt_data.get("model"),
                "serial_number": elt_data.get("serial_number"),
                "installation_date": elt_data.get("installation_date"),
                "certification_date": elt_data.get("certification_date"),
                "battery_expiry_date": elt_data.get("battery_expiry_date"),
                "battery_install_date": elt_data.get("battery_install_date"),
                "battery_interval_months": elt_data.get("battery_interval_months"),
                "beacon_hex_id": elt_data.get("beacon_hex_id")
            }
        }
    
    def _transform_stc(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Transform STC data"""
        return {
            "stc_references": [{
                "stc_number": data.get("stc_number", ""),
                "title": data.get("title"),
                "description": data.get("description"),
                "holder": data.get("holder"),
                "applicable_models": data.get("applicable_models", []),
                "installation_date": data.get("installation_date"),
                "installation_airframe_hours": self._safe_float(
                    data.get("installation_airframe_hours")
                ),
                "installed_by": data.get("installed_by"),
                "work_order_reference": data.get("work_order_reference"),
                "remarks": data.get("remarks")
            }],
            "ad_sb_references": [],
            "parts_replaced": []
        }
    
    def _transform_invoice(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Transform invoice data"""
        parts = []
        for part in data.get("parts", []):
            parts.append({
                "part_number": part.get("part_number", ""),
                "name": part.get("name") or part.get("description"),
                "description": part.get("description") or part.get("name"),
                "serial_number": part.get("serial_number"),
                "quantity": part.get("quantity", 1),
                "price": self._safe_float(
                    part.get("total_price") or part.get("unit_price") or part.get("price")
                ),
                "unit_price": self._safe_float(part.get("unit_price")),
                "line_total": self._safe_float(part.get("line_total")),
                "supplier": data.get("supplier") or data.get("vendor_name"),
                "manufacturer": part.get("manufacturer")
            })
        
        return {
            "invoice_number": data.get("invoice_number"),
            "invoice_date": data.get("invoice_date"),  # FIXED: use invoice_date not date
            "supplier": data.get("supplier") or data.get("vendor_name"),
            "vendor_name": data.get("vendor_name") or data.get("supplier"),
            "total": self._safe_float(data.get("total") or data.get("total_cost")),
            "total_cost": self._safe_float(data.get("total_cost") or data.get("total")),
            "labor_hours": self._safe_float(data.get("labor_hours")),
            "labor_cost": self._safe_float(data.get("labor_cost")),
            "parts_cost": self._safe_float(data.get("parts_cost")),
            "currency": data.get("currency", "CAD"),
            "parts": parts,  # Keep as "parts" for APPLY logic
            "parts_replaced": parts,  # Also provide as parts_replaced for compatibility
            "ad_sb_references": data.get("ad_sb_references", []),
            "stc_references": data.get("stc_references", [])
        }
    
    def _safe_float(self, value) -> Optional[float]:
        """Safely convert value to float"""
        if value is None:
            return None
        try:
            return float(value)
        except (ValueError, TypeError):
            return None


# Create singleton instance
ocr_service = OCRService()
