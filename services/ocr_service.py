"""
OCR Service for AeroLogix AI
Uses OpenAI Vision (GPT-4o) via Emergent LLM Key
"""

import os
import json
import logging
import re
from typing import Optional, Dict, Any
from datetime import datetime
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# Initialize OpenAI client with Emergent LLM Key
EMERGENT_LLM_KEY = os.getenv("EMERGENT_LLM_KEY", "sk-emergent-eAf207608993771Ad9")

client = OpenAI(
    api_key=EMERGENT_LLM_KEY,
    base_url="https://integrations.emergentagent.com/llm/openai/v1",
    timeout=60.0  # 60 second timeout to prevent hanging
)

# Prompts spécialisés par type de document
MAINTENANCE_REPORT_PROMPT = """You are an aviation maintenance expert. Analyze this maintenance report image and extract ALL structured information.

IMPORTANT: Respond ONLY with valid JSON, no text before or after.

CRITICAL RULES FOR NUMBERS:
- Spaces or commas may be thousands separators (e.g., "6 344.6" = 6344.6, "6,344.6" = 6344.6)
- Decimal point is always "." (e.g., 6344.6)
- Be very careful not to reverse digits
- If uncertain about a number, read it multiple times carefully

COMPONENT KEYWORDS TO DETECT (ENGLISH):
- PROPELLER/PROP: PROPELLER, PROP, HARTZELL, MCCAULEY, SENSENICH, FIXED PITCH, VARIABLE PITCH, CONSTANT SPEED
- MAGNETO: MAGNETO, MAGNETOS, SLICK, BENDIX, IMPULSE COUPLING
- AVIONICS 24 MONTHS: 24 MONTH, ALTIMETER, PITOT, STATIC, PITOT-STATIC, TRANSPONDER, ENCODER, IFR CERTIFICATION
- VACUUM PUMP: VACUUM PUMP, DRY AIR PUMP, GYRO INSTRUMENTS

Expected JSON structure:
{
    "date": "YYYY-MM-DD or null",
    "ame_name": "Mechanic/AME name or null",
    "amo_name": "Maintenance organization/AMO name or null",
    "ame_license": "AME license number or null",
    "work_order_number": "Work Order number or null",
    "description": "Complete description of work performed",
    "airframe_hours": decimal number or null,
    "engine_hours": decimal number or null,
    "propeller_hours": decimal number or null,
    "remarks": "Additional remarks or null",
    "labor_cost": number or null,
    "parts_cost": number or null,
    "total_cost": number or null,
    "component_work": {
        "propeller": {
            "detected": true/false,
            "type": "fixed or variable or null",
            "manufacturer": "HARTZELL, MCCAULEY, SENSENICH, etc. or null",
            "model": "model or null",
            "work_type": "INSPECTION, OVERHAUL, REPAIR or null",
            "hours_since_work": number or null,
            "work_date": "YYYY-MM-DD or null"
        },
        "magnetos": {
            "detected": true/false,
            "manufacturer": "SLICK, BENDIX, etc. or null",
            "model": "model or null",
            "work_type": "500H INSPECTION, OVERHAUL, TIMING or null",
            "hours_since_work": number or null,
            "work_date": "YYYY-MM-DD or null"
        },
        "avionics_certification": {
            "detected": true/false,
            "type": "ALTIMETER, PITOT-STATIC, TRANSPONDER or null",
            "certification_date": "YYYY-MM-DD or null",
            "next_due_date": "YYYY-MM-DD or null",
            "status": "CURRENT, DUE, PAST_DUE or null (detect from text: PAST DUE, DUE, OVERDUE)"
        },
        "vacuum_pump": {
            "detected": true/false,
            "manufacturer": "manufacturer or null",
            "model": "model or null",
            "work_type": "REPLACEMENT, INSPECTION or null",
            "hours_since_work": number or null,
            "work_date": "YYYY-MM-DD or null"
        },
        "engine": {
            "detected": true/false,
            "model": "engine model or null",
            "work_type": "OVERHAUL, TOP OVERHAUL, INSPECTION or null",
            "hours_since_work": number or null,
            "work_date": "YYYY-MM-DD or null"
        }
    },
    "ad_sb_references": [
        {
            "adsb_type": "AD or SB",
            "reference_number": "e.g., AD 2024-05-12",
            "status": "COMPLIED, PENDING or UNKNOWN",
            "compliance_date": "YYYY-MM-DD or null",
            "airframe_hours": number or null,
            "engine_hours": number or null,
            "propeller_hours": number or null,
            "description": "Description or null"
        }
    ],
    "parts_replaced": [
        {
            "part_number": "P/N",
            "name": "Part name",
            "serial_number": "S/N or null",
            "quantity": integer,
            "price": number or null,
            "supplier": "Supplier or null"
        }
    ],
    "stc_references": [
        {
            "stc_number": "STC number",
            "title": "Title or null",
            "description": "Description or null",
            "installation_date": "YYYY-MM-DD or null"
        }
    ],
    "elt_data": {
        "detected": true/false,
        "brand": "Artex, Kannad, ACK, etc. or null",
        "model": "ELT model or null",
        "serial_number": "ELT serial number or null",
        "installation_date": "YYYY-MM-DD or null",
        "certification_date": "YYYY-MM-DD or null",
        "battery_expiry_date": "YYYY-MM-DD or null",
        "battery_install_date": "YYYY-MM-DD or null",
        "battery_interval_months": number or null,
        "beacon_hex_id": "Beacon hex ID or null"
    }
}

IMPORTANT RULES:
1. Detect ALL AD (Airworthiness Directive) references - typical format: AD XXXX-XX-XX
2. Detect ALL SB (Service Bulletin) references - typical format: SB XX-XXXX
3. For each AD/SB, determine status: COMPLIED if clearly indicated as done, PENDING if to be done, UNKNOWN otherwise
4. Extract airframe (airframe), engine (engine) and propeller (propeller) hours if mentioned
5. Identify all replaced parts with their P/N
6. If information is not found, use null
7. ELT DETECTION: Look for any mention of "ELT", "Emergency Locator Transmitter"
   - Common brands: Artex, Kannad, ACK, Ameri-King, ACR
8. COMPONENT WORK: Actively look for work on propeller, magnetos, avionics, vacuum pump, engine
   - Set detected=true only if clearly mentioned in the document

Analyze the image now:"""

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

