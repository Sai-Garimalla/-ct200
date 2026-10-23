# CT-200 Document Intelligence & QA Traceability Hub

A robust backend service for ingesting technical and regulatory device manuals (CardioTrack CT-200 Blood Pressure Monitor), reconstructing their hierarchical tree and tables directly from raw PDF files, managing version-pinned selections, and generating source-grounded QA test cases with automated staleness detection.

Built with **Python 3.10+**, **FastAPI**, **SQLAlchemy 2.0**, **SQLite**, **Pydantic**, and **PyMuPDF**.

---

## Interactive Local Endpoints

When the server is running locally:
* **Interactive UI Hub:** [http://127.0.0.1:8000/](http://127.0.0.1:8000/)
* **Swagger API Documentation:** [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)
* **ReDoc Specification:** [http://127.0.0.1:8000/redoc](http://127.0.0.1:8000/redoc)
* **Health Check:** [http://127.0.0.1:8000/health](http://127.0.0.1:8000/health)

---

## Core Capabilities

1. **Direct PDF Ingestion & Hierarchy Reconstruction**
   - Extracts titles, chapters, subheadings, paragraphs, and markdown tables directly from raw PDF bytes.
   - Solves the Page 3 clinical list collision: differentiates true section headers from numbered list items (`1. Normal...`, `2. Elevated...`).
   - Handles skipped hierarchical levels (e.g. `2.1.1.1` cleanly linking to parent `2.1`).
   - Disambiguates duplicate headings without overwriting data.
   - Normalizes typographic Unicode ligatures (`fi`, `fl`, hyphens, non-breaking spaces).
2. **Document Versioning & Canonical Diffing**
   - Ingests revisions (e.g. v1 and v2) as discrete versions without destroying prior history.
   - Computes canonical whitespace-invariant SHA-256 hashes so layout/pagination shifts do not trigger false diffs.
   - Identifies added sections (e.g. `5.3 Data Export`), modified sections (`2.1.1.1`, `3.2`, `4.2.table.1`, `4.3`), and unchanged sections.
3. **Version-Pinned Selection API**
   - Supports creating named **Selections** (`POST /selections`) referencing specific node+version pairs.
   - Future document updates never alter the text a selection was originally resolved against.
4. **Source-Grounded QA Test Generation**
   - Generates structured test cases with title, ordered sequential steps, expected results citing numerical thresholds, and exact source quotes.
   - Validated against strict Pydantic schemas with automatic 3-attempt retry loop on malformed LLM responses.
   - Deterministic offline fallback generates grounded test cases when running without API keys.
   - **Duplicate Selection Policy:** Returns existing test cases idempotently when `regenerate=False`, preventing duplicate database records and saving API spend.
5. **Automated Staleness & Impact Detection**
   - Tracks whether previously generated test cases still reflect the latest document state.
   - Flags modified or deleted source sections with explicit diagnostic reasons (`source_modified` or `source_deleted`).
   - Retrieval API allows filtering test cases by `version_id`, `selection_id`, `node_key`, or `stale=true`.

---

## Setup & Running Locally

### 1. Requirements
* Python 3.10+
* pip

### 2. Installation
```bash
# Enter project directory
cd ct200-document-intelligence

# (Optional) Create and activate a virtual environment
python -m venv .venv
# On Windows:
.venv\Scripts\activate
# On Linux / macOS:
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### 3. Environment Variables (Optional)
The system runs offline out-of-the-box using its deterministic local synthesizer. To use an external LLM:
```bash
# Windows PowerShell:
$env:OPENAI_API_KEY = "your-api-key"

# Linux / macOS:
export OPENAI_API_KEY="your-api-key"
```

### 4. Run Server
```bash
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```
Open [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs) in your browser.

---

## Step-by-Step Walkthrough: v1 → v2 Re-Ingestion & Staleness Flow

### Step 1: Ingest Version 1
```bash
curl -X POST "http://127.0.0.1:8000/documents/ingest?document_key=ct200" \
  -F "file=@sample_data_ct200_manual.pdf"
```

### Step 2: Browse Top-Level Sections
```bash
curl -X GET "http://127.0.0.1:8000/sections/top-level?document_key=ct200"
```

### Step 3: Inspect a Specific Node & Its Children
```bash
# Query node #1 (Chapter 1) to view full text and its immediate children
curl -X GET "http://127.0.0.1:8000/nodes/1"
```

### Step 4: Create a Version-Pinned Selection
```bash
curl -X POST "http://127.0.0.1:8000/selections" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Inflation Dynamics & Safety",
    "version_id": 1,
    "node_keys": ["3.2", "4.1"]
  }'
```
*Response returns selection `id: 1` with resolved exact source text.*

### Step 5: Generate QA Test Cases from the Selection
```bash
curl -X POST "http://127.0.0.1:8000/test-cases/generate" \
  -H "Content-Type: application/json" \
  -d '{
    "selection_id": 1,
    "count": 2
  }'
```

### Step 6: Ingest Version 2 (Triggering Re-Versioning)
```bash
curl -X POST "http://127.0.0.1:8000/documents/ingest?document_key=ct200" \
  -F "file=@sample_data_ct200_manual_v2.pdf"
```

### Step 7: View the Version Diff
```bash
curl -X GET "http://127.0.0.1:8000/documents/1/diff/2"
```
*Shows `5.3` added, and `3.2`, `2.1.1.1`, `4.2.table.1`, and `4.3` modified.*

### Step 8: Check Node-Level Version Changes
```bash
# Check if node 3.2 has changed in the latest version
curl -X GET "http://127.0.0.1:8000/nodes/13/changes"
```
*Returns `has_changed: true`, `change_type: "modified"` with hash diff summary.*

### Step 9: Query Stale Test Cases
```bash
# List all test cases flagged as stale
curl -X GET "http://127.0.0.1:8000/test-cases?stale=true"

# Query detailed diagnostics for test case #1
curl -X GET "http://127.0.0.1:8000/test-cases/1/staleness"
```

### Step 10: Retrieve Test Cases by Selection ID
```bash
curl -X GET "http://127.0.0.1:8000/test-cases?selection_id=1"
```

---

## Automated Test Suite

Run all unit, irregular layout, and end-to-end integration tests:
```bash
python -m pytest -v
```

### Test Coverage Highlights
* `tests/test_parser.py`: Heading regexes, Unicode ligature decomposition, whitespace-invariant canonical hashing, and full extraction on `sample_data_ct200_manual.pdf`.
* `tests/test_irregular_pdfs.py`: 5 edge cases tested:
  1. Empty / zero-text PDF.
  2. Skipped/jumping numbering hierarchy (`1.0` -> `1.5.3.1`).
  3. Unnumbered headings with embedded numbered list items.
  4. Ragged tables with missing columns and `None` cells.
  5. Duplicate heading numbering and identical titles across parent chapters.
* `tests/test_api_and_traceability.py`: Full end-to-end flow (Ingest v1 -> browse tree -> top-level sections -> node children -> create selection -> generate cases -> duplicate policy test -> ingest v2 -> version diff -> node change check -> staleness detection -> filtered retrieval).
