"""
Specialized agents (LangGraph nodes).

Each node is a small async function that takes the shared ``GraphState`` and
returns a partial state update.  Nodes are deliberately single-purpose:

    extract_node     -> Gemini multimodal: PDF -> JSON transactions
    categorize_node  -> Claude: assigns a category to each transaction
    calculate_node   -> Pure-Python: deterministic totals (no LLM rounding)
    qa_node          -> Claude: grounded Q&A over FAISS-retrieved chunks

The Q&A node is the only one that takes free-form user input, so it's wrapped
with the PII + ContentSafety middleware (see graph.py).
"""

from __future__ import annotations

import json
import re
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, TypedDict

from . import ingest, llm_clients
from .middleware import ContentSafetyMiddleware, PIIMiddleware


# ---------------------------------------------------------------------------
# Shared state
# ---------------------------------------------------------------------------
class GraphState(TypedDict, total=False):
    doc_id: str
    pdf_path: str
    markdown: str
    transactions: List[Dict[str, Any]]
    summary: Dict[str, Any]
    question: str
    answer: str
    sources: List[Dict[str, Any]]
    error: str
    safety_blocked: bool


# ---------------------------------------------------------------------------
# 1. EXTRACT  (Gemini, vision)
# ---------------------------------------------------------------------------
_EXTRACT_INSTRUCTION = """Read this bank/credit-card statement carefully and extract EVERY transaction.

The statement is in INR (Indian Rupees). Amounts may be prefixed with ₹, Rs.,
Rs, or INR, and may use Indian-style thousands separators like "1,23,456.78".

Return ONLY a strict JSON array, no prose, no code fences. Each item must be:
{
  "date": "YYYY-MM-DD",
  "description": "merchant or memo as printed",
  "amount": "-123.45",            // INR; negative for debits/withdrawals, positive for credits/deposits
  "balance": "1234.56" | null,    // running balance in INR if shown
  "raw": "original line text"
}

Rules:
- Numbers MUST be plain decimals; strip currency symbols (₹, Rs., INR) and ALL thousands separators.
- If the year is omitted, infer from the statement period header.
- Skip headers, footers, page numbers, summaries.
"""


def _strip_code_fences(s: str) -> str:
    s = s.strip()
    s = re.sub(r"^```(?:json)?\s*", "", s)
    s = re.sub(r"\s*```$", "", s)
    return s.strip()


async def extract_node(state: GraphState) -> Dict[str, Any]:
    """Calls Gemini-3.1-Pro on the original PDF (so the model can see layout
    even when the markdown converter loses table structure)."""
    pdf_path = state["pdf_path"]
    raw = await llm_clients.vision_extract(pdf_path, _EXTRACT_INSTRUCTION)
    try:
        data = json.loads(_strip_code_fences(raw))
        if not isinstance(data, list):
            raise ValueError("not a list")
    except (json.JSONDecodeError, ValueError) as exc:
        return {"error": f"extract_node: failed to parse JSON ({exc})",
                "transactions": []}

    # Mask PII inside the descriptions before storing - extraction is the
    # earliest point we touch model output, so we re-apply the redaction
    # rules in case Gemini surfaced a previously-buried account number.
    pii = PIIMiddleware()
    cleaned: List[Dict[str, Any]] = []
    for tx in data:
        desc = str(tx.get("description", ""))
        masked_desc, _ = pii.mask(desc)
        cleaned.append({
            "date": str(tx.get("date", "")).strip(),
            "description": masked_desc,
            "amount": str(tx.get("amount", "0")).strip(),
            "balance": (None if tx.get("balance") in (None, "", "null")
                        else str(tx["balance"]).strip()),
            "raw": str(tx.get("raw", ""))[:240],
        })
    return {"transactions": cleaned}


# ---------------------------------------------------------------------------
# 2. CATEGORIZE  (Claude, deterministic JSON)
# ---------------------------------------------------------------------------
_CATEGORIES = [
    "Income", "Groceries", "Dining", "Transport", "Fuel", "Utilities",
    "Rent/Mortgage", "Insurance", "Healthcare", "Entertainment",
    "Shopping", "Travel", "Subscriptions", "Transfers", "Fees/Interest",
    "Cash", "Other",
]

_CATEGORIZE_PROMPT_TEMPLATE = """Categorize each transaction. Pick exactly one
category from this list: {cats}.

Return a JSON array of category strings, in the SAME ORDER as the input.
No prose, no code fences, just the array.

Transactions:
{txs}
"""


