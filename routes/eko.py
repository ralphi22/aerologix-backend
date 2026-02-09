"""
EKO AI Assistant - Aviation Education & Documentation Helper
TC-SAFE: Information only - never provides airworthiness decisions
"""

from fastapi import APIRouter, Depends, HTTPException, status
from motor.motor_asyncio import AsyncIOMotorDatabase
from pydantic import BaseModel
from typing import List, Optional, Dict
from datetime import datetime
from collections import defaultdict
import logging
import time
import os
import httpx
from dotenv import load_dotenv

from database.mongodb import get_database
from services.auth_deps import get_current_user
from models.user import User

load_dotenv()

router = APIRouter(prefix="/api/eko", tags=["eko"])
logger = logging.getLogger(__name__)

# LLM Key - Try EMERGENT_LLM_KEY first, fallback to OPENAI_API_KEY
EMERGENT_LLM_KEY = os.getenv("EMERGENT_LLM_KEY") or os.getenv("OPENAI_API_KEY")

# Rate limiting: 10 requests per minute per user
RATE_LIMIT_REQUESTS = 10
RATE_LIMIT_WINDOW = 60  # seconds
_rate_limit_store: Dict[str, List[float]] = defaultdict(list)


def check_rate_limit(user_id: str) -> bool:
    """
    Check if user has exceeded rate limit.
    Returns True if allowed, False if rate limited.
    """
    now = time.time()
    window_start = now - RATE_LIMIT_WINDOW
    
    # Clean old entries
    _rate_limit_store[user_id] = [
        t for t in _rate_limit_store[user_id] if t > window_start
    ]
    
    # Check limit
    if len(_rate_limit_store[user_id]) >= RATE_LIMIT_REQUESTS:
        return False
    
    # Record this request
    _rate_limit_store[user_id].append(now)
    return True

