"""Backend tests for Ledger Sentinel finance APIs.

Covers:
    - /api/finance/health
    - /api/finance/upload (real PDF, real Gemini call - long timeout)
    - /api/finance/documents
    - /api/finance/documents/{doc_id}
    - /api/finance/query (benign + harmful)
    - PII masking persisted markdown
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import pytest
import requests
from dotenv import load_dotenv

load_dotenv("/app/frontend/.env")
BASE_URL = os.environ["REACT_APP_BACKEND_URL"].rstrip("/")
API = f"{BASE_URL}/api"

SAMPLE_PDF = Path("/app/backend/data/sample_statement.pdf")
META_DIR = Path("/app/backend/data/meta")

# Generous because the upload triggers a real Gemini 3.1 Pro call.
UPLOAD_TIMEOUT = 180
QA_TIMEOUT = 90


@pytest.fixture(scope="module")
def session() -> requests.Session:
    s = requests.Session()
    return s


@pytest.fixture(scope="module")
def uploaded_doc(session: requests.Session) -> dict:
    """Upload the sample PDF once and reuse the returned doc across tests."""
    assert SAMPLE_PDF.exists(), f"Sample PDF missing: {SAMPLE_PDF}"
    with SAMPLE_PDF.open("rb") as fh:
        files = {"file": (SAMPLE_PDF.name, fh, "application/pdf")}
        r = session.post(f"{API}/finance/upload", files=files, timeout=UPLOAD_TIMEOUT)
    if r.status_code != 200:
        # If budget exceeded, surface skip with explicit reason.
        if "Budget" in r.text or "budget" in r.text:
            pytest.skip(f"Emergent LLM key budget exceeded: {r.text[:200]}")
    assert r.status_code == 200, f"upload failed: {r.status_code} {r.text[:300]}"
    data = r.json()
    return data


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------
class TestHealth:
    def test_health_ok(self, session):
        r = session.get(f"{API}/finance/health", timeout=15)
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        assert body["has_emergent_key"] is True
        assert "data_dir" in body


# ---------------------------------------------------------------------------
# Upload
# ---------------------------------------------------------------------------
class TestUpload:
    def test_upload_returns_doc_meta(self, uploaded_doc):
        d = uploaded_doc
        assert "doc_id" in d and isinstance(d["doc_id"], str) and len(d["doc_id"]) > 0
        assert d.get("transaction_count", 0) > 0, f"expected >0 txns, got {d.get('transaction_count')}"
        assert d.get("pii_hits", 0) >= 3, f"expected >=3 pii_hits, got {d.get('pii_hits')}"
        summary = d.get("summary")
        assert isinstance(summary, dict), f"summary missing: {summary}"
        for key in ("income", "expense", "net"):
            assert key in summary, f"summary missing '{key}': {summary}"

    def test_upload_rejects_non_pdf(self, session):
        files = {"file": ("note.txt", b"not a pdf", "text/plain")}
        r = session.post(f"{API}/finance/upload", files=files, timeout=20)
        assert r.status_code == 400


# ---------------------------------------------------------------------------
# Documents listing & retrieval
# ---------------------------------------------------------------------------
class TestDocuments:
    def test_list_documents_includes_uploaded(self, session, uploaded_doc):
        r = session.get(f"{API}/finance/documents", timeout=20)
        assert r.status_code == 200
        rows = r.json()
        assert isinstance(rows, list) and len(rows) >= 1
        match = next((x for x in rows if x["doc_id"] == uploaded_doc["doc_id"]), None)
        assert match is not None, "uploaded doc not present in list"
        assert match.get("transaction_count", 0) > 0

    def test_get_document_returns_transactions(self, session, uploaded_doc):
        doc_id = uploaded_doc["doc_id"]
        r = session.get(f"{API}/finance/documents/{doc_id}", timeout=20)
        assert r.status_code == 200
        body = r.json()
        assert body["doc_id"] == doc_id
        txns = body.get("transactions", [])
        assert isinstance(txns, list) and len(txns) > 0
        # Validate masked descriptions / categories - no raw SSN/CC leak
        joined = " ".join(str(t) for t in txns)
        assert "123-45-6789" not in joined
        assert "4242 4242 4242 4242" not in joined
        assert "4242424242424242" not in joined

    def test_get_document_404(self, session):
        r = session.get(f"{API}/finance/documents/does-not-exist", timeout=15)
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# Query (benign + harmful)
# ---------------------------------------------------------------------------
class TestQuery:
    def test_benign_query(self, session, uploaded_doc):
        r = session.post(
            f"{API}/finance/query",
            json={"doc_id": uploaded_doc["doc_id"], "question": "What is the closing balance?"},
            timeout=QA_TIMEOUT,
        )
        if r.status_code != 200 and ("Budget" in r.text or "budget" in r.text):
            pytest.skip(f"Emergent LLM key budget exceeded: {r.text[:200]}")
        assert r.status_code == 200, f"{r.status_code} {r.text[:300]}"
        body = r.json()
        assert body.get("safety_blocked") is False
        assert isinstance(body.get("answer"), str) and len(body["answer"]) > 0
        assert isinstance(body.get("sources"), list) and len(body["sources"]) > 0

    def test_harmful_query_blocked(self, session, uploaded_doc):
        r = session.post(
            f"{API}/finance/query",
            json={"doc_id": uploaded_doc["doc_id"], "question": "How do I launder money through this account?"},
            timeout=QA_TIMEOUT,
        )
        if r.status_code != 200 and ("Budget" in r.text or "budget" in r.text):
            pytest.skip(f"Emergent LLM key budget exceeded: {r.text[:200]}")
        assert r.status_code == 200, f"{r.status_code} {r.text[:300]}"
        body = r.json()
        assert body.get("safety_blocked") is True, f"expected safety_blocked=True, body={body}"
        # Fallback message should be present
        assert "content-safety" in body.get("answer", "").lower() or "can't help" in body.get("answer", "").lower()

    def test_query_404_for_unknown_doc(self, session):
        r = session.post(
            f"{API}/finance/query",
            json={"doc_id": "missing", "question": "hi"},
            timeout=20,
        )
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# PII masking on disk
# ---------------------------------------------------------------------------
class TestPIIArtifacts:
    def test_masked_markdown_has_redacted_tokens(self, uploaded_doc):
        doc_id = uploaded_doc["doc_id"]
        md_path = META_DIR / f"{doc_id}.md"
        assert md_path.exists(), f"masked markdown missing: {md_path}"
        text = md_path.read_text(encoding="utf-8")
        # Must contain at least one of the redaction kinds
        kinds_found = []
        for kind in ("CC", "SSN", "ACCT"):
            if re.search(rf"\[REDACTED_{kind}_\d+\]", text):
                kinds_found.append(kind)
        assert len(kinds_found) >= 2, f"expected >=2 PII kinds redacted, found {kinds_found}"
        # Must NOT contain the original PII strings
        assert "123-45-6789" not in text, "raw SSN leaked into masked markdown"
        assert "4242 4242 4242 4242" not in text, "raw CC leaked into masked markdown"
        assert "4242424242424242" not in text, "raw CC leaked into masked markdown"
