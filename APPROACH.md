# CT-200 Document Intelligence & QA Traceability — Technical Approach
**Author:** Vamsi Inampudi ([@Vamsi112-max](https://github.com/Vamsi112-max))  
**Assignment:** Tri9T AI Engineering Internship  

---

## 1. Architectural Strategy & Direct PDF Ingestion

The objective of this service is to ingest CardioTrack CT-200 blood pressure monitor documentation directly from raw PDF files, construct an accurate structural hierarchy, manage versioned document lifecycles, and generate traceable, reproducible QA test cases.

Rather than relying on intermediate Markdown conversion (which discards font metrics, bounding box coordinates, and typographic styling), the system processes raw PDFs directly using **PyMuPDF (`fitz`)**. Low-level access to font spans, bounding boxes (`bbox`), and table cell structures allows the parser to reconstruct document semantics with high fidelity while remaining resilient across revision updates.

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

**Technical Solution:**  
The parser inspects font span metadata alongside regex patterns:
- Multi-part section numbers (e.g. `1.1`, `2.1.1.1`) are categorized as section headings regardless of font weight.
- Single-digit entries (`1.`, `2.`) must exhibit bold styling (`is_bold == True`) or exceed an adaptive font size threshold (`max_size >= heading_size_threshold`) to qualify as headings.
- Unbolded body lines containing title colons (`:`) within standard point sizes are classified as paragraph text and appended to the active section.

### 2. Table Extraction & Bounding Box Suppression
Section 2.1 contains a General Specifications table and Section 4.2 contains an Error Codes table. Naive text extraction merges column cells into unstructured strings, separating error codes from their associated actions.

**Technical Solution:**  
PyMuPDF's `page.find_tables()` identifies table structures, converting cell matrices into standardized GitHub-Flavored Markdown tables. Text blocks falling within table bounding boxes (`is_rect_overlap`) are suppressed during text block iteration to prevent duplication. Table nodes are inserted as child nodes under the immediate parent heading based on vertical coordinate order ($y_0$).

### 3. Unicode Ligature & Typographic Normalization
The PDF uses embedded OpenType ligatures:
- `\ufb01` for "fi" (e.g., `Speciﬁcations`, `Deﬂation`)
- `\ufb02` for "fl" (`Inﬂation`)
- Non-standard hyphens (`\u2011`, `\u2013`) and curved quotation marks.

These characters cause terminal encoding failures on standard Windows code pages (CP1252) and break keyword searching.

**Technical Solution:**  
All extracted strings pass through `normalize_text()`, which executes `unicodedata.normalize("NFKD", text)`. This decomposes compatibility ligatures into plain ASCII characters (`fi`, `fl`, `-`, `"`) while stripping non-printable control codes.

### 4. Non-Sequential Hierarchy Backtracking (`2.1.1.1`)
In Section 2, the document jumps directly from `2.1 General Specifications` to `2.1.1.1 Battery Life Under Typical Use` without an intermediate `2.1.1` heading.

**Technical Solution:**  
`parser.py` implements an ancestor backtracking loop. When resolving a parent key, it trims section keys progressively from right to left (`2.1.1.1` -> `2.1.1` -> `2.1`). It verifies candidate keys against previously registered headings, assigning `2.1` cleanly as the parent node.

### 5. Duplicate Heading Disambiguation
Manuals occasionally feature repeated section numbers across chapters or duplicate titles.

**Technical Solution:**  
The parser maintains a frequency dictionary of heading keys. When a duplicate section number is encountered, it appends an index suffix (`1.1#2`) and links the node to the nearest matching ancestor in the hierarchy.

### 6. Parent Headings with Child Content Roll-Up (Node 4.2 Error Codes)
Section `4.2 Error Codes` consists solely of a section title followed by table `4.2.table.1`. The heading node itself has empty direct text (`content = ""`, `content_hash = digest("")`).
If selections or test generation rely solely on a node's direct content, selecting `4.2` passes empty text to the LLM generator. Furthermore, when `v2` modifies rows in the error table, the direct content hash of `4.2` remains identical, falsely marking the section as unchanged.

**Technical Solution:**  
The system implements bottom-up content roll-up (`rollup_subtree_content`). Each node computes:
1. `content`: Direct paragraph text.
2. `effective_content`: Direct text aggregated with all descendant tables, sub-paragraphs, and child sections.
3. `subtree_hash`: SHA-256 hash computed over `effective_content`.
When generating test cases from `4.2`, the LLM receives the complete child error table. When `4.2.table.1` is altered in v2, `4.2`'s `subtree_hash` changes, correctly invalidating test cases with `stale = true` (`source_modified`).

### 7. Adaptive Font Size Thresholding
Rather than relying on a hardcoded 12.0pt threshold, `parser.py` samples font sizes across page blocks to determine the document's median body text size (`median_font_size`). Headings are detected using an adaptive ratio `max(11.8, median_font_size * 1.15)`, ensuring stability across documents with varying base typography.

### 8. OCR Fallback for Low-Text Pages
For pages with minimal or no embedded vector text (e.g. scanned bitmap inserts), `extract_page_dict_with_ocr_fallback()` inspects character density. If extracted characters fall below 50, it attempts a PyMuPDF OCR textpage extraction (`page.get_textpage_ocr`).

---

## 3. Cross-Version Section Matching (`app/services/matcher.py`)

To compare document versions and track section lifecycles, `matcher.py` implements a multi-pass deterministic matching pipeline:

1. **Pass 1: Identical Key & Strong Title Similarity (>= 0.85):**  
   Pairs sections sharing the same structural numbering and title. Computes content similarity to categorize the section as `exact` (unmodified) or `content_modified`.
2. **Pass 2: Renumbered Sections:**  
   Resolves cases where section numbering changed across revisions (e.g. Section `2.1` becoming `2.2` because a new section was inserted). Matches sections with high title similarity (>= 0.85, invariant to leading section numbers) and high content overlap.
3. **Pass 3: Renamed Sections:**  
   Pairs sections with matching structural keys whose titles were edited but whose substantive body content remains similar (>= 0.70 similarity).
4. **Pass 4: Unmatched Classifications:**  
   Unmatched nodes in the newer version are categorized as `added`; unmatched nodes in the older version are categorized as `removed`.
5. **False Match Suppression:**  
   Unrelated sections sharing short identical boilerplate text (e.g. "Do not immerse device in water") are explicitly prevented from matching by requiring strong title similarity.

---

## 4. Generalized Inconsistency & Cross-Reference Auditing

`inconsistency_detector.py` provides automated domain auditing for document contradictions:

1. **Generalized Timing Contradictions:**  
   Scans arbitrary sections for statements matching `"within N seconds"`. Rather than relying on hardcoded section numbers, it extracts action keywords, clusters them into domain categories (e.g., `emergency_deflation`, `display_power_down`, `pump_motor_cutoff`, `data_transmission`), and flags genuine contradictions where the specified time limits conflict.
   - Detects the planted CT-200 contradiction: Section 4.1 specifies emergency valve venting within 2.0s, whereas Section 4.2 Error Codes Table (Row E3) specifies auto-deflation within 1.5s in v2.
   - Prevents false contradictions on unrelated actions with different time limits (e.g. 30s display sleep vs. 2.0s valve vent).
2. **Planted Vendor Anomalies:**  
   Scans for foreign technology vendor citations (e.g., Section 6.2 stating *"Anthropic recommends professional recalibration..."* for a CardioTrack device).
3. **Relational Cross-References:**  
   Extracts cross-references (such as Section 3.3 referencing `2.1, 4.3`) and validates that destination nodes exist in the document tree.

---

## 5. Selection API & Idempotent Generation Policy

- `POST /selections`: Creates a named selection referencing specific node IDs/keys and freezes the exact text against the immutable `version_id`.
- `GET /selections/{id}`: Resolves the frozen text and metadata.
- **Idempotent Generation Policy:** When calling `POST /test-cases/generate` with a `selection_id` and `regenerate=False` (default), the API returns existing test cases tied to that selection. This prevents redundant LLM token spend and preserves test case IDs across automated regression runs. Passing `regenerate=True` forces fresh test generation.

---

## 6. QA Generation & Strict Count Validation

Located in `app/services/qa_generator.py`:
- Ingests selected section content and formats it into a prompt demanding concrete pass/fail thresholds, exact error codes, and source quotations.
- Output is validated against the `QAGenerationResponse` Pydantic schema.
- **Strict Count Validation:** The response is verified to contain *exactly* the requested number of test cases (`len(test_cases) == count`). If the count or schema fails, the error is fed back to the LLM in an automatic retry loop (up to 3 attempts).
- **Deterministic Offline Fallback:** If no API key is provided or retries fail, `generate_local_grounded_cases()` extracts real parameter boundaries, timings, and error table rows to produce valid, grounded test cases without external network calls.

---

## 7. Known System Limitations

1. **OCR Host Dependency:**  
   PyMuPDF's OCR fallback requires external Tesseract binaries and `tessdata` installed on the host operating system. Without host Tesseract binaries, bitmap-only scanned pages without text layers will yield empty content.
2. **1-to-1 Matching Assumption:**  
   The section matcher pairs nodes 1-to-1. Revisions that split a single section across multiple subsections, or merge several sections into one, are classified as additions and deletions.
3. **Single-Column Reading Order:**  
   Text blocks are sorted vertically ($y_0$) across the page. Multi-column documents require bounding-box column clustering to avoid reading horizontally across column gutters.