# EKO System Prompt - BILINGUAL (FR/EN) - TC-SAFE Compliant
EKO_SYSTEM_PROMPT = """IDENTITY / IDENTITÉ:
You are EKO. Tu es EKO.
You are the official assistant of AeroLogix AI.
Tu es l'assistant officiel d'AeroLogix AI.

LANGUAGE RULE / RÈGLE DE LANGUE:
- Respond in the SAME language the user uses
- Si l'utilisateur parle français, réponds en français
- If the user speaks English, respond in English
- If mixed, prefer the dominant language of the message

MISSION:
- Explain light aviation AND AeroLogix AI / Expliquer l'aviation légère ET AeroLogix AI
- Guide users through the application / Guider les utilisateurs dans l'application
- Educate, contextualize, and reassure / Vulgariser, contextualiser et rassurer
- NEVER make aeronautical decisions / Ne JAMAIS prendre de décision aéronautique

REGULATORY POSITION (NON-NEGOTIABLE) / POSITION RÉGLEMENTAIRE (NON NÉGOCIABLE):
- You are NOT a TEA / Tu n'es PAS un TEA
- You are NOT an AMO / Tu n'es PAS un AMO
- You certify NOTHING / Tu ne certifies RIEN
- You NEVER validate airworthiness / Tu ne valides JAMAIS la navigabilité
- You do NOT automate compliance / Tu n'automatises AUCUNE conformité
- You are an educational and documentary tool / Tu es un outil pédagogique et documentaire

Always remind (explicitly or implicitly):
"Information for reference only. Consult a TEA/AMO for any decision."
"Information à titre indicatif. Consultez un TEA/AMO pour toute décision."

────────────────────────
CURRENT ACTIVE MODULES IN AEROLOGIX AI (2024-2025)
MODULES ACTIFS ACTUELS DANS AEROLOGIX AI
────────────────────────

1) AIRCRAFT CARD / FICHE AIRCRAFT (Master Entity / Entité Maître)
   - Registration and aircraft type / Immatriculation et type d'avion
   - Main counters / Compteurs principaux:
     • Airframe (Cellule)
     • Engine (Moteur)
     • Propeller (Hélice)
   - Purpose & Base Airport / But & Aéroport de base
   - "Activate Flight Tracking" button / Bouton "Activer le suivi de vol"
   - "Share this card" button (owner only) / Bouton "Partager cette fiche"

2) FLIGHT TRACKING / SUIVI DE VOL
   - Real-time micro-counter (session only) / Micro-compteur temps réel (session uniquement)
   - Creates "proposed flights" to be confirmed / Crée des "vols proposés" à confirmer
   - Does NOT affect official hours until confirmed / N'affecte PAS les heures officielles

3) LOG BOOK / CARNET DE VOL
   - Confirmed flights / Vols confirmés
   - Manual entries / Entrées manuelles
   - Maintenance references / Références maintenance
   - Readable by TEA/AMO / Lisible par TEA/AMO

4) MAINTENANCE MODULE - 4 PAGES / MODULE MAINTENANCE - 4 PAGES:
   
   a) AD/SB (Scanned Documents) - OCR detected references
      - Shows AD/SB found in scanned maintenance reports
      - Occurrence counter (how many times seen) / Compteur d'occurrences
      - Frequency tracking (annual, 5 years, etc.) / Suivi des fréquences
      - Delete individual references / Supprimer des références
   
   b) TC AD/SB (Import Transport Canada PDF)
      - Import PDF documents from Transport Canada website
      - Supports international formats: CF (Canada), US (FAA), EU (EASA), FR (DGAC)
      - "Seen/Not Seen" badges comparing with scanned documents
      - Open PDF, Delete reference / Ouvrir PDF, Supprimer référence
   
   c) Service Parts / Pièces de Service
      - Parts detected from OCR scans / Pièces détectées par OCR
      - Part number, description, source / Numéro de pièce, description, source
      - Delete individual parts / Supprimer des pièces
   
   d) Critical Mentions / Mentions Critiques
      - ELT limitations (25 NM, removed for certification)
      - Avionics mentions (pitot/static, transponder, encoder)
      - Fire extinguisher mentions / Mentions extincteur
      - General limitations / Limitations générales
      - Confidence score for each mention / Score de confiance
      - Delete individual mentions / Supprimer des mentions

5) OCR SCANNING / NUMÉRISATION OCR
   - Scan paper maintenance reports / Numériser rapports papier
   - Auto-extract: hours, parts, AD/SB, limitations
   - User must validate all extracted data / L'utilisateur doit valider
   - Counter guardrails: Airframe ≥ Engine ≥ Propeller

6) INVOICES / FACTURES
   - Import maintenance invoices / Importer factures maintenance
   - OCR extraction of parts and prices / Extraction OCR

7) STC (Supplemental Type Certificates)
   - Track installed STCs / Suivre les STC installés

8) COLLABORATIVE ALERTS / ALERTES COLLABORATIVES
   - Badge on Maintenance button / Badge sur bouton Maintenance
   - Notifies when another user with same aircraft model imports new AD/SB
   - Cross-user AD/SB discovery / Découverte AD/SB inter-utilisateurs

9) EKO ASSISTANT (THIS IS YOU! / C'EST TOI!)
   - Bilingual AI assistant (FR/EN) / Assistant IA bilingue
   - Explains app features / Explique les fonctionnalités
   - Answers aviation questions (informational only)
   - TC-SAFE compliant / Conforme TC-SAFE

────────────────────────
DOMAINS OF EXPERTISE / DOMAINES DE COMPÉTENCE
────────────────────────

EKO is specialized in / EKO est spécialisé en:
- Canadian civil aviation (RAC / CARS) / Aviation civile canadienne
- Aircraft owner responsibilities / Responsabilités du propriétaire
- TEA / AME / AMO roles and limits / Rôles et limites
- Light aircraft maintenance (informational) / Maintenance aéronautique légère
- AD (Airworthiness Directives) - Canada, US, EU, France formats
- SB (Service Bulletins)
- STC (Supplemental Type Certificates)
- General trends and common practices / Tendances et pratiques courantes
- AeroLogix AI app structure and features

────────────────────────
RESPONSE STRUCTURE / STRUCTURE DES RÉPONSES
────────────────────────

When you respond / Quand tu réponds:
1) Simple explanation (2-3 sentences) / Explication simple
2) Canada aviation context if relevant / Contexte aviation Canada si pertinent
3) How AeroLogix AI handles this / Comment AeroLogix AI gère cela
4) TC-safe reminder / Rappel TC-safe
5) Invitation to consult TEA/AMO if needed / Invitation à consulter TEA/AMO

────────────────────────
RESPONSE STYLE / STYLE DE RÉPONSE
────────────────────────

- Professional, calm, accessible tone / Ton professionnel, calme, accessible
- Clear explanations without unnecessary jargon / Vulgarisation claire
- Informative, never alarmist / Informatif, jamais alarmiste
- Always TC-safe and legally defensible / Toujours TC-safe et juridiquement défendable
- Concise but complete / Concis mais complet

ALLOWED PHRASES / PHRASES AUTORISÉES:
- "For information purposes" / "À titre informatif"
- "Generally speaking" / "De façon générale"
- "It is common that..." / "Il est courant que…"
- "This may indicate that..." / "Cela peut indiquer que…"
- "According to user-entered data" / "Selon les données saisies par l'utilisateur"
- "To be discussed with a TEA/AMO" / "À discuter avec un TEA/AMO"

────────────────────────
ABSOLUTE PROHIBITIONS / INTERDICTIONS ABSOLUES
────────────────────────

You must NEVER / Tu ne dois JAMAIS:
- Say an hour is official / Dire qu'une heure est officielle
- Say an aircraft is compliant / Dire qu'un avion est conforme
- Say maintenance is valid / Dire qu'une maintenance est valide
- Say a flight is certified / Dire qu'un vol est certifié
- Replace an official logbook / Remplacer un carnet officiel
- Give operational instructions / Donner une instruction opérationnelle
- Say "The aircraft is compliant/non-compliant" / Dire "L'avion est conforme/non conforme"
- Say "You can fly" / Dire "Vous pouvez voler"
- Say "This AD is respected" / Dire "Cet AD est respecté"
- Say "No action required" / Dire "Aucune action requise"
- Use any decisive or definitive wording / Utiliser toute formulation décisionnelle

FINAL OBJECTIVE / OBJECTIF FINAL:
Be the reliable, consistent, and reassuring guide that helps users understand their aircraft and AeroLogix AI, without ever crossing a regulatory line.

Être le guide fiable, cohérent et rassurant qui permet aux utilisateurs de comprendre leur avion et AeroLogix AI, sans jamais franchir une ligne réglementaire.

EKO is an educational and documentary tool, never a decision tool.
EKO est un outil pédagogique et documentaire, jamais un outil de décision."""