INVOICE_PROMPT = """Tu es un expert en pièces aéronautiques. Analyse cette image de facture/bon de commande et extrait les informations sur les pièces.

IMPORTANT: Réponds UNIQUEMENT avec un JSON valide, sans texte avant ou après.

Structure JSON attendue:
{
    "invoice_number": "Numéro de facture ou null",
    "invoice_date": "YYYY-MM-DD ou null",
    "supplier": "Nom du fournisseur",
    "parts": [
        {
            "part_number": "P/N (numéro de pièce)",
            "name": "Nom/description de la pièce",
            "serial_number": "S/N ou null",
            "quantity": nombre,
            "unit_price": nombre ou null,
            "total_price": nombre ou null,
            "manufacturer": "Fabricant ou null"
        }
    ],
    "subtotal": nombre ou null,
    "tax": nombre ou null,
    "total": nombre ou null,
    "currency": "USD, CAD, EUR, etc."
}

Analyse l'image maintenant:"""


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
            
            logger.info(f"Analyzing {document_type} document with OpenAI Vision")
            
            # Call OpenAI Vision API
            response = self.client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": prompt
                            },
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": image_url,
                                    "detail": "high"
                                }
                            }
                        ]
                    }
                ],
                max_tokens=4096
            )
            
            # Extract response
            raw_response = response.choices[0].message.content
            logger.info(f"OCR Response received: {len(raw_response)} characters")
            
            # Clean and parse JSON
            cleaned_json = self._clean_json_response(raw_response)
            
            try:
                extracted_data = json.loads(cleaned_json)
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
                "name": part.get("name"),
                "serial_number": part.get("serial_number"),
                "quantity": part.get("quantity", 1),
                "price": self._safe_float(
                    part.get("total_price") or part.get("unit_price")
                ),
                "supplier": data.get("supplier"),
                "manufacturer": part.get("manufacturer")
            })
        
        return {
            "invoice_number": data.get("invoice_number"),
            "date": data.get("invoice_date"),
            "supplier": data.get("supplier"),
            "total_cost": self._safe_float(data.get("total")),
            "currency": data.get("currency", "USD"),
            "parts_replaced": parts,
            "ad_sb_references": [],
            "stc_references": []
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
