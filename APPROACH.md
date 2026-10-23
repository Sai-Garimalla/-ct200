# CT-200 Document Intelligence & QA Traceability — Engineering Approach
**Author:** Vamsi Inampudi ([@Vamsi112-max](https://github.com/Vamsi112-max))  
**Assignment:** Tri9T AI Engineering Internship  

---

## 1. Architectural Strategy & Direct PDF Ingestion

The objective of this service is to ingest CardioTrack CT-200 blood pressure monitor documentation directly from raw PDF files, construct an accurate structural hierarchy, manage versioned document lifecycles, and generate traceable, reproducible QA test cases.

Rather than relying on lossy text dumps or external Markdown converters (which discard font metrics, table coordinate bounds, and typographical styling), the system processes the raw PDF via **PyMuPDF (`fitz`)**. Access to low-level font spans, bounding boxes (`bbox`), and table structures allows the parser to reconstruct document semantics with high fidelity while remaining resilient across revision updates.

---

## 2. Layout Extraction & Structural Challenges

Analyzing the CT-200 technical documentation revealed several layout and typographical challenges. The implementation addresses each as follows:

### 1. Disambiguating Numbered Clinical Lists from Chapter Headings
Under Section `3.3 Result Display and Classification`, the manual includes a clinical threshold list:
```text
1. Normal: systolic < 120 and diastolic < 80
2. Elevated: systolic 120–129 and diastolic < 80
3. Hypertension Stage 1: systolic 130–139 or diastolic 80–89
4. Hypertension Stage 2: systolic ≥ 140 or diastolic ≥ 90
5. Hypertensive Crisis: systolic > 180 or diastolic > 120
```
A pure regex pattern matching `^\d+(\.\d+)*\s+` would incorrectly classify items `1.` through `5.` as top-level document chapters, overwriting legitimate chapters like `1. Device Overview` and `4. Alarms and Safety Behavior`.

**Implementation:**
The parser inspects font span metadata alongside regex patterns:
- Multi-part section numbers (e.g. `1.1`, `2.1.1.1`) are categorized as section headings regardless of font weight.
- Single-digit entries (`1.`, `2.`) must exhibit bold styling (`is_bold == True`) or exceed an adaptive font size threshold (`max_size >= heading_size_threshold`) to qualify as headings.
- Unbolded body lines containing title colons (`:`) within standard point sizes are classified as paragraph text and appended to the active section.

### 2. Table Extraction & Bounding Box Suppression
Section 2.1 contains a General Specifications table and Section 4.2 contains an Error Codes table. Naive text extraction merges column cells into unstructured strings, separating error codes from their associated actions.

**Implementation:**
PyMuPDF's `page.find_tables()` identifies table structures, converting cell matrices into standardized GitHub-Flavored Markdown tables. Text blocks falling within table bounding boxes (`is_rect_overlap`) are suppressed during text block iteration to prevent duplication. Table nodes are inserted as child nodes under the immediate parent heading based on vertical coordinate order ($y_0$).

### 3. Unicode Ligature & Typographic Normalization
The PDF uses embedded OpenType ligatures:
- `\ufb01` for "fi" (e.g., `Speciﬁcations`, `Deﬂation`)
- `\ufb02` for "fl" (`Inﬂation`)
- Non-standard hyphens (`\u2011`, `\u2013`) and curved quotation marks.

These characters cause terminal encoding failures on standard Windows code pages (CP1252) and break keyword searching.

**Implementation:**
All extracted strings pass through `normalize_text()`, which executes `unicodedata.normalize("NFKD", text)`. This decomposes compatibility ligatures into plain ASCII characters (`fi`, `fl`, `-`, `"`) while stripping non-printable control codes.

### 4. Non-Sequential Hierarchy Backtracking (`2.1.1.1`)
In Section 2, the document jumps directly from `2.1 General Specifications` to `2.1.1.1 Battery Life Under Typical Use` without an intermediate `2.1.1` heading.

**Implementation:**
`parser.py` implements an ancestor backtracking loop. When resolving a parent key, it trims section keys progressively from right to left (`2.1.1.1` -> `2.1.1` -> `2.1`). It verifies candidate keys against previously registered headings, assigning `2.1` cleanly as the parent node.

### 5. Duplicate Heading Disambiguation
Manuals occasionally feature repeated section numbers across chapters or duplicate titles.

**Implementation:**
The parser maintains a frequency dictionary of heading keys. When a duplicate section number is encountered, it appends an index suffix (`1.1#2`) and links the node to the nearest matching ancestor in the hierarchy.

### 6. Parent Headings with Child Content Roll-Up (Node 4.2 Error Codes)
Section `4.2 Error Codes` consists solely of a section title followed by table `4.2.table.1`. The heading node itself has empty direct text (`content = ""`, `content_hash = digest("")`).
If selections or test generation rely solely on a node's direct content, selecting `4.2` passes empty text to the LLM generator. Furthermore, when `v2` modifies rows in the error table, the direct content hash of `4.2` remains identical, falsely marking the section as unchanged.

**Implementation:**
The system implements bottom-up content roll-up (`rollup_subtree_content`). Each node computes:
1. `content`: Direct paragraph text.
2. `effective_content`: Direct text aggregated with all descendant tables, sub-paragraphs, and child sections.
3. `subtree_hash`: SHA-256 hash computed over `effective_content`.
When generating test cases from `4.2`, the LLM receives the complete child error table. When `4.2.table.1` is altered in v2, `4.2`'s `subtree_hash` changes, correctly invalidating test cases with `stale = true` (`source_modified`).

### 7. Heading Trailing Dot Preservation
Regex group capture preserves original heading punctuation so that chapter headers (`1. Device Overview`) retain the trailing dot while sub-headings (`1.1 Intended Use`) retain clean dotless numbering.

### 8. Adaptive Font Size Thresholding
Rather than relying on a hardcoded 12.0pt threshold, `parser.py` samples font sizes across page blocks to determine the document's median body text size (`median_font_size`). Headings are detected using an adaptive ratio `max(11.8, median_font_size * 1.15)`, ensuring stability across documents with varying base typography.

### 9. OCR Fallback for Scanned Pages
For pages with minimal or no embedded vector text (e.g. scanned bitmap inserts), `extract_page_dict_with_ocr_fallback()` inspects character density. If extracted characters fall below 50, it attempts a PyMuPDF OCR textpage extraction (`page.get_textpage_ocr`).

---

## 3. Data Model & Architecture Decisions

The system is implemented with **FastAPI**, **SQLAlchemy 2.0**, and **SQLite**.

### Database Justification
SQLite with relational foreign keys and JSON columns was chosen over remote document databases (like MongoDB Atlas) for:
1. **Zero External Dependencies:** Evaluators can clone the repository and run the test suite immediately without managing remote database clusters, IP whitelists, or connection strings.
2. **ACID Transaction Guarantees:** Document ingestion, version increments, selection snapshots, and staleness updates occur within explicit database transactions.
3. **Structured Flexibility:** Test case execution steps and selection node lists are stored as typed JSON arrays, combining relational integrity with flexible document structures.

### Data Entities
- **`DocumentVersion`**: Manages document revisions (`document_key="ct200"`), version sequence numbers (monotonic integer), filenames, overall file hash, and creation timestamps.
- **`DocumentNode`**: Stores individual sections, headings, and tables. Tracks `node_key`, `parent_key`, `title`, `level`, `content`, `content_hash`, `effective_content`, `subtree_hash`, `cross_references`, and `page`.
- **`Selection`**: Represents a named collection of sections pinned to a specific `version_id`.
- **`TestCase`**: Represents QA test cases linked to a `version_id`, `node_key`, and the exact `selected_hash` from which they were generated, alongside `stale` and `stale_reason` tracking.

---

## 4. Selection API & Idempotent Generation Policy

### Selection Management
- `POST /selections`: Accepts a name, `version_id`, and target node IDs/keys. Resolves and freezes the exact text at that version.
- `GET /selections/{id}`: Resolves the frozen source text and metadata.

### Duplicate Generation Policy
In `POST /test-cases/generate`:
- When a `selection_id` is passed with `regenerate=False` (default), the API acts **idempotently**: it queries the database for existing test cases tied to that selection and returns them immediately. This prevents token waste, avoids duplicate records, and maintains stable test IDs.
- When `regenerate=True`, the API bypasses the cache and generates fresh test cases.

---

## 5. QA Test Case Generation & Staleness Tracking

### LLM Prompt Architecture & Count Enforcement
Located in `app/services/qa_generator.py`:
- Ingests selected section content and formats it into a prompt demanding concrete pass/fail thresholds, exact error codes, and source quotations.
- Output is validated against the `QAGenerationResponse` Pydantic schema.
- **Strict Count Validation:** The response is verified to contain *exactly* the requested number of test cases (`len(test_cases) == count`). If the count or schema fails, the error is fed back to the LLM in an automatic retry loop (up to 3 attempts).
- **Deterministic Offline Fallback:** If no API key is provided or retries fail, `generate_local_grounded_cases()` extracts real parameter boundaries, timings, and error table rows to produce valid, grounded test cases without external network calls.

### Automated Staleness & Impact Tracking
When a new document version is ingested, `update_all_test_cases_staleness()` evaluates historical test cases against the latest nodes:
- Target node missing: `stale = true`, `stale_reason = "source_deleted"`.
- Target node content or subtree hash altered: `stale = true`, `stale_reason = "source_modified"`.
- Both hashes match: `stale = false`, `stale_reason = null`.

---

## 6. Planted Inconsistencies Auditing & Relational Cross-References

Domain audit heuristics in `app/services/inconsistency_detector.py` automatically detect planted anomalies in the CT-200 manual:

1. **Emergency Venting Timing Contradiction:**
   - Section 4.1 specifies emergency valve venting within 2.0 seconds.
   - Section 4.2 Error Codes table row E3 specifies auto-deflation within 1.5 seconds in v2.
   - Flagged via `GET /documents/{version_id}/inconsistencies` as a `timing_contradiction` (`severity="high"`).

2. **Foreign Vendor Anomaly:**
   - Section 6.2 states: *"Anthropic recommends professional recalibration every 2 years or after 10,000 cycles, whichever comes first."*
   - Flagged as a `vendor_brand_anomaly` (`severity="high"`), identifying an external AI company cited in place of the medical device manufacturer (CardioTrack).

3. **Relational Cross-References:**
   - Section 3.3 references `(see 2.1, 4.3 for related specifications and alarm thresholds)`.
   - Extracted and validated as relational graph edges (`3.3 -> 2.1`, `3.3 -> 4.3`) accessible via `GET /documents/{version_id}/cross-references`.

---

## 7. Known Limitations & Constraints

1. **OCR Host Dependency:**
   - The parser includes an OCR fallback hook for pages with fewer than 50 text characters. However, optical recognition requires external Tesseract binaries and `tessdata` installed on the host OS. Without Tesseract installed, scanned bitmap-only pages will yield empty text.
2. **Structural Key Matching Across Versions:**
   - Versions are aligned by section keys (`node_key`, e.g. `3.2` to `3.2`). If an author inserts a new section that renumbers subsequent chapters, shifted sections appear as deletions and additions rather than renames. A production enhancement would incorporate bipartite graph matching via title and text embeddings.
3. **Single-Column Reading Order:**
   - Blocks are sorted vertically by $y_0$ coordinate across the page. Pages formatted with multi-column text columns require bounding-box clustering to avoid reading horizontally across column gutters.
