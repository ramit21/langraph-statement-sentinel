# DEVELOPER_GUIDE.md

A code map for `ledger-sentinel-langraph`.

```
backend/
├── server.py              # FastAPI entrypoint, routes, Mongo
├── .env                   # local secrets (not committed)
├── .env.example           # template
└── app/
    ├── middleware.py      # PIIMiddleware + ContentSafetyMiddleware
    ├── ingest.py          # PDF → markdown → FAISS
    ├── llm_clients.py     # Emergent Universal Key wrappers (Gemini / Claude)
    ├── nodes.py           # Specialized LangGraph agent nodes
    └── graph.py           # Graph compilation
frontend/
└── src/
    ├── App.js             # Router shell
    ├── index.css          # Design tokens, fonts, PII pill, scan animation
    └── components/
        ├── Dashboard.jsx  # The whole UI (left rail / center / chat)
        └── PII.jsx        # Inline redaction-token renderer
```

## `app/middleware.py`

Two middlewares + a `with_guardrails(node, pii, safety)` composer.

### `PIIMiddleware`
- Regex bank: SSN (`NNN-NN-NNNN`), credit cards (Luhn-validated, 13–19
  digits, allowing space/dash separators), bank-account numbers
  (8–17 contiguous digits not bordered by digits).
- Returns the masked text **and** a `MaskMap` so the caller can later
  reverse the mapping for display, audit, or de-mask in trusted contexts.
- `display_mask(value)` produces `••••1234`-style strings used by the React
  UI.

### `ContentSafetyMiddleware`
- Keyword backstop, deliberately conservative.
- Default policy terms cover money laundering, tax evasion, structuring,
  smurfing, and self-harm. Pass `extra_keywords=...` to extend.
- Returns a `SafetyVerdict(blocked: bool, reason: str)`.

### `with_guardrails`
A higher-order function that wraps a single LLM call:

```python
guarded = with_guardrails(my_node, pii, safety)
answer  = await guarded(prompt)   # PII-masked in, safety-checked out
```

## `app/ingest.py`

PDF lifecycle:

1. `_save_pdf(...)` — persists upload to `LEDGER_DATA_DIR/pdfs/`.
2. `pymupdf4llm.to_markdown(...)` — fast, deterministic PDF → markdown.
3. `PIIMiddleware().mask(...)` — masks PII **before** the text is embedded.
4. `RecursiveCharacterTextSplitter` — markdown-aware chunking
   (separators `\n## `, `\n### `, `\n\n`, `\n`, ` `).
5. `FAISS.from_documents(...)` — written under `faiss/<doc_id>/`.
6. Metadata + masked markdown also persisted under `meta/`.

Helpers:
- `retrieve(doc_id, query, k=6)` — similarity search used by the Q&A node.
- `get_pdf_path(doc_id)` — the original bytes for re-running extraction.
- `get_masked_markdown(doc_id)` — for downstream agents that prefer text.

## `app/llm_clients.py`

Two single-purpose async functions. Each constructs a fresh `LlmChat`
session — chat history is owned by *us* (Mongo), so transient sessions are
fine and isolate concurrent users from cross-talk.

| Function          | Provider   | Model                          | Use            |
|-------------------|------------|--------------------------------|----------------|
| `vision_extract`  | gemini     | `gemini-3.1-pro-preview`       | PDF → tx JSON  |
| `reason_chat`     | anthropic  | `claude-sonnet-4-5-20250929`   | Q&A + categorize |

`_key()` raises an explicit `RuntimeError` if `EMERGENT_LLM_KEY` is missing,
so configuration mistakes fail fast at first call.

## `app/nodes.py`

Each node is `async def node(state) -> partial_state`.

### `extract_node` (Gemini, multimodal)
- Reads the original PDF (not the converted markdown) so layout cues
  matter.
- Prompt instructs strict JSON schema; we strip code fences if Gemini
  decides to wrap output anyway.
- Re-applies PII masking on parsed descriptions for defense-in-depth.

### `categorize_node` (Claude)
- Sends a compact `idx | date | desc | amount` list with the closed
  category list.
- Robust JSON parse: malformed output → all "Other".
- Always returns a list of the same length as `transactions`.

### `calculate_node` (pure Python — **no LLM**)
- All arithmetic uses `decimal.Decimal` to avoid float drift.
- Computes `income`, `expense`, `net`, `count`, `by_category`, and
  `top_category` (largest negative bucket).
- This is intentional: financial totals must never be hallucinated.

### `qa_node` (Claude + FAISS retrieval)
- Retrieves top-`k=6` chunks for the question.
- Composes a prompt that explicitly instructs the model to treat
  `[REDACTED_*]` and `••••NNNN` as opaque identifiers.
- Wraps the call in PII + ContentSafety middleware.
- Surfaces source citations (chunk index + 240-char snippet) to the UI.

## `app/graph.py`

Two compiled graphs:

```python
EXTRACTION_GRAPH = build_extraction_graph()  # extract → categorize → calculate → END
QA_GRAPH         = build_qa_graph()          # qa → END
```

They are compiled once at server startup (`server.py`) so request latency
isn't paying graph-compilation cost.

## `server.py`

FastAPI app with one router prefixed `/api`. Notable details:

- `load_dotenv` runs **before** importing `app.*` so `EMERGENT_LLM_KEY` is
  visible to `llm_clients.py`.
- Mongo collections used:
    - `statements` — one document per uploaded PDF (transactions + summary).
    - `status_checks` — kept for backward compatibility with the platform
      smoke test.
- `model_config = ConfigDict(extra="ignore")` on every Pydantic response so
  Mongo's `_id` never leaks out.

## Categorization & interest formulas

The current categorizer uses **keyword-aware Claude classification** over a
fixed taxonomy (`Income, Groceries, Dining, Transport, Fuel, Utilities,
Rent/Mortgage, Insurance, Healthcare, Entertainment, Shopping, Travel,
Subscriptions, Transfers, Fees/Interest, Cash, Other`).

The current build does **not** apply an interest-calculation formula —
interest is reported verbatim from the statement. Two future formulas are
sketched in `nodes.py` if you want to add them:

```python
# Simple interest
I = P * r * t                   # P=principal, r=annual rate, t=years

# Daily-balance compound
A = P * (1 + r/365) ** (n_days)
```

> Open question to the product owner: which categorization rule wins when
> a transaction matches more than one category (e.g., "AMAZON PRIME" → both
> *Subscriptions* and *Shopping*)? Today we let Claude pick; flip
> `_CATEGORIZE_PROMPT_TEMPLATE` to enforce a deterministic priority list if
> needed.
