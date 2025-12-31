from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from database.mongodb import db
from config import get_settings
from routes import auth, plans, aircraft, ocr, maintenance, adsb, stc, parts, elt, invoices, components, shares, payments, fleet, eko, flight_candidates, logbook, pilot_invites
import logging

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

settings = get_settings()

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifecycle manager for the app"""
    # Startup
    await db.connect(settings.mongo_url, settings.db_name)
    logger.info("AeroLogix AI Backend started")
    yield
    # Shutdown
    await db.disconnect()
    logger.info("AeroLogix AI Backend stopped")

# Create FastAPI app
app = FastAPI(
    title="AeroLogix AI API",
    description="Aviation Maintenance Management with AI Predictions",
    version="1.0.0",
    lifespan=lifespan
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, restrict to specific origins
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
from routes import aircraft

app.include_router(auth.router)
app.include_router(plans.router)
app.include_router(aircraft.router)
app.include_router(ocr.router)
app.include_router(maintenance.router)
app.include_router(adsb.router)
app.include_router(stc.router)
app.include_router(parts.router)
app.include_router(elt.router)
app.include_router(invoices.router)
app.include_router(components.router)
app.include_router(shares.router)
app.include_router(payments.router)
app.include_router(fleet.router)
app.include_router(eko.router)
app.include_router(flight_candidates.router)
app.include_router(logbook.router)
app.include_router(pilot_invites.router)

@app.get("/")
async def root():
    return {
        "message": "AeroLogix AI API",
        "version": "1.0.0",
        "status": "running"
    }

@app.get("/api")
async def api_root():
    return {
        "message": "AeroLogix AI API",
        "endpoints": {
            "auth": "/api/auth",
            "plans": "/api/plans"
        }
    }

@app.get("/health")
async def health_check():
    return {"status": "healthy"}
