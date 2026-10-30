"""FastAPI entry point. Run from src/backend:  uvicorn app.main:app --reload"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import config
from app.routers import analysis, evidence
from app.schemas import SCHEMA_VERSION

app = FastAPI(title="Incident Reconstruction API", version=SCHEMA_VERSION)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(evidence.router)
app.include_router(analysis.router)


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "schema_version": SCHEMA_VERSION, "incident_id": config.INCIDENT_ID}
