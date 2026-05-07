"""
Middleware layer for Ledger Sentinel.

Provides two pluggable middlewares that wrap any LLM node:
    1. PIIMiddleware           - masks PII before content reaches the model.
    2. ContentSafetyMiddleware - blocks harmful / fraudulent responses on the way out.

Both middlewares are *pure* (no I/O, no logging of raw payload) which keeps
sensitive data out of stdout and supervisor logs.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Dict, List, Tuple


# ---------------------------------------------------------------------------
# PII MIDDLEWARE
# ---------------------------------------------------------------------------

# Note: anchored with word-boundaries / lookarounds to keep precision high.
# The patterns below intentionally cover the THREE fields we promised users:
#   credit-card, bank-account, SSN.
_CC_RE = re.compile(
    r"(?<!\d)(?:\d[ -]?){13,19}(?!\d)"
)
_SSN_RE = re.compile(r"(?<!\d)\d{3}-\d{2}-\d{4}(?!\d)")
# Bank-account: 8-17 contiguous digits NOT preceded/followed by a digit and NOT a date.
_ACCT_RE = re.compile(r"(?<!\d)\d{8,17}(?!\d)")


def _luhn_ok(num: str) -> bool:
    digits = [int(c) for c in num if c.isdigit()]
    if not 13 <= len(digits) <= 19:
        return False
    checksum = 0
    for i, d in enumerate(reversed(digits)):
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        checksum += d
    return checksum % 10 == 0


def _last4(value: str) -> str:
    digits = re.sub(r"\D", "", value)
    return digits[-4:] if len(digits) >= 4 else digits


@dataclass
class MaskMap:
    """Bidirectional map between original PII and its placeholder token."""

    forward: Dict[str, str] = field(default_factory=dict)
    reverse: Dict[str, str] = field(default_factory=dict)

    def add(self, original: str, token: str) -> None:
        self.forward[original] = token
        self.reverse[token] = original


class PIIMiddleware:
    """Detects + masks PII. Stateless across calls; per-call ``MaskMap``
    is returned so callers can optionally unmask trusted fields client-side."""

    PLACEHOLDER_FMT = "[REDACTED_{kind}_{idx}]"

    def __init__(self, mask_credit_cards: bool = True,
                 mask_bank_accounts: bool = True,
                 mask_ssn: bool = True) -> None:
        self.mask_cc = mask_credit_cards
        self.mask_bank = mask_bank_accounts
        self.mask_ssn = mask_ssn

    def mask(self, text: str) -> Tuple[str, MaskMap]:
        if not text:
            return text, MaskMap()

        mm = MaskMap()
        out = text
        counters = {"CC": 0, "ACCT": 0, "SSN": 0}

        def _replace(pattern: re.Pattern, kind: str, validator=None) -> None:
            nonlocal out
            matches = list(pattern.finditer(out))
            # Replace right-to-left so positions stay valid.
            for m in reversed(matches):
                raw = m.group(0)
                if validator and not validator(raw):
                    continue
                if raw in mm.forward:
                    token = mm.forward[raw]
                else:
                    counters[kind] += 1
                    token = self.PLACEHOLDER_FMT.format(
                        kind=kind, idx=counters[kind]
                    )
                    mm.add(raw, token)
                start, end = m.span()
                out = out[:start] + token + out[end:]

        if self.mask_ssn:
            _replace(_SSN_RE, "SSN")
        if self.mask_cc:
            _replace(_CC_RE, "CC", validator=_luhn_ok)
        if self.mask_bank:
            _replace(_ACCT_RE, "ACCT")
        return out, mm

    def display_mask(self, value: str, kind: str = "ACCT") -> str:
        """Produces a UI-safe representation: '••••1234'."""
        last = _last4(value)
        return f"••••{last}" if last else "••••"


# ---------------------------------------------------------------------------
# CONTENT-SAFETY MIDDLEWARE
# ---------------------------------------------------------------------------

# Coarse keyword guardrails. The LLM is generally well-aligned, but for a
# finance tool we add a deterministic backstop that does not rely on model
# behaviour.
_HARMFUL_KEYWORDS: List[str] = [
    "money laundering", "launder money", "tax evasion", "evade taxes",
    "hide income", "fake invoice", "shell company to avoid",
    "structure cash deposits", "smurfing",
    "kill", "hurt yourself", "self-harm",
]


@dataclass
class SafetyVerdict:
    blocked: bool
    reason: str = ""


class ContentSafetyMiddleware:
    """Inspects model output and blocks responses containing harmful or
    financially-fraudulent guidance."""

    def __init__(self, extra_keywords: List[str] | None = None) -> None:
        self.keywords = [k.lower() for k in _HARMFUL_KEYWORDS]
        if extra_keywords:
            self.keywords.extend(k.lower() for k in extra_keywords)

    def check(self, text: str) -> SafetyVerdict:
        if not text:
            return SafetyVerdict(False)
        lowered = text.lower()
        for kw in self.keywords:
            if kw in lowered:
                return SafetyVerdict(
                    True,
                    f"response blocked: matched policy term '{kw}'",
                )
        return SafetyVerdict(False)

    def safe_fallback(self, reason: str) -> str:
        return (
            "I can't help with that request. The response was withheld by the "
            "content-safety guardrail. Reason: " + reason
        )


# ---------------------------------------------------------------------------
# COMPOSITION HELPER
# ---------------------------------------------------------------------------

LLMNode = Callable[[str], Awaitable[str]]


def with_guardrails(
    node: LLMNode,
    pii: PIIMiddleware,
    safety: ContentSafetyMiddleware,
) -> LLMNode:
    """Wrap an LLM node so that:
       - prompts are PII-masked before being sent to the model
       - responses are content-safety-checked before being returned
    """

    async def _wrapped(prompt: str) -> str:
        masked, _ = pii.mask(prompt)
        raw = await node(masked)
        verdict = safety.check(raw)
        if verdict.blocked:
            return safety.safe_fallback(verdict.reason)
        return raw

    return _wrapped
