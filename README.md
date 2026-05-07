# Ledger Sentinel — `ledger-sentinel-langraph`

> A privacy-first **Multi-Agent Finance Statement Analyzer**.
> Ingests bank-statement PDFs, extracts transactions with high fidelity, and
> answers questions about them through a guard-railed RAG pipeline — without
> ever exposing PII to the LLM.

[![python](https://img.shields.io/badge/python-3.11%2B-blue)]()
[![langgraph](https://img.shields.io/badge/orchestration-LangGraph-black)]()
[![faiss](https://img.shields.io/badge/vector--store-FAISS-cpu-green)]()

---

## Why

Bank statements are the most leakage-prone document a finance tool will
ever touch. Account numbers, card PANs and SSNs cannot reach a third-party
LLM, but the same document still has to be parsed, classified and queried.
Ledger Sentinel solves that with a small **multi-agent LangGraph** pipeline
where every LLM-facing surface is wrapped in two custom middlewares:

- `PIIMiddleware` — regex + Luhn-validated masking of credit cards,
  account numbers, and SSNs *before* prompts hit the model.
- `ContentSafetyMiddleware` — keyword / topic guardrails on model output to
  block harmful or financially-fraudulent guidance (laundering, tax evasion,
  self-harm, etc.).

## Architecture

```
                 ┌───────────────┐    ┌────────────┐
   PDF upload ─► │  ingest.py    │ ─► │  FAISS     │
                 │  pymupdf4llm  │    │  (local)   │
                 └──────┬────────┘    └────┬───────┘
                        │                  │
                ┌───────▼──────────────────▼────────┐
                │         LangGraph (graph.py)      │
                │                                   │
                │  extract  ─►  categorize  ─►  calc│
                │  (Gemini)     (Claude)        (Py)│
                └───────────────────────────────────┘
                                │
                                ▼
                         POST /api/finance/upload
                         POST /api/finance/query  ←  qa_node (Claude + RAG)
```

All LLM calls are funnelled through `with_guardrails(...)` (see `middleware.py`)
which masks PII on the way *in* and runs safety checks on the way *out*.

## API Model Strategy

| Stage         | Model                          | Why |
|---------------|--------------------------------|-----|
| PDF parsing   | **Gemini 3.1 Pro Preview** (`gemini-3.1-pro-preview`) | True multimodal — sees layouts, tables, scanned columns even when OCR struggles. |
| Reasoning/Q&A | **Claude Sonnet 4.5** (`claude-sonnet-4-5-20250929`) | Strong, careful financial reasoning; better adherence to "answer only from context" instructions. |
| Embeddings    | `sentence-transformers/all-MiniLM-L6-v2` | Runs locally — embeddings of PII-masked statements never leave the box. |

> **Note on Llama-4.** The original spec mentioned Llama-4 for logic. The
> Emergent Universal Key does not currently route to Llama-4; we use Claude
> Sonnet 4.5 instead. Swapping back is a one-liner — see
> `app/llm_clients.py` (`reason_chat`).

## Quick start

```bash
# 1. Backend deps
pip install -r backend/requirements.txt
pip install langgraph langchain langchain-community faiss-cpu pymupdf4llm \
            sentence-transformers tiktoken

# 2. Configure env
cp backend/.env.example backend/.env
#   then set EMERGENT_LLM_KEY (Profile -> Universal Key in Emergent)

# 3. Run
sudo supervisorctl restart backend
cd frontend && yarn install && yarn start
```

See **[SETUP.md](./SETUP.md)** for OS-level PDF library notes (libGL, MuPDF,
glibc), CPU vs GPU FAISS, and embedding-model selection.

See **[DEVELOPER_GUIDE.md](./DEVELOPER_GUIDE.md)** for a code-map of every
module.

## Endpoints

| Method | Path                                  | Purpose                                  |
|-------:|---------------------------------------|------------------------------------------|
| POST   | `/api/finance/upload`                 | Upload PDF → run extraction graph        |
| GET    | `/api/finance/documents`              | List ingested statements                 |
| GET    | `/api/finance/documents/{doc_id}`     | Transactions + summary for one statement |
| POST   | `/api/finance/query`                  | Grounded Q&A over a statement            |
| GET    | `/api/finance/health`                 | Liveness + LLM-key probe                 |

## Privacy posture

- PDFs persisted on disk under `LEDGER_DATA_DIR` (configurable).
- The **PII-masked** markdown is what gets chunked and embedded.
- Raw markdown is kept on disk for audit only and is never embedded.
- Every prompt is masked again at the node boundary (defense in depth).
- Logs never echo prompts or answers; failures log a short class name only.

## Refactor notes (Emergent → standalone)

If you want to deploy this outside the Emergent platform, the only
Emergent-specific surface is the `emergentintegrations.llm.chat` client used
inside `app/llm_clients.py`. Replace those two functions with vanilla
LangChain wrappers:

```python
# vision_extract  →  langchain_google_genai.ChatGoogleGenerativeAI
# reason_chat     →  langchain_anthropic.ChatAnthropic
```

Everything else (LangGraph, FAISS, PyMuPDF4LLM, the middleware, the FastAPI
layer, the React UI) is stock open-source and unaffected.

## License

MIT. See `LICENSE`.