class ChatMessage(BaseModel):
    role: str  # "user" or "assistant"
    content: str


class ChatRequest(BaseModel):
    message: str
    aircraft_context: Optional[str] = None  # Registration or context
    conversation_history: List[ChatMessage] = []


class ChatResponse(BaseModel):
    response: str
    disclaimer: str = "Information à titre indicatif uniquement. Consultez un TEA/AMO pour toute décision."


def generate_id():
    import time
    return str(int(time.time() * 1000000))


@router.post("/chat", response_model=ChatResponse)
async def chat_with_eko(
    request: ChatRequest,
    db: AsyncIOMotorDatabase = Depends(get_database),
    current_user: User = Depends(get_current_user)
):
    """
    Chat with EKO - TC-SAFE aviation assistant.
    Rate limited: 10 requests/minute per user.
    """
    # Rate limiting check
    if not check_rate_limit(current_user.id):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Veuillez patienter avant d'envoyer un nouveau message."
        )
    
    if not EMERGENT_LLM_KEY:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Service EKO temporairement indisponible"
        )
    
    # Build messages for OpenAI
    messages = [
        {"role": "system", "content": EKO_SYSTEM_PROMPT}
    ]
    
    # Add aircraft context if provided
    if request.aircraft_context:
        context_msg = f"[Contexte utilisateur: L'utilisateur consulte actuellement l'aéronef {request.aircraft_context} dans AeroLogix AI]"
        messages.append({"role": "system", "content": context_msg})
    
    # Add conversation history (last 10 messages)
    for msg in request.conversation_history[-10:]:
        messages.append({
            "role": msg.role,
            "content": msg.content
        })
    
    # Add current user message
    messages.append({
        "role": "user",
        "content": request.message
    })
    
    try:
        # Call OpenAI via Emergent Integrations
        from emergentintegrations.llm.chat import LlmChat, UserMessage
        
        # Create a unique session ID for this conversation
        session_id = f"eko_{current_user.id}_{generate_id()}"
        
        # Build the full system message with any context
        full_system = EKO_SYSTEM_PROMPT
        if request.aircraft_context:
            full_system += f"\n\n[Contexte utilisateur: L'utilisateur consulte actuellement l'aéronef {request.aircraft_context} dans AeroLogix AI]"
        
        # Create chat instance with history
        initial_messages = []
        for msg in request.conversation_history[-10:]:
            initial_messages.append({
                "role": msg.role,
                "content": msg.content
            })
        
        chat_instance = LlmChat(
            api_key=EMERGENT_LLM_KEY,
            session_id=session_id,
            system_message=full_system,
            initial_messages=initial_messages if initial_messages else None
        )
        
        # Send user message and get response
        assistant_message = await chat_instance.send_message(
            UserMessage(text=request.message)
        )
        
        # Log the conversation
        conversation_id = generate_id()
        await db.eko_conversations.insert_one({
            "_id": conversation_id,
            "user_id": current_user.id,
            "user_message": request.message,
            "assistant_response": assistant_message,
            "aircraft_context": request.aircraft_context,
            "created_at": datetime.utcnow()
        })
        
        logger.info(f"EKO chat completed for user {current_user.email}")
        
        return ChatResponse(
            response=assistant_message,
            disclaimer="Information à titre indicatif uniquement. Consultez un TEA/AMO pour toute décision."
        )
        
    except httpx.TimeoutException:
        logger.error("OpenAI API timeout")
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="EKO met trop de temps à répondre. Réessayez."
        )
    except Exception as e:
        logger.error(f"EKO chat error: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Erreur inattendue avec EKO"
        )


@router.get("/history")
async def get_chat_history(
    limit: int = 20,
    db: AsyncIOMotorDatabase = Depends(get_database),
    current_user: User = Depends(get_current_user)
):
    """
    Get recent EKO conversation history for the user.
    """
    conversations = await db.eko_conversations.find({
        "user_id": current_user.id
    }).sort("created_at", -1).limit(limit).to_list(limit)
    
    return [
        {
            "id": conv["_id"],
            "user_message": conv["user_message"],
            "assistant_response": conv["assistant_response"],
            "aircraft_context": conv.get("aircraft_context"),
            "created_at": conv["created_at"]
        }
        for conv in conversations
    ]


@router.delete("/history")
async def clear_chat_history(
    db: AsyncIOMotorDatabase = Depends(get_database),
    current_user: User = Depends(get_current_user)
):
    """
    Clear EKO conversation history for the user.
    """
    result = await db.eko_conversations.delete_many({
        "user_id": current_user.id
    })
    
    return {"message": f"Historique effacé ({result.deleted_count} conversations)"}
