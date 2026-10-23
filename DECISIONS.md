# Decision Log — CT-200 Engineering Choices & Trade-offs
**Author:** Vamsi Inampudi ([@Vamsi112-max](https://github.com/Vamsi112-max))

The assignment prompt specifically asked for direct answers to three questions representing genuine engineering reasoning and trade-offs. Here is the technical assessment of this system:

---

### Question 1: What's the one part of this system most likely to silently give wrong results without erroring? How would you catch it?

**Answer:**  
The heading detection and parent-child section nesting logic inside `app/services/parser.py`.

When a technical document contains an unnumbered heading or a heading styled with unusual font formatting (for example, a bold sub-header rendered at 11.5pt when the adaptive threshold is 11.8pt, or a header lacking bold flags), the parser will not classify it as a new section. Instead, it silently appends all of that section's paragraphs and tables to whatever previous section was active.

The service will not throw any runtime exceptions. It returns an HTTP 200, persists records to the database, and displays in Swagger UI without warnings. However, content is silently parented to the wrong section in the hierarchy.

**How to catch it in production:**
1. **Structural Invariant Checks:** Implement automated structural linters that flag statistical anomalies, such as a child section having 10x more text than its siblings, or a section spanning 4 pages without any sub-headers.
2. **Visual Bounding-Box Overlay Auditing:** Generate an annotated debug PDF during parsing where detected headings are outlined in green, body text in blue, and tables in red, with assigned section keys printed in the margins. Reviewing this visual overlay makes mis-parented text immediately visible.

---

### Question 2: Where did you choose simplicity over correctness because of time, and what would break first if this went to production as-is?

**Answer:**  
The 1-to-1 section matching model in `app/services/matcher.py`.

The matcher aligns sections across document revisions using normalized title similarity (Levenshtein ratio) and token Jaccard content similarity. This successfully resolves renumbered sections (e.g. Section `2.1` moving to `2.2`), minor title edits, and content modifications.

**What would break first in production:**  
1-to-many section splits and many-to-1 section merges. In complex regulatory manual revisions, an engineering team frequently takes a large section (such as `4.2 Error Codes`) and splits it across multiple dedicated subsections (`4.2.1 Sensor Faults`, `4.2.2 Pneumatic Faults`, `4.2.3 Battery Faults`), or merges multiple small annexes into a single chapter.

Because the current matcher enforces a deterministic 1-to-1 bipartite pairing, it would match one subsection and mark the remaining split subsections as completely new additions, fragmenting historical QA traceability.

**What should be done instead:**  
Implement an n-to-m semantic alignment graph using sentence embeddings and hierarchical subtree clustering. This allows the system to recognize that a set of multiple child sections in v2 collectively correspond to a single parent section in v1.

---

### Question 3: Name one input (to your parser, your versioning matcher, or your LLM call) that you did not handle, and what your system does when it sees it.

**Answer:**  
Two specific input edge cases are constrained by current design choices:

1. **Multi-Column Flow Layouts:**  
   In `app/services/parser.py`, page text blocks are sorted by vertical coordinate ($y_0$) across the page width. When processing a multi-column document, the parser reads horizontally across column gutters rather than top-to-bottom within each column. The resulting text interleaves lines from adjacent columns, producing garbled sentences that downstream LLM validation must reject or retry.
   
2. **Scanned Image-Only PDFs without Host Tesseract:**  
   While `extract_page_dict_with_ocr_fallback()` includes an OCR fallback hook via `page.get_textpage_ocr()`, PyMuPDF depends on the host operating system having Tesseract binaries and `tessdata` installed. On an environment lacking Tesseract, scanned bitmap pages yield fewer than 50 characters and extract as empty content.

---

### Additional Architectural Decisions

#### 4. Direct PDF Parsing vs. Intermediate Markdown Conversion
The system parses raw PDF bytes directly via PyMuPDF instead of converting to Markdown first. External Markdown converters strip font size metrics, boldness flags, and bounding box coordinates. Without low-level font spans, disambiguating clinical numbered lists (e.g. Page 3 clinical thresholds under Section 3.3) from true chapter headings is unreliable.

#### 5. Selection API & Idempotent Generation Policy
`POST /selections` creates named, version-pinned selections that freeze source text against immutable `version_id`s. `POST /test-cases/generate` defaults to idempotent cache retrieval (`regenerate=False`), saving token spend and keeping test case IDs stable across regression runs, while allowing explicit re-generation with `regenerate=True`.

#### 6. SQLite with Relational Schemas vs. Remote NoSQL
SQLite with relational foreign keys and JSON arrays was selected over remote document stores (like MongoDB Atlas) to eliminate network configuration overhead for evaluators, provide ACID transaction safety when updating version staleness, and keep the application fully self-contained.

#### 7. Bottom-Up Content Roll-Up & Subtree Hashing
Parent sections with empty direct text (such as Section `4.2 Error Codes` immediately followed by child table `4.2.table.1`) aggregate descendant content into `effective_content` and compute `subtree_hash`. This ensures that downstream test generators receive complete child table context, and modifications to child tables in v2 properly invalidate test cases pinned to parent headings.

#### 8. Generalized Timing Contradiction Detection
Rather than hardcoding section numbers `4.1` and `4.2`, `inconsistency_detector.py` scans arbitrary sections for specifications of the form `"within N seconds"`, extracts action keywords, clusters them by domain (e.g. emergency deflation vs. display standby), and detects conflicting numerical limits.
