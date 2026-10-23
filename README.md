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
   - Parses `sample_data_ct200_manual.pdf` and `v2` directly using PyMuPDF.
   - Differentiates real chapter headings from numbered clinical bullet points (`1. Normal...`, `2. Elevated...` under Section 3.3) by inspecting font size and boldness flags.
   - Converts the General Specifications table (Page 2) and Error Codes table (Page 4) into clean GitHub-style Markdown tables attached to their parent sections.
   - Normalizes Unicode ligatures (`\ufb01` -> `fi`, `\ufb02` -> `fl`) and standardizes hyphens to prevent terminal encoding crashes and broken searches.
   - Handles skipped hierarchy levels (`2.1.1.1` cleanly linking to parent `2.1`).
   - Disambiguates duplicate headings (`1.1` and `1.1#2`) without overwriting records.
   - Preserves canonical heading punctuation (e.g. `1. Device Overview` retains the trailing dot, while `1.1 Intended Use` retains its dotless sub-header style).
   - Dynamic font size thresholding: adapts heading detection to the document's median body text font size rather than hardcoding arbitrary point values.
   - OCR fallback hook: attempts PyMuPDF OCR fallback for pages with little or no extractable text.

2. **Parent-Child Content Roll-Up & Subtree Hashing:**
   - Technical manuals often contain structural headings with zero direct paragraph text (e.g. Section `4.2 Error Codes` immediately followed by child table `4.2.table.1`).
   - Recursively rolls up child tables and subsections into `effective_content`.
   - Computes a `subtree_hash` across each section and all its descendants. Selecting parent headings provides full child table context to the test generator, and modifying child tables in v2 properly flags parent test cases as stale.

3. **Planted Inconsistencies Auditing & Relational Cross-References:**
   - `GET /documents/{version_id}/inconsistencies`: Automatically audits the document for safety-critical defects:
     - Timing contradiction: Section 4.1 specifies emergency venting within 2.0s, while Section 4.2 Error Codes Table (row E3) specifies auto-deflation within 1.5s in v2.
     - Foreign vendor anomaly: Section 6.2 states "Anthropic recommends professional recalibration...", mistakenly citing an AI company instead of CardioTrack.
   - `GET /documents/{version_id}/cross-references`: Extracts cited section numbers (such as Section 3.3 citing `2.1, 4.3`) and validates that target sections exist in the document tree.

4. **Document Versioning & Canonical Diffing:**
   - Ingests new revisions (e.g. `v2`) as new versions while preserving historical snapshots of `v1`.
   - Computes canonical whitespace-normalized SHA-256 hashes so that layout/pagination shifts (such as Section 5.3 pushing Section 6 onto new pages) do not trigger false diff changes.
   - Accurately highlights added sections (`5.3 Data Export`), modified sections (`2.1.1.1`, `3.2`, `4.2`, `4.2.table.1`, `4.3`), and unchanged sections.

5. **Version-Pinned Selection API:**
   - `POST /selections` allows users to save named selections referencing specific node+version pairs.
   - When fetched later, selections resolve the exact version-pinned source text even after newer revisions are uploaded.

6. **Source-Grounded QA Test Generation:**
   - Generates structured test cases with title, ordered execution steps, concrete expected results referencing exact numerical limits or error codes, and source quotes.
   - Validated against strict Pydantic schemas with an automatic 3-attempt retry loop that strictly enforces the exact requested test case count (`len(test_cases) == count`).
   - Includes a deterministic offline fallback generator that extracts actual numbers, error codes, and pressure thresholds, so the entire pipeline works out-of-the-box without needing an external API key.
   - **Duplicate Selection Policy:** Returns cached test cases idempotently when `regenerate=False` (saving token costs and keeping IDs stable), with explicit re-generation available when `regenerate=True`.

7. **Automated Staleness & Impact Detection:**
   - When a new document version is uploaded, older test cases are evaluated against the newest revision using both direct content hashes and subtree hashes.
   - Flags modified sections and parent sections with modified child tables as `stale = true` with reason `"source_modified"`, and deleted sections as `"source_deleted"`.
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

### 11. Audit Planted Inconsistencies (Timing & Vendor Anomalies)
```bash
curl -X GET "http://127.0.0.1:8000/documents/2/inconsistencies"
```
*Identifies the 2.0s vs 1.5s emergency venting contradiction between Section 4.1 and Section 4.2 (Row E3), and flags the foreign vendor citation of "Anthropic" in Section 6.2.*

### 12. Query Relational Cross-References
```bash
curl -X GET "http://127.0.0.1:8000/documents/2/cross-references"
```
*Returns captured graph edges (e.g. Section 3.3 linking to 2.1 and 4.3).*

---

## System Limitations & Known Constraints

1. **OCR Limitations for Scanned/Bitmap PDFs:**
   - The extraction pipeline relies on native PDF text streams and font span dictionaries extracted via PyMuPDF.
   - An OCR fallback mechanism (`page.get_textpage_ocr`) is integrated for pages with little or no extractable text. However, running OCR requires the underlying Tesseract binary and language data (`tessdata`) to be installed on the host operating system. Without host Tesseract binaries, bitmap-only scanned pages without text layers will yield empty content.

2. **Cross-Version Section Matching by Number:**
   - Section nodes are aligned across revisions by their structural keys (`node_key`, e.g., `3.2` to `3.2`).
   - If an author inserts a new section early in a document and shifts subsequent numbers (e.g. `2.2` inserted, shifting `2.2` -> `2.3`), the system evaluates shifted sections as deletions and additions rather than renames. In a full production deployment, this would be augmented with bipartite title/content embedding alignment.

3. **Multi-Column Flow Assumption:**
   - Page blocks are sorted by their vertical coordinate ($y_0$) across the page width. This functions reliably for standard single-column technical manuals, but pages formatted with multiple parallel text columns require bounding-box clustering to avoid reading horizontally across column gutters.

---

## Running the Automated Test Suite

Run pytest from the project directory:
```bash
python -m pytest -v
```

All 17 unit, irregularity, and integration tests pass:
- `tests/test_parser.py`: 6 tests: Heading regexes, Unicode ligature decomposition, whitespace-invariant canonical hashing, full extraction on the real CT-200 PDF, and OCR fallback handler.
- `tests/test_irregular_pdfs.py`: 5 edge-case PDFs:
  1. Empty PDF with no text.
  2. Skipped/jumping numbering hierarchy (`1.0` -> `1.5.3.1`).
  3. Unnumbered headings with embedded numbered list items.
  4. Ragged tables with missing columns and `None` cells.
  5. Duplicate heading numbering and identical titles across parent chapters.
- `tests/test_inconsistencies_and_rollup.py`: 4 targeted integration tests:
  1. Parent-child content roll-up on heading 4.2, child table retrieval, and subtree hash staleness evaluation across v1 and v2.
  2. Planted inconsistencies auditing (4.1 vs 4.2 timing contradiction, 6.2 Anthropic vendor anomaly, and 3.3 cross-reference graph edges).
  3. Heading trailing dot preservation (e.g. `1. Device Overview`).
  4. Exact requested test case count validation (verifying exact count enforcement for 1, 3, 5).
- `tests/test_api_and_traceability.py`: 2 end-to-end integration tests (health check + full lifecycle: Ingest v1 -> browse tree -> top-level sections -> node children -> create selection -> generate cases -> duplicate policy test -> ingest v2 -> version diff -> node change check -> staleness detection -> filtered retrieval).
