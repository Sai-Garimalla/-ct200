# Submission Email Draft

**Subject:** Tri9T AI Engineering Internship Submission — CT-200 Document Intelligence — Vamsi Inampudi

**To:** hiring@tri9t.ai

---

Hi Tri9T AI Engineering Team,

Here is my submission for the **AI Engineering Internship Assignment — CT-200 Document Intelligence & QA Traceability**.

### Repository & Documentation Links
* **GitHub Repository:** https://github.com/Vamsi112-max/ct200-document-intelligence
* **Approach Document:** https://github.com/Vamsi112-max/ct200-document-intelligence/blob/main/APPROACH.md
* **Decision Log:** https://github.com/Vamsi112-max/ct200-document-intelligence/blob/main/DECISIONS.md

---

### Project Summary & Engineering Highlights

1. **Direct PDF Parsing without Markdown Intermediate:**
   - I worked directly with the raw PDF manuals (`sample_data_ct200_manual.pdf` and `v2`) using PyMuPDF.
   - Solved the Page 3 clinical list collision (`1. Normal...`, `2. Elevated...` under Section 3.3) by inspecting font span metadata (point size, bold flags) rather than pure regex.
   - Handled skipped hierarchical levels (e.g. `2.1.1.1` connecting to `2.1`), decomposed embedded Unicode ligatures (`\ufb01` -> `fi`), and extracted structured tables into clean Markdown attached to their parent sections.

2. **Document Versioning & Canonical Whitespace Invariance:**
   - Ingesting `v2` creates a discrete new revision while preserving `v1`. Content hashes are computed over whitespace-normalized text so that layout and pagination shifts (such as Section 5.3 pushing Section 6 onto new pages) do not trigger false diffs.

3. **Version-Pinned Selection API & Idempotent Generation:**
   - Implemented `POST /selections` and `GET /selections/{id}` to freeze source text against immutable `version_id`s.
   - Enforced an explicit duplicate selection policy: idempotent cache retrieval by default (`regenerate=False`) to save token costs and prevent duplicate records, with forced re-generation available when `regenerate=True`.

4. **Subtree Hashing & Automated Staleness Tracking:**
   - Parent headings like `4.2 Error Codes` with empty direct content recursively roll up their child tables (`4.2.table.1`) into `effective_content` and compute `subtree_hash`.
   - When `v2` is uploaded, older test cases are evaluated against the latest version. Modified sections (`3.2`, `2.1.1.1`, `4.2`, `4.2.table.1`, `4.3`) are flagged with `stale = true` and `stale_reason = "source_modified"`, while untouched sections (`1.1`, `3.1`, `6.1`) remain clean.

5. **Planted Inconsistencies Auditing & Relational Cross-References:**
   - Detects the safety-critical timing contradiction between Section 4.1 (2.0s valve vent) and Section 4.2 Table E3 (1.5s auto-deflation in v2).
   - Catches the foreign vendor anomaly in Section 6.2 citing "Anthropic" for CardioTrack recalibration.
   - Extracts and validates Section 3.3 cross-references to Sections 2.1 and 4.3 as relational graph edges.

6. **Comprehensive Automated Test Suite (15 Tests Passing):**
   - Covers parent-child rollup, subtree staleness, planted inconsistencies, cross-references, empty PDFs, skipped levels, list collisions, ragged tables, and duplicate headings.

The system runs completely offline out-of-the-box (with a deterministic local synthesizer) or with external LLMs via `OPENAI_API_KEY`.

I look forward to discussing my architecture, design choices, and trade-offs during the technical walkthrough!

Best regards,

**Vamsi Inampudi**  
GitHub: [@Vamsi112-max](https://github.com/Vamsi112-max)  
Email: vamsiinampudi01@gmail.com
