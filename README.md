# CT-200 Document Intelligence & QA Traceability

Backend service built for the Tri9T AI Engineering Internship assignment. It ingests technical and regulatory manuals for the CardioTrack CT-200 blood pressure monitor directly from raw PDF files, extracts a structured hierarchy (sections, subsections, and Markdown tables), tracks document revisions, manages version-pinned selections, and generates source-grounded QA test cases with automated staleness detection.

**Author:** Vamsi Inampudi ([@Vamsi112-max](https://github.com/Vamsi112-max))  
**Tech Stack:** Python 3.10+, FastAPI, SQLAlchemy 2.0, SQLite, Pydantic, PyMuPDF

---

## Local Links

Once the server is running locally:
* **Interactive Dashboard:** [http://127.0.0.1:8000/](http://127.0.0.1:8000/)
* **Swagger API Docs:** [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)
* **ReDoc Docs:** [http://127.0.0.1:8000/redoc](http://127.0.0.1:8000/redoc)
* **Health Check:** [http://127.0.0.1:8000/health](http://127.0.0.1:8000/health)

---

## What This Project Does

1. **Direct PDF Parsing without Markdown Intermediate:**
   - Parses `sample_data_ct200_manual.pdf` directly using PyMuPDF.
   - Solves the Page 3 clinical list collision: differentiates real chapter headings from numbered clinical bullet points (`1. Normal...`, `2. Elevated...` under Section 3.3) by inspecting font size and boldness flags.
   - Converts the General Specifications table (Page 2) and Error Codes table (Page 4) into clean GitHub-style Markdown tables attached to their parent sections.
   - Cleans up Unicode ligatures (`\ufb01` -> `fi`, `\ufb02` -> `fl`) and standardizes hyphens to prevent Windows terminal crashes and broken search queries.
   - Handles skipped hierarchy levels (`2.1.1.1` cleanly linking to parent `2.1`).
   - Disambiguates duplicate headings (`1.1` and `1.1#2`) without overwriting records.

2. **Document Versioning & Canonical Diffing:**
   - Ingests new revisions (e.g. `v2`) as new versions without destroying `v1`.
   - Computes canonical whitespace-normalized SHA-256 hashes so that layout/pagination shifts (such as Section 5.3 pushing Section 6 onto new pages) do not trigger false diff changes.
   - Accurately highlights added sections (`5.3 Data Export`), modified sections (`2.1.1.1`, `3.2`, `4.2.table.1`, `4.3`), and unchanged sections.

3. **Version-Pinned Selection API:**
   - `POST /selections` allows users to save named selections referencing specific node+version pairs.
   - When fetched later, selections resolve the exact text from the version they were pinned to, even after new manual revisions are ingested.

4. **Source-Grounded QA Test Generation:**
   - Generates structured test cases with title, ordered execution steps, concrete expected results referencing exact numerical limits or error codes, and source quotes.
   - Validated against strict Pydantic schemas with an automatic 3-attempt retry loop on malformed LLM responses.
   - Includes a deterministic offline fallback generator that extracts actual numbers, error codes, and pressure thresholds, so the entire pipeline works out-of-the-box without needing an external API key.
   - **Duplicate Selection Policy:** Returns cached test cases idempotently when `regenerate=False` (saving token costs and keeping IDs stable), with explicit re-generation available when `regenerate=True`.

5. **Automated Staleness & Impact Detection:**
   - When a new document version is uploaded, older test cases are evaluated against the newest revision.
   - Flags modified sections as `stale = true` with reason `"source_modified"`, and deleted sections as `"source_deleted"`.
   - Supports querying test cases by `selection_id`, `node_key`, `node_id`, `version_id`, or `stale=true`.

---

## Setup & Running Locally

### 1. Requirements
* Python 3.10 or higher
* pip

### 2. Setup
```bash
# Clone the repository
git clone https://github.com/Vamsi112-max/ct200-document-intelligence.git
cd ct200-document-intelligence

# (Optional) Create a virtual environment
python -m venv .venv
# On Windows:
.venv\Scripts\activate
# On Linux/macOS:
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### 3. (Optional) External LLM Configuration
The project runs offline by default using its built-in rule synthesizer. If you want to use a live LLM:
```bash
# Windows PowerShell:
$env:OPENAI_API_KEY = "your-api-key"

# Linux / macOS:
export OPENAI_API_KEY="your-api-key"
```

### 4. Start the API Server
```bash
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```
Open [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs) in your browser.

---

## Testing the v1 → v2 Re-Ingestion & Staleness Flow

Here is the exact curl sequence to verify the end-to-end versioning and staleness flow:

### 1. Ingest Version 1
```bash
curl -X POST "http://127.0.0.1:8000/documents/ingest?document_key=ct200" \
  -F "file=@sample_data_ct200_manual.pdf"
```

### 2. Browse Top-Level Sections
```bash
curl -X GET "http://127.0.0.1:8000/sections/top-level?document_key=ct200"
```

### 3. Fetch a Specific Node With Its Immediate Children
```bash
# Fetch Chapter 1 to see its full text and children array (1.1, 1.2)
curl -X GET "http://127.0.0.1:8000/nodes/1"
```

### 4. Create a Version-Pinned Selection
```bash
curl -X POST "http://127.0.0.1:8000/selections" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Inflation & Safety Selection",
    "version_id": 1,
    "node_keys": ["3.2", "4.1"]
  }'
```

### 5. Generate QA Test Cases from the Selection
```bash
curl -X POST "http://127.0.0.1:8000/test-cases/generate" \
  -H "Content-Type: application/json" \
  -d '{
    "selection_id": 1,
    "count": 2
  }'
```

### 6. Ingest Version 2 (Triggers Re-Versioning & Staleness Evaluation)
```bash
curl -X POST "http://127.0.0.1:8000/documents/ingest?document_key=ct200" \
  -F "file=@sample_data_ct200_manual_v2.pdf"
```

### 7. View the Version Diff
```bash
curl -X GET "http://127.0.0.1:8000/documents/1/diff/2"
```
*Shows `5.3 Data Export` added, and `3.2`, `2.1.1.1`, `4.2.table.1`, and `4.3` modified.*

### 8. Inspect Node-Level Changes
```bash
# Check if node 3.2 has changed in the latest version
curl -X GET "http://127.0.0.1:8000/nodes/13/changes"
```
*Returns `has_changed: true` and `change_type: "modified"` with hash diff summary.*

### 9. Query Stale Test Cases
```bash
# List all test cases flagged as stale
curl -X GET "http://127.0.0.1:8000/test-cases?stale=true"

# Inspect detailed staleness diagnostics for test case #1
curl -X GET "http://127.0.0.1:8000/test-cases/1/staleness"
```

### 10. Fetch Test Cases by Selection ID
```bash
curl -X GET "http://127.0.0.1:8000/test-cases?selection_id=1"
```

---

## Running the Automated Test Suite

Run pytest from the project directory:
```bash
python -m pytest -v
```

All 12 unit, irregularity, and integration tests pass:
- `tests/test_parser.py`: Heading regexes, Unicode ligature decomposition, whitespace-invariant canonical hashing, and full extraction on the real CT-200 PDF.
- `tests/test_irregular_pdfs.py`: 5 edge-case PDFs:
  1. Empty PDF with no text.
  2. Skipped/jumping numbering hierarchy (`1.0` -> `1.5.3.1`).
  3. Unnumbered headings with embedded numbered list items.
  4. Ragged tables with missing columns and `None` cells.
  5. Duplicate heading numbering and identical titles across parent chapters.
- `tests/test_api_and_traceability.py`: Full end-to-end integration test (Ingest v1 -> browse tree -> top-level sections -> node children -> create selection -> generate cases -> duplicate policy test -> ingest v2 -> version diff -> node change check -> staleness detection -> filtered retrieval).
