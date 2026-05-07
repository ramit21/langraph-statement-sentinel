"""
PDF -> Markdown -> chunks -> FAISS vector store.

We use ``pymupdf4llm`` (fast, deterministic) to convert PDFs to markdown,
then ``RecursiveCharacterTextSplitter`` for chunking, and finally a local
FAISS index persisted under ``DATA_DIR/faiss/<doc_id>``.

PII masking happens *before* text is embedded so vectors never contain raw
account or card numbers.
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

import pymupdf4llm
from langchain_community.vectorstores import FAISS
from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter
from sentence_transformers import SentenceTransformer

from .middleware import PIIMiddleware


# ---------------------------------------------------------------------------
# Embeddings (local, free, deterministic) - keeps embeddings on-device.
# ---------------------------------------------------------------------------
_EMBED_MODEL_NAME = os.environ.get(
    "EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2"
)
_embed_model: Optional[SentenceTransformer] = None


def _embedder() -> SentenceTransformer:
    global _embed_model
    if _embed_model is None:
        _embed_model = SentenceTransformer(_EMBED_MODEL_NAME)
    return _embed_model


class _LocalEmbeddings(Embeddings):
    """LangChain-compatible embeddings adapter for sentence-transformers."""

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        return _embedder().encode(
            texts, show_progress_bar=False, normalize_embeddings=True
        ).tolist()

    def embed_query(self, text: str) -> List[float]:
        return _embedder().encode(
            [text], show_progress_bar=False, normalize_embeddings=True
        )[0].tolist()

    # Some recent FAISS code paths call the embeddings object directly.
    def __call__(self, text):
        if isinstance(text, str):
            return self.embed_query(text)
        return self.embed_documents(list(text))


# ---------------------------------------------------------------------------
# Storage layout
# ---------------------------------------------------------------------------
DATA_DIR = Path(os.environ.get("LEDGER_DATA_DIR", "/app/backend/data"))
PDF_DIR = DATA_DIR / "pdfs"
INDEX_DIR = DATA_DIR / "faiss"
META_DIR = DATA_DIR / "meta"
for d in (PDF_DIR, INDEX_DIR, META_DIR):
    d.mkdir(parents=True, exist_ok=True)


@dataclass
class IngestResult:
    doc_id: str
    pdf_path: str
    markdown: str
    chunk_count: int
    pii_hits: int


def _save_pdf(file_bytes: bytes, original_name: str) -> Tuple[str, str]:
    doc_id = uuid.uuid4().hex
    safe_name = Path(original_name).name or "statement.pdf"
    target = PDF_DIR / f"{doc_id}__{safe_name}"
    target.write_bytes(file_bytes)
    return doc_id, str(target)


def ingest_pdf(file_bytes: bytes, original_name: str) -> IngestResult:
    """Persist the PDF, convert to markdown, mask PII, embed, write FAISS."""
    pii = PIIMiddleware()
    doc_id, pdf_path = _save_pdf(file_bytes, original_name)

    # 1. PDF -> markdown
    md_text = pymupdf4llm.to_markdown(pdf_path)

    # 2. Mask PII before embedding
    masked_md, mm = pii.mask(md_text)
    pii_hits = len(mm.forward)

    # 3. Chunk
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=800,
        chunk_overlap=120,
        separators=["\n## ", "\n### ", "\n\n", "\n", " "],
    )
    chunks = splitter.split_text(masked_md)
    docs = [
        Document(
            page_content=chunk,
            metadata={"doc_id": doc_id, "chunk": i, "source": original_name},
        )
        for i, chunk in enumerate(chunks)
    ]

    # 4. Embed + persist FAISS
    store = FAISS.from_documents(docs, _LocalEmbeddings())
    store.save_local(str(INDEX_DIR / doc_id))

    # 5. Persist raw markdown (already PII-masked) for downstream agents.
    (META_DIR / f"{doc_id}.json").write_text(
        json.dumps(
            {
                "doc_id": doc_id,
                "original_name": original_name,
                "pdf_path": pdf_path,
                "chunk_count": len(chunks),
                "pii_hits": pii_hits,
            },
            indent=2,
        )
    )
    (META_DIR / f"{doc_id}.md").write_text(masked_md)
    (META_DIR / f"{doc_id}.raw.md").write_text(md_text)  # local-only, not embedded

    return IngestResult(
        doc_id=doc_id,
        pdf_path=pdf_path,
        markdown=masked_md,
        chunk_count=len(chunks),
        pii_hits=pii_hits,
    )


def load_store(doc_id: str) -> FAISS:
    return FAISS.load_local(
        str(INDEX_DIR / doc_id),
        _LocalEmbeddings(),
        allow_dangerous_deserialization=True,
    )


def retrieve(doc_id: str, query: str, k: int = 6) -> List[Document]:
    store = load_store(doc_id)
    return store.similarity_search(query, k=k)


def get_pdf_path(doc_id: str) -> str:
    meta = json.loads((META_DIR / f"{doc_id}.json").read_text())
    return meta["pdf_path"]


def get_masked_markdown(doc_id: str) -> str:
    return (META_DIR / f"{doc_id}.md").read_text()
