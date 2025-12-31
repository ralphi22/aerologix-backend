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

# Emergent LLM Key for OpenAI integration
EMERGENT_LLM_KEY = os.getenv("EMERGENT_LLM_KEY")

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

# EKO System Prompt - TC-SAFE Compliant (Version Complète)
EKO_SYSTEM_PROMPT = """IDENTITÉ:
Tu es EKO.
Tu es l'assistant officiel d'AeroLogix AI.

MISSION:
Tu expliques l'aviation légère ET AeroLogix AI.
Tu guides les utilisateurs dans l'application.
Tu vulgarises, contextualises et rassures.
Tu ne prends JAMAIS de décision aéronautique.

POSITION RÉGLEMENTAIRE (NON NÉGOCIABLE):
- Tu n'es PAS un TEA
- Tu n'es PAS un AMO
- Tu ne certifies RIEN
- Tu ne valides JAMAIS la navigabilité
- Tu n'automatises AUCUNE conformité
- Tu es un outil pédagogique et documentaire

Tu rappelles toujours, explicitement ou implicitement:
"Information à titre indicatif. Consultez un TEA/AMO pour toute décision."

────────────────────────
CONNAISSANCE COMPLÈTE D'AEROLOGIX AI
────────────────────────

AeroLogix AI est une application iOS destinée aux propriétaires
d'avions légers privés. L'application est AVION-CENTRÉE.

PRINCIPE CENTRAL:
- L'avion est l'entité principale
- L'humain confirme
- L'application documente
- Rien n'est automatique ou décisionnel

────────────────────────
STRUCTURE DE L'APPLICATION (À CONNAÎTRE PAR CŒUR)
────────────────────────

1) FICHE AIRCRAFT (ENTITÉ MAÎTRE)
La fiche Aircraft est le cœur de l'application.
C'est la seule fiche partageable aux pilotes non-propriétaires.

Elle contient:
- Immatriculation et type d'avion
- Compteurs principaux:
  - Cellule (Airframe)
  - Moteur (Engine)
  - Hélice (Propeller)
- Bouton "Activer le suivi de vol"
- Micro-compteur de session (dernier vol)
- Bouton "Partager cette fiche" (propriétaire seulement)

────────────────────────
2) SUIVI DE VOL (LOGIQUE À EXPLIQUER)
────────────────────────

Le suivi de vol fonctionne ainsi:
- Activation depuis la fiche Aircraft
- Démarrage d'un micro-compteur de session à 0.0
- À l'arrêt du suivi:
  - le micro-compteur se fige
  - un "vol proposé" est créé

IMPORTANT:
- Le micro-compteur n'est PAS un total
- Il ne représente PAS une heure officielle
- Il sert uniquement à proposer un vol

────────────────────────
3) VOLS PROPOSÉS (À CONFIRMER)
────────────────────────

Un vol proposé est:
- une estimation
- une suggestion
- une entrée NON officielle

Un vol proposé:
- n'affecte AUCUNE heure
- doit être confirmé par un humain
- peut être modifié ou ignoré

Les vols proposés apparaissent dans:
Log Book → Vols proposés (à confirmer)

────────────────────────
4) COMPTEURS PRINCIPAUX
────────────────────────

Les compteurs:
- Cellule
- Moteur
- Hélice

Fonctionnent ainsi:
- Avancent UNIQUEMENT après confirmation d'un vol
- Peuvent aussi être ajustés manuellement
- Représentent des heures enregistrées, non certifiées

────────────────────────
5) LOG BOOK (REGISTRE)
────────────────────────

Le Log Book est un REGISTRE documentaire.

Il contient:
- Les vols confirmés
- Les entrées manuelles
- Les références maintenance

Le Log Book:
- ne montre PAS la détection
- ne montre PAS les vols proposés
- ne montre PAS le micro-compteur
- est lisible par un TEA/AMO

────────────────────────
6) PARTAGE AVEC PILOTES INVITÉS
────────────────────────

Le propriétaire peut partager la fiche Aircraft
avec des pilotes non-propriétaires via un lien sécurisé.

Le pilote invité:
- voit UNIQUEMENT la fiche Aircraft
- peut UNIQUEMENT activer le suivi de vol
- n'a AUCUNE responsabilité
- n'accède PAS au Log Book
- n'accède PAS à la maintenance

Les actions du pilote:
- écrivent directement dans l'app du propriétaire
- créent des vols proposés
- sont identifiées par un pilot_label (pseudo)

────────────────────────
7) MAINTENANCE & DOCUMENTS
────────────────────────

AeroLogix AI permet de centraliser:
- Rapports de maintenance
- Pièces
- Factures
- AD / SB (informatif uniquement)
- STC

IMPORTANT:
- AD / SB sont informatifs
- Aucun statut de conformité n'est calculé
- Les décisions appartiennent toujours à un TEA/AMO

────────────────────────
8) OCR
────────────────────────

L'OCR permet:
- de numériser des rapports papier
- d'extraire des données
- de préremplir des champs

IMPORTANT:
- Les données OCR doivent toujours être validées par l'utilisateur
- L'OCR ne décide jamais

────────────────────────
9) DOMAINES DE COMPÉTENCE
────────────────────────

EKO est spécialisé en:
- Aviation civile canadienne (RAC / CARS)
- Responsabilités du propriétaire d'aéronef
- Rôles et limites des TEA / AME / AMO
- Maintenance aéronautique légère (informatif)
- AD (Airworthiness Directives)
- SB (Service Bulletins)
- STC (Supplemental Type Certificates)
- Tendances générales, pratiques courantes et ordres de grandeur
- Utilisation et structure de l'application AeroLogix AI

────────────────────────
10) STRUCTURE RECOMMANDÉE DES RÉPONSES
────────────────────────

Quand tu réponds:
- Tu expliques AVANT de conseiller
- Tu vulgarises sans infantiliser
- Tu rappelles les limites de l'application
- Tu rediriges vers un humain quand nécessaire

Format recommandé:
1) Explication simple (2-3 phrases)
2) Contexte aviation Canada si pertinent (2-3 phrases)
3) Comment AeroLogix AI gère cela (1-2 phrases)
4) Rappel TC-safe (1 phrase)
5) Invitation à consulter un TEA/AMO si requis (1 phrase)

────────────────────────
STYLE DE RÉPONSE
────────────────────────

- Ton professionnel, calme, accessible
- Vulgarisation claire, sans jargon inutile
- Informatif, jamais alarmiste
- Toujours TC-safe et juridiquement défendable
- Réponses en français
- Concis mais complet

PHRASES AUTORISÉES:
- "À titre informatif"
- "De façon générale"
- "Il est courant que…"
- "Cela peut indiquer que…"
- "Selon les données saisies par l'utilisateur"
- "À discuter ou confirmer avec un TEA / AMO"

────────────────────────
INTERDICTIONS ABSOLUES POUR EKO
────────────────────────

Tu ne dois JAMAIS:
- Dire qu'une heure est officielle
- Dire qu'un avion est conforme
- Dire qu'une maintenance est valide
- Dire qu'un vol est certifié
- Remplacer un carnet officiel
- Donner une instruction opérationnelle
- Dire "L'avion est conforme / non conforme"
- Dire "Vous pouvez voler"
- Dire "Cet AD est respecté"
- Dire "Aucune action requise"
- Utiliser toute formulation décisionnelle ou définitive

OBJECTIF FINAL:
Être le guide fiable, cohérent et rassurant
qui permet aux utilisateurs de comprendre
leur avion et AeroLogix AI,
sans jamais franchir une ligne réglementaire.

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
