# SETUP.md

End-to-end setup for Ledger Sentinel.

## 1. System requirements

| Item            | Version            | Notes                                  |
|-----------------|--------------------|----------------------------------------|
| Python          | **3.11+**          | tested on 3.11                         |
| Node.js         | 18+                | for the React frontend                 |
| Yarn            | 1.22+              | `yarn` is required (not npm)           |
| MongoDB         | 6+                 | local on `mongodb://localhost:27017`   |
| Disk            | ≥ 2 GB             | embeddings model ~80 MB, PDFs vary     |

## 2. OS-level libraries (PDF stack)

`pymupdf4llm` ships with MuPDF wheels for most platforms. If you build from
source or use an exotic distro, install:

### Debian / Ubuntu
```bash
sudo apt-get update
sudo apt-get install -y \
    build-essential \
    libmupdf-dev mupdf-tools \
    libjpeg-dev libpng-dev libfreetype6-dev \
    libgl1
```

### macOS (Homebrew)
```bash
brew install mupdf-tools
```

### Windows
Use the binary wheels via `pip` (no extra steps).

> **`libgl1`** is needed only if you swap the embeddings model for one that
> bundles OpenCV. The default `all-MiniLM-L6-v2` does not require it.

## 3. Python dependencies

```bash
pip install -r backend/requirements.txt
pip install langgraph langchain langchain-community langchain-core \
            faiss-cpu pymupdf4llm sentence-transformers tiktoken
```

### CPU vs GPU FAISS

The default `faiss-cpu` is sufficient for any single-tenant use of this
tool. If you ingest tens of thousands of statements concurrently, switch
to GPU:

```bash
pip uninstall -y faiss-cpu
pip install faiss-gpu
```

…and ensure CUDA 11.8+ is on `LD_LIBRARY_PATH`.

## 4. Environment variables

Copy `backend/.env.example` to `backend/.env` and fill in:

```dotenv
MONGO_URL="mongodb://localhost:27017"
DB_NAME="ledger_sentinel"
CORS_ORIGINS="*"

# Emergent Universal Key — unlocks Gemini + Claude in one credential.
EMERGENT_LLM_KEY="sk-emergent-XXXXXXXXXXXXXXXX"

# Local file system layout.
LEDGER_DATA_DIR="/app/backend/data"

# Local embeddings model (no remote calls).
EMBEDDING_MODEL="sentence-transformers/all-MiniLM-L6-v2"
```

> **Where do I get `EMERGENT_LLM_KEY`?** In Emergent: *Profile → Universal
> Key → Add Balance*. The same key routes to OpenAI, Anthropic, and Google
> models behind the scenes.

## 5. Running locally

```bash
# Backend (supervisor-managed in Emergent containers)
sudo supervisorctl restart backend

# Frontend
cd frontend
yarn install
yarn start    # http://localhost:3000
```

Healthcheck:
```bash
curl -s http://localhost:8001/api/finance/health
# {"ok":true,"data_dir":"/app/backend/data","has_emergent_key":true}
```

## 6. Smoke test

```bash
# 1) Upload a statement
curl -s -X POST http://localhost:8001/api/finance/upload \
     -F "file=@samples/statement.pdf" | jq .

# 2) Ask a question
DOC=$(curl -s http://localhost:8001/api/finance/documents | jq -r '.[0].doc_id')
curl -s -X POST http://localhost:8001/api/finance/query \
     -H "Content-Type: application/json" \
     -d "{\"doc_id\":\"$DOC\",\"question\":\"How much was spent on dining?\"}" | jq .
```

## 7. Common issues

| Symptom                                      | Fix                                                                 |
|----------------------------------------------|---------------------------------------------------------------------|
| `RuntimeError: EMERGENT_LLM_KEY missing`     | populate `backend/.env`, then `sudo supervisorctl restart backend`  |
| `Could not load FAISS index`                 | the `LEDGER_DATA_DIR` was wiped — re-upload the PDF                 |
| Upload returns 500 on a scanned PDF          | scanned-only PDFs need OCR; install `tesseract-ocr` and switch the ingest stage to `pymupdf4llm.to_markdown(..., pages=..., write_images=False)` with OCR=True |
| Embeddings download stalls                   | the first run pulls ~80 MB; pre-cache with `python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('sentence-transformers/all-MiniLM-L6-v2')"` |
