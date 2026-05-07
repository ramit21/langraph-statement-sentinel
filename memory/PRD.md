# PRD — Ledger Sentinel (`ledger-sentinel-langraph`)

## Original problem statement
Build a privacy-first **Multi-Agent Finance Statement Analyzer** that ingests
unstructured multi-format bank-statement PDFs, extracts transactions with high
fidelity, and exposes a secure Q&A interface — without ever leaking PII to the
LLM or logs.

Hard requirements:
- Python 3.11+ with **LangGraph** state machine.
- **PyMuPDF4LLM** PDF→Markdown ingestion + local **FAISS** vector store.
- Custom `PIIMiddleware` (mask CC / account / SSN) and
  `ContentSafetyMiddleware` (block harmful + financially-fraudulent output).
- Documentation: `README.md`, `SETUP.md`, `DEVELOPER_GUIDE.md`, `.env.example`.
- Repository name approved by user: **`ledger-sentinel-langraph`**.

## User personas
- **Compliance-conscious finance user** — wants to analyze their own / clients'
  statements in an environment where PII never leaves the box.
- **Technical reviewer / auditor** — needs to verify the masking pipeline and
  understand each agent's role (hence the developer guide).

## Architecture
- Backend FastAPI (`/api/finance/*`).
- LangGraph `extraction_graph`: `extract → categorize → calculate`.
- LangGraph `qa_graph`: `qa` (RAG over masked FAISS, guard-railed).
- Mongo collection `statements` stores doc metadata + transactions.
- React 19 single-page Dashboard (Swiss / high-contrast aesthetic).

## Models
- `gemini-3.1-pro-preview` for multimodal PDF extraction.
- `claude-sonnet-4-5-20250929` for categorization + grounded Q&A.
- `sentence-transformers/all-MiniLM-L6-v2` for local embeddings.

## What's been implemented (2026-05-01)
- ✅ Backend: middleware, ingest, llm_clients, nodes, graph, FastAPI routes.
- ✅ Frontend: Dashboard with upload zone, doc list, summary cards, masked
  transactions table, guarded Q&A chat with sources.
- ✅ PII pill renderer + JetBrains-Mono numerals + emerald safety badges.
- ✅ Documentation: `README.md`, `SETUP.md`, `DEVELOPER_GUIDE.md`,
  `backend/.env.example`.
- ✅ End-to-end validated by testing agent: 10/10 backend tests pass,
  frontend Playwright walk-through 100% green, on-disk masked markdown
  verified to contain `[REDACTED_*]` tokens and not the original PII.

## P0 / blocking — all complete

## P1 — backlog
- Stream long Gemini extractions to the UI (SSE) so the upload spinner
  reflects per-stage progress.
- Per-document delete + bulk re-categorize.
- CSV / OFX export (PII-masked or unmasked behind a confirm dialog).

## P2 — future
- OCR fallback for scanned-only PDFs (Tesseract via PyMuPDF4LLM).
- Multi-statement aggregation across uploaded periods.
- "What-if" interest forecaster (deterministic Decimal math, no LLM).
- Splitting Dashboard.jsx into smaller files (cosmetic).

## Code-review notes received from testing agent
1. Logger init moved above route bodies (already addressed).
2. `_ACCT_RE` is intentionally greedy — false-positive masking is preferred
   over false-negative PII leaks for a finance tool.
3. Upload returns 200 even when the graph fails — the response still includes
   `graph_error` so the UI can surface it.
