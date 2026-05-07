"""
Thin async wrappers around the Emergent Universal LLM key.

We expose two *single-purpose* clients:
    - ``vision_extract`` : Gemini 3.1 Pro for PDF -> structured transactions
    - ``reason_chat``    : Claude Sonnet 4.5 for grounded financial Q&A

Both functions are intentionally model-aware and do NOT leak the API key
into logs.  Refactor note (see README): swapping these out for a vanilla
LangChain `ChatAnthropic` / `ChatGoogleGenerativeAI` is mechanical.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Optional

from emergentintegrations.llm.chat import (
    FileContentWithMimeType,
    LlmChat,
    UserMessage,
)


# -- model registry ----------------------------------------------------------
GEMINI_VISION_MODEL = "gemini-3.1-pro-preview"
CLAUDE_REASONING_MODEL = "claude-sonnet-4-5-20250929"


def _key() -> str:
    key = os.environ.get("EMERGENT_LLM_KEY")
    if not key:
        raise RuntimeError(
            "EMERGENT_LLM_KEY missing. Add it to backend/.env "
            "(see .env.example)."
        )
    return key


# ---------------------------------------------------------------------------
# Gemini : multimodal PDF extractor
# ---------------------------------------------------------------------------
async def vision_extract(
    pdf_path: str,
    instruction: str,
    *,
    session_id: Optional[str] = None,
) -> str:
    """Run Gemini-3.1-Pro over a single PDF file and return raw text output."""
    chat = LlmChat(
        api_key=_key(),
        session_id=session_id or f"vision-{uuid.uuid4().hex[:8]}",
        system_message=(
            "You are a precise financial document parser. Extract data "
            "verbatim. Never invent values. Only output what the document "
            "literally contains."
        ),
    ).with_model("gemini", GEMINI_VISION_MODEL)

    pdf = FileContentWithMimeType(
        file_path=str(Path(pdf_path).resolve()),
        mime_type="application/pdf",
    )
    msg = UserMessage(text=instruction, file_contents=[pdf])
    return await chat.send_message(msg)


# ---------------------------------------------------------------------------
# Claude : grounded text reasoning
# ---------------------------------------------------------------------------
async def reason_chat(
    prompt: str,
    *,
    system: str = "You are Ledger Sentinel, a careful financial analyst. "
                  "Answer only from the provided context. If the answer "
                  "isn't grounded in the context, say so plainly.",
    session_id: Optional[str] = None,
) -> str:
    chat = LlmChat(
        api_key=_key(),
        session_id=session_id or f"reason-{uuid.uuid4().hex[:8]}",
        system_message=system,
    ).with_model("anthropic", CLAUDE_REASONING_MODEL)
    return await chat.send_message(UserMessage(text=prompt))
