# Decision Log — CT-200 Document Intelligence

This log records the core design trade-offs, defensive decisions, and acknowledged weaknesses in our implementation.

---

### 1. What's the one part of this system most likely to silently give wrong results without erroring? How would you catch it?

**The failure mode:**
The parent-child hierarchy reconstruction in `app/services/parser.py`. 
When a document contains unnumbered subheadings or stylistic headings where font size doesn't cleanly exceed body text, the parser cannot reliably tell if the line is a section header or a bold introductory sentence. In such cases, the parser continues accumulating lines into the previous active section. The system returns HTTP 200, persists a clean database tree, and passes basic sanity checks—but downstream paragraphs and tables are silently parented to the wrong section.

**How to catch it:**
1. **Structural Invariant Validation:** Run an automated audit checking the ratio of parent-to-child word counts and asserting that section depth does not skip across structural boundaries without an explicit sub-heading.
2. **Visual Bounding-Box Overlay:** Generate a debug PDF overlay highlighting detected headings (green), body text (blue), and tables (red) with their assigned `node_key` annotations. Visual inspection against page margins immediately reveals mis-parented blocks.

---

### 2. Where did you choose simplicity over correctness because of time, and what would break first if this went to production as-is?

**The trade-off:**
Section identity and version matching is strictly key/path-based (`node_key`, e.g., `3.2` -> `3.2`).

**What breaks first in production:**
Section renumbering. In regulatory medical manuals, adding a new section early in the document (for example, inserting `2.2 Bluetooth Connectivity` between `2.1` and `2.2`) shifts all subsequent sections down by one (`2.2` becomes `2.3`, `2.3` becomes `2.4`). 
Our current system will report that every renumbered section was "deleted" and "added" from scratch. This completely destroys traceability: test cases generated against the old `2.2` will be flagged as `source_deleted`, and no test cases will link to the new `2.3` even though 100% of the text remained unchanged.

**Production remedy:**
A bipartite graph matching algorithm combining title fuzzy matching (normalized Levenshtein distance) and semantic embedding cosine similarity on the section body text to re-link nodes across renumbering events.

---

### 3. Name one input (to your parser, your versioning matcher, or your LLM call) that you did not handle, and what your system does when it sees it.

**The unhandled input:**
Multi-column page layouts (such as two-column specification sheets or side-by-side callout boxes).

**What the system does:**
PyMuPDF's block extractor on each page is sorted by top-to-bottom vertical coordinate ($y_0$) across the entire page width. When processing a two-column layout, it reads horizontally across column 1 and column 2 line-by-line rather than flowing down column 1 first and column 2 second.
The system does not crash or raise an error; instead, it concatenates alternating lines from both columns into a jumbled paragraph, computes a SHA-256 hash over that jumbled text, and stores it in SQLite. When sent to the LLM, the prompt receives garbled sentences with interleaved thoughts, causing the LLM to either fail schema validation or produce hallucinated test cases.

---

### 4. Direct PDF Parsing vs. Markdown Intermediate

* **Decision:** Parse the PDF directly using PyMuPDF (`page.get_text("dict")` and `page.find_tables()`) rather than converting to Markdown first.
* **Why:** The Page 3 clinical list (`1. Normal...`, `2. Elevated...`) shares identical regex patterns with Chapter 1 and Chapter 2 headings. Markdown conversion strips font sizes and weights, making list-vs-heading disambiguation nearly impossible. PyMuPDF gives direct access to font family (`NimbusSans-Bold`), point size (16.5pt vs 11.0pt), and table bounding boxes.

---

### 5. Selection API & Idempotent Generation Policy

* **Decision:** We implemented version-pinned selections stored in `selections` referencing immutable `version_id` and node IDs/keys.
* **Duplicate Selection Policy:** When a client submits the same `selection_id` to `/test-cases/generate`:
  - If `regenerate=False` (default), the API returns the existing generated test cases immediately (HTTP 200). This enforces idempotency, prevents duplicate database pollution, and saves costly LLM token spend.
  - If `regenerate=True`, the API explicitly re-queries the LLM and appends a fresh generation batch.

---

### 6. Relational SQLite with Structured JSON vs Remote MongoDB

* **Decision:** Use SQLite with SQLAlchemy 2.0 and native JSON columns rather than an external MongoDB Atlas cluster.
* **Why:** Evaluator reproducibility and ACID reliability. Storing document versions, nodes, selections, and test cases in a single relational database guarantees atomic foreign key integrity and transactional consistency without network dependencies or cloud credential failures during offline grading. The test case steps and selection node lists are stored as structured JSON arrays, matching NoSQL document ergonomics.