async def categorize_node(state: GraphState) -> Dict[str, Any]:
    txs = state.get("transactions") or []
    if not txs:
        return {}

    # Build a compact prompt — descriptions are already PII-masked.
    items = [
        f"{i+1}. {t['date']} | {t['description'][:80]} | {t['amount']}"
        for i, t in enumerate(txs)
    ]
    prompt = _CATEGORIZE_PROMPT_TEMPLATE.format(
        cats=", ".join(_CATEGORIES), txs="\n".join(items),
    )
    raw = await llm_clients.reason_chat(
        prompt,
        system=("You are a transaction categorizer. Output strictly a JSON "
                "array of category strings. No commentary."),
    )
    try:
        cats = json.loads(_strip_code_fences(raw))
        if not isinstance(cats, list):
            raise ValueError("not a list")
    except (json.JSONDecodeError, ValueError):
        # Fallback: mark everything Other.
        cats = ["Other"] * len(txs)

    # Pad/trim to length and clamp to allowed categories.
    if len(cats) < len(txs):
        cats = list(cats) + ["Other"] * (len(txs) - len(cats))
    cats = cats[: len(txs)]
    allowed = set(_CATEGORIES)
    cats = [c if c in allowed else "Other" for c in cats]

    enriched = [{**t, "category": c} for t, c in zip(txs, cats)]
    return {"transactions": enriched}


# ---------------------------------------------------------------------------
# 3. CALCULATE  (pure Python — never trust the LLM with arithmetic)
# ---------------------------------------------------------------------------
def _to_decimal(s: Any) -> Decimal:
    try:
        cleaned = (
            str(s)
            .replace(",", "")
            .replace("\u20b9", "")  # ₹
            .replace("$", "")
            .replace("Rs.", "")
            .replace("Rs", "")
            .replace("INR", "")
        )
        # Drop any remaining whitespace (e.g. "- 500" after stripping symbols).
        cleaned = "".join(cleaned.split())
        return Decimal(cleaned) if cleaned else Decimal("0")
    except (InvalidOperation, ValueError, TypeError):
        return Decimal("0")


async def calculate_node(state: GraphState) -> Dict[str, Any]:
    txs = state.get("transactions") or []
    if not txs:
        return {"summary": {
            "income": "0.00", "expense": "0.00", "net": "0.00",
            "count": 0, "by_category": {}, "top_category": None,
        }}

    income = Decimal("0")
    expense = Decimal("0")
    by_cat: Dict[str, Decimal] = {}
    for t in txs:
        amt = _to_decimal(t.get("amount", 0))
        cat = t.get("category", "Other")
        if amt >= 0:
            income += amt
        else:
            expense += amt  # negative
        by_cat[cat] = by_cat.get(cat, Decimal("0")) + amt

    # Top expense category (most-negative).
    expense_cats = {k: v for k, v in by_cat.items() if v < 0}
    top_cat = (
        min(expense_cats.items(), key=lambda kv: kv[1])[0]
        if expense_cats else None
    )

    summary = {
        "income": f"{income:.2f}",
        "expense": f"{expense:.2f}",
        "net": f"{income + expense:.2f}",
        "count": len(txs),
        "by_category": {k: f"{v:.2f}" for k, v in by_cat.items()},
        "top_category": top_cat,
    }
    return {"summary": summary}


# ---------------------------------------------------------------------------
# 4. Q&A  (Claude, grounded by FAISS retrieval)
# ---------------------------------------------------------------------------
_QA_TEMPLATE = """You are answering a question about a single bank statement.
The statement excerpt below is PII-masked: tokens like [REDACTED_CC_1] or
••••1234 represent real values you must not try to guess. Treat them as opaque
identifiers.

If the answer cannot be derived from the excerpt, reply exactly:
"I don't have enough information in this statement to answer that."

--- STATEMENT EXCERPT START ---
{context}
--- STATEMENT EXCERPT END ---

Question: {question}

Answer concisely. Quote dates and amounts verbatim from the excerpt.
"""


async def qa_node(state: GraphState) -> Dict[str, Any]:
    doc_id = state["doc_id"]
    question = state.get("question", "").strip()
    if not question:
        return {"answer": "", "sources": []}

    docs = ingest.retrieve(doc_id, question, k=6)
    context = "\n\n---\n\n".join(d.page_content for d in docs)
    prompt = _QA_TEMPLATE.format(context=context, question=question)

    # PII middleware re-mask (defense in depth) + safety check on output.
    pii = PIIMiddleware()
    safety = ContentSafetyMiddleware()
    masked_prompt, _ = pii.mask(prompt)
    raw = await llm_clients.reason_chat(masked_prompt)
    verdict = safety.check(raw)
    if verdict.blocked:
        return {
            "answer": safety.safe_fallback(verdict.reason),
            "safety_blocked": True,
            "sources": [],
        }
    sources = [
        {"chunk": d.metadata.get("chunk"), "snippet": d.page_content[:240]}
        for d in docs
    ]
    return {"answer": raw, "sources": sources, "safety_blocked": False}
