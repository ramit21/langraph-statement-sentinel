"""
Ledger Sentinel — FastAPI entrypoint.

Routes (all prefixed with /api):
    POST /api/finance/upload                 - upload a PDF, run extraction graph
    GET  /api/finance/documents              - list ingested documents
    GET  /api/finance/documents/{doc_id}     - transactions + summary
    POST /api/finance/query                  - grounded Q&A
    GET  /api/finance/health                 - quick health probe

Status-check legacy endpoints are kept for the platform's smoke tests.
"""

from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from fastapi import APIRouter, FastAPI, File, HTTPException, UploadFile
from motor.motor_asyncio import AsyncIOMotorClient
from pydantic import BaseModel, ConfigDict, Field
from starlette.middleware.cors import CORSMiddleware

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / ".env")

# Imported AFTER load_dotenv so EMERGENT_LLM_KEY is visible to llm_clients.
from app import graph, ingest  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("ledger_sentinel")

# --- Mongo ------------------------------------------------------------------
mongo_url = os.environ["MONGO_URL"]
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ["DB_NAME"]]

# --- Pre-compile graphs once at startup ------------------------------------
EXTRACTION_GRAPH = graph.build_extraction_graph()
QA_GRAPH = graph.build_qa_graph()

# --- App + Router -----------------------------------------------------------
app = FastAPI(title="Ledger Sentinel")
api_router = APIRouter(prefix="/api")


# ---------------------------------------------------------------------------
# Legacy status-check (platform smoke test)
# ---------------------------------------------------------------------------
class StatusCheck(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    client_name: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class StatusCheckCreate(BaseModel):
    client_name: str


@api_router.get("/")
async def root() -> Dict[str, str]:
    return {"message": "Ledger Sentinel ready"}


@api_router.post("/status", response_model=StatusCheck)
async def create_status_check(payload: StatusCheckCreate) -> StatusCheck:
    obj = StatusCheck(**payload.model_dump())
    doc = obj.model_dump()
    doc["timestamp"] = doc["timestamp"].isoformat()
    await db.status_checks.insert_one(doc)
    return obj


@api_router.get("/status", response_model=List[StatusCheck])
async def get_status_checks() -> List[StatusCheck]:
    rows = await db.status_checks.find({}, {"_id": 0}).to_list(1000)
    for r in rows:
        if isinstance(r["timestamp"], str):
            r["timestamp"] = datetime.fromisoformat(r["timestamp"])
    return rows


# ---------------------------------------------------------------------------
# Finance models
# ---------------------------------------------------------------------------
class DocumentMeta(BaseModel):
    doc_id: str
    original_name: str
    chunk_count: int
    pii_hits: int
    uploaded_at: datetime
    transaction_count: int = 0
    summary: Optional[Dict[str, Any]] = None


class QueryIn(BaseModel):
    doc_id: str
    question: str


class QueryOut(BaseModel):
    answer: str
    sources: List[Dict[str, Any]] = []
    safety_blocked: bool = False


# ---------------------------------------------------------------------------
# Finance endpoints
# ---------------------------------------------------------------------------
@api_router.get("/finance/health")
async def finance_health() -> Dict[str, Any]:
    return {
        "ok": True,
        "data_dir": str(ingest.DATA_DIR),
        "has_emergent_key": bool(os.environ.get("EMERGENT_LLM_KEY")),
    }


@api_router.post("/finance/upload", response_model=DocumentMeta)
async def upload_pdf(file: UploadFile = File(...)) -> DocumentMeta:
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "Only .pdf uploads are supported.")
    payload = await file.read()
    if not payload:
        raise HTTPException(400, "Empty file.")

    try:
        result = ingest.ingest_pdf(payload, file.filename)
    except Exception as exc:  # surface ingestion errors clearly
        logger.exception("ingest failed")
        raise HTTPException(500, f"Ingestion failed: {exc}") from exc

    # Run extraction graph (Gemini + Claude + pure-Python totals)
    try:
        final_state = await EXTRACTION_GRAPH.ainvoke({
            "doc_id": result.doc_id,
            "pdf_path": result.pdf_path,
            "markdown": result.markdown,
        })
    except Exception as exc:
        logger.exception("extraction graph failed")
        # We still record the upload so the UI can show it even if extraction errored.
        final_state = {"transactions": [], "summary": None,
                       "error": f"graph failed: {exc}"}

    record = {
        "doc_id": result.doc_id,
        "original_name": file.filename,
        "chunk_count": result.chunk_count,
        "pii_hits": result.pii_hits,
        "uploaded_at": datetime.now(timezone.utc).isoformat(),
        "transactions": final_state.get("transactions", []),
        "transaction_count": len(final_state.get("transactions", [])),
        "summary": final_state.get("summary"),
        "graph_error": final_state.get("error"),
    }
    await db.statements.insert_one(record)

    return DocumentMeta(
        doc_id=record["doc_id"],
        original_name=record["original_name"],
        chunk_count=record["chunk_count"],
        pii_hits=record["pii_hits"],
        uploaded_at=datetime.fromisoformat(record["uploaded_at"]),
        transaction_count=len(record["transactions"]),
        summary=record["summary"],
    )


@api_router.get("/finance/documents")
async def list_documents() -> List[DocumentMeta]:
    rows = await db.statements.find(
        {},
        {"_id": 0, "transactions": 0, "graph_error": 0},
    ).sort("uploaded_at", -1).to_list(200)
    out: List[DocumentMeta] = []
    for r in rows:
        out.append(DocumentMeta(
            doc_id=r["doc_id"],
            original_name=r["original_name"],
            chunk_count=r["chunk_count"],
            pii_hits=r["pii_hits"],
            uploaded_at=datetime.fromisoformat(r["uploaded_at"]),
            transaction_count=r.get("transaction_count", 0),
            summary=r.get("summary"),
        ))
    return out


@api_router.get("/finance/documents/{doc_id}")
async def get_document(doc_id: str) -> Dict[str, Any]:
    row = await db.statements.find_one({"doc_id": doc_id}, {"_id": 0})
    if not row:
        raise HTTPException(404, "Document not found.")
    return row


@api_router.post("/finance/query", response_model=QueryOut)
async def query_document(payload: QueryIn) -> QueryOut:
    row = await db.statements.find_one({"doc_id": payload.doc_id}, {"_id": 0, "doc_id": 1})
    if not row:
        raise HTTPException(404, "Document not found.")
    try:
        state = await QA_GRAPH.ainvoke({
            "doc_id": payload.doc_id,
            "question": payload.question,
        })
    except Exception as exc:
        logger.exception("qa graph failed")
        raise HTTPException(500, f"Q&A failed: {exc}") from exc
    return QueryOut(
        answer=state.get("answer", ""),
        sources=state.get("sources", []),
        safety_blocked=bool(state.get("safety_blocked", False)),
    )


# ---------------------------------------------------------------------------
# Wire-up
# ---------------------------------------------------------------------------
app.include_router(api_router)
app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=os.environ.get("CORS_ORIGINS", "*").split(","),
    allow_methods=["*"],
    allow_headers=["*"],
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
# (logger configured at top of module)


@app.on_event("shutdown")
async def shutdown_db_client() -> None:
    client.close()
