# CT-200 Document Intelligence & QA Traceability

Backend service built for the Tri9T AI Engineering Internship assignment. It ingests technical and regulatory manuals for the CardioTrack CT-200 blood pressure monitor directly from raw PDF files, extracts a structured hierarchy (sections, subsections, and Markdown tables), tracks document revisions, aligns sections across versions using a deterministic matcher, manages version-pinned selections, audits specification inconsistencies, and generates source-grounded QA test cases with automated staleness impact detection.

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

## Core System Architecture & Features

### 1. Direct PDF Parsing (Without Intermediate Markdown)
- Parses `sample_data_ct200_manual.pdf` and `v2` directly using PyMuPDF.
- Differentiates real chapter headings from clinical bullet points (`1. Normal...`, `2. Elevated...` under Section 3.3) by inspecting font size and boldness flags.
- Converts the General Specifications table (Page 2) and Error Codes table (Page 4) into clean GitHub-style Markdown tables attached to parent sections.
- Decomposes Unicode ligatures (`\ufb01` -> `fi`, `\ufb02` -> `fl`) and standardizes hyphens to prevent terminal encoding crashes.
- Handles skipped hierarchy levels (`2.1.1.1` cleanly linking to parent `2.1`) via ancestor key backtracking.
- Disambiguates duplicate headings (`1.1` and `1.1#2`) deterministically.
- Dynamic font size thresholding: adapts heading detection to the document's median body text font size rather than hardcoding arbitrary point values.
- OCR fallback hook: attempts PyMuPDF OCR fallback for pages with little or no extractable text (<50 characters).

### 2. Parent-Child Content Roll-Up & Subtree Hashing
- Technical manuals often contain structural headings with zero direct text (e.g. Section `4.2 Error Codes` followed immediately by child table `4.2.table.1`).
- Recursively aggregates descendant tables and paragraphs into `effective_content`.
- Computes `subtree_hash` across each section and all its descendants. Selecting parent headings provides full child table context to the test generator, and modifying child tables in v2 properly flags parent test cases as stale.

### 3. Cross-Version Section Matcher (`app/services/matcher.py`)
- Aligns sections across revisions using normalized title similarity (Levenshtein ratio) and token Jaccard content similarity.
- Resolves renumbered sections (e.g., Section `2.1` becoming `2.2` when an earlier section is inserted) by matching titles and body content.
- Resolves slightly rephrased titles without losing section identity.
- Explicitly suppresses false matches between unrelated sections sharing short boilerplate text (e.g. "Do not immerse device in water").
- Classifies sections into `unmodified`, `modified`, `added`, and `removed`.

### 4. Generalized Inconsistency & Anomaly Detector (`app/services/inconsistency_detector.py`)
- **Generalized Timing Contradictions:** Scans arbitrary sections for specifications of the form `"within N seconds"`. Clusters actions by domain (e.g., `emergency_deflation`, `display_power_down`, `pump_motor_cutoff`, `data_transmission`), and detects conflicting limits.
  - Automatically identifies the planted contradiction: Section 4.1 specifies emergency valve venting within 2.0s, whereas Section 4.2 Error Codes Table (Row E3) specifies auto-deflation within 1.5s in v2.
  - Avoids false contradictions on unrelated actions with different time limits (e.g., 30s display sleep vs. 2.0s valve vent).
- **Foreign Vendor Anomalies:** Flags unexpected AI vendor citations (Section 6.2 stating *"Anthropic recommends professional recalibration..."* for a CardioTrack device).
- **Relational Cross-References:** Captures cross-references (such as Section 3.3 referencing `2.1, 4.3`) and validates that destination sections exist in the document tree.

### 5. Version-Pinned Selection API
- `POST /selections`: Saves named selections referencing specific node+version pairs, freezing the exact source text against immutable `version_id`s.
- `GET /selections/{id}`: Resolves the frozen text and metadata even after newer document versions are uploaded.

### 6. QA Generation & Strict Count Validation (`app/services/qa_generator.py`)
- Generates structured test cases citing exact numerical limits, error codes, and source quotations.
- Enforces strict Pydantic schema validation and an automatic 3-attempt retry loop that strictly validates the exact requested test case count (`len(test_cases) == count`).
- Built-in rule synthesizer provides deterministic offline test generation out-of-the-box without requiring an external LLM API key.
- **Idempotency Policy:** In `POST /test-cases/generate`, passing `regenerate=False` (default) re-returns existing test cases for that selection to save token spend and preserve test case IDs; passing `regenerate=True` forces re-generation.

### 7. Automated Staleness Impact Detection
- When `v2` is uploaded, older test cases are evaluated against the latest version using both direct content hashes and subtree hashes.
- Flags modified sections and parent sections with modified child tables as `stale = true` (`stale_reason = "source_modified"`), and deleted sections as `stale_reason = "source_deleted"`.

---

## Setup & Running Locally

### 1. Prerequisites
* Python 3.10 or higher
* pip

### 2. Installation
```bash
# Clone repository
git clone https://github.com/Vamsi112-max/ct200-document-intelligence.git
cd ct200-document-intelligence

# Create virtual environment
python -m venv .venv

# Activate virtual environment
# On Windows (PowerShell):
.venv\Scripts\Activate.ps1
# On Linux/macOS:
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

*(Note for Windows users: If PowerShell blocks script execution, run `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` before activating the venv).*

---

## Environment Variable & LLM Configuration

The project includes a documented `.env.example` file.

### 1. Default Mode: Built-in Offline Mode (Zero Configuration)
No environment variables or API keys are required. If `OPENAI_API_KEY` is unset, the application automatically uses its deterministic rule synthesizer, extracting exact pressure thresholds, timings, and error codes directly from the manual text.

### 2. OpenAI Configuration
```bash
# Windows PowerShell:
$env:OPENAI_API_KEY = "sk-your-openai-api-key"
$env:LLM_MODEL = "gpt-4o-mini"
$env:OPENAI_BASE_URL = "https://api.openai.com/v1"

# Linux / macOS:
export OPENAI_API_KEY="sk-your-openai-api-key"
export LLM_MODEL="gpt-4o-mini"
export OPENAI_BASE_URL="https://api.openai.com/v1"
```

### 3. Google Gemini Configuration (via OpenAI-Compatible Endpoint)
Gemini does not require a separate SDK. It connects via Google's official OpenAI-compatible endpoint:
```bash
# Windows PowerShell:
$env:OPENAI_API_KEY = "your-gemini-api-key"
$env:OPENAI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
$env:LLM_MODEL = "gemini-1.5-flash"
```

### 4. Local LLMs (Ollama / vLLM / LM Studio)
```bash
$env:OPENAI_BASE_URL = "http://localhost:11434/v1"
$env:OPENAI_API_KEY = "ollama"
$env:LLM_MODEL = "llama3.1"
```

---

## Running the Application

Start the local server:
```bash
python -m uvicorn app.main:app --reload --port 8000
```
Open [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs) in your browser for the interactive Swagger UI.

---

## End-to-End Workflow & Example cURL Commands

### 1. Health Probe
```bash
curl -X GET "http://127.0.0.1:8000/health"
```

### 2. Ingest Version 1 PDF
```bash
curl -X POST "http://127.0.0.1:8000/documents/ingest?document_key=ct200" \
  -H "accept: application/json" \
  -F "file=@sample_data_ct200_manual.pdf;type=application/pdf"
```

### 3. Browse Document Tree
```bash
curl -X GET "http://127.0.0.1:8000/documents/1/tree"
```

### 4. Create Pinned Selection (Freeze Section 3.2 and 1.1)
```bash
curl -X POST "http://127.0.0.1:8000/selections" \
  -H "Content-Type: application/json" \
  -d '{"name": "Clinical Validation Set", "version_id": 1, "node_keys": ["3.2", "1.1"]}'
```

### 5. Generate QA Test Cases from Selection
```bash
curl -X POST "http://127.0.0.1:8000/test-cases/generate" \
  -H "Content-Type: application/json" \
  -d '{"selection_id": 1, "count": 2, "regenerate": false}'
```

### 6. Ingest Version 2 PDF (Revision)
```bash
curl -X POST "http://127.0.0.1:8000/documents/ingest?document_key=ct200" \
  -H "accept: application/json" \
  -F "file=@sample_data_ct200_manual_v2.pdf;type=application/pdf"
```

### 7. Compare Version Diff (v1 vs v2)
```bash
curl -X GET "http://127.0.0.1:8000/documents/1/diff/2"
```

### 8. Check Test Case Staleness
```bash
curl -X GET "http://127.0.0.1:8000/test-cases/1/staleness"
```

### 9. Audit Planted Inconsistencies & Timing Contradictions
```bash
curl -X GET "http://127.0.0.1:8000/documents/2/inconsistencies"
```

### 10. Query Relational Cross-References
```bash
curl -X GET "http://127.0.0.1:8000/documents/2/cross-references"
```

---

## Running the Automated Test Suite

Run pytest from the project directory:
```bash
python -m pytest -q
```

**All 30 automated unit, irregularity, and integration tests pass:**
* `tests/test_matcher_and_generalized_inconsistencies.py` (13 tests):
  * Section matching: identical titles with modified content, renumbered sections (`2.1` -> `2.2`), title edits, short text boilerplate isolation, empty section disambiguation, Unicode ligature decomposition, additions/deletions.
  * Generalized inconsistencies: Section 4.1 vs 4.2 contradiction, arbitrary section numbers, custom section titles, unrelated action isolation, multiple timing statements in same section, agreeing specifications.
* `tests/test_parser.py` (6 tests): Heading regex levels, content-sensitive hashing, ligature normalization, linebreak invariance, real CT-200 PDF parsing, and OCR fallback handler.
* `tests/test_irregular_pdfs.py` (5 tests): Edge cases for empty PDFs, skipped numbering levels (`1.0` -> `1.5.3.1`), unnumbered list collisions, ragged tables, and duplicate headings.
* `tests/test_inconsistencies_and_rollup.py` (4 tests): Parent-child rollup and subtree hash staleness on Section 4.2, planted inconsistencies audit, trailing dot preservation, exact test case count validation (1, 3, 5).
* `tests/test_api_and_traceability.py` (2 tests): Health probe and end-to-end v1 to v2 full lifecycle.

---

## System Limitations & Known Constraints

1. **OCR Host Dependency for Scanned Images:**  
   The parser uses native PDF text and font spans. While an OCR fallback hook (`page.get_textpage_ocr`) is provided, optical character recognition requires external Tesseract binaries and `tessdata` installed on the host OS. Without host Tesseract binaries, bitmap-only scanned pages without text layers will extract as empty text.
2. **1-to-1 Section Matching Model:**  
   The cross-version matcher pairs sections 1-to-1. If a revision splits a single section into multiple subsections, or merges several sections into one, the matcher pairs the closest match and marks the remainder as additions/deletions.
3. **Multi-Column Text Flow:**  
   Blocks are sorted vertically ($y_0$) across the page. Documents with multi-column text layouts require bounding-box column clustering to avoid reading horizontally across column gutters.
