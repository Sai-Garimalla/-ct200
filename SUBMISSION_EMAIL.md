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
* **README:** https://github.com/Vamsi112-max/ct200-document-intelligence/blob/main/README.md

---

### Project Summary & Engineering Highlights

1. **Direct PDF Parsing without Markdown Intermediate:**
   - Works directly with raw PDF manuals (`sample_data_ct200_manual.pdf` and `v2`) using PyMuPDF.
   - Solved the Page 3 clinical list collision (`1. Normal...`, `2. Elevated...` under Section 3.3) by inspecting font span metadata (point size, bold flags) rather than pure regex.
   - Handled skipped hierarchical levels (e.g. `2.1.1.1` connecting to `2.1`), decomposed embedded Unicode ligatures (`\ufb01` -> `fi`), and extracted structured tables into clean Markdown attached to their parent sections.

2. **Cross-Version Section Matching (`app/services/matcher.py`):**
   - Matches sections across document revisions using normalized title similarity (Levenshtein ratio) and token Jaccard content similarity.
   - Resolves renumbered sections (e.g. Section `2.1` becoming `2.2` when an earlier section is inserted) without losing historical continuity.
   - Explicitly suppresses false matches between unrelated sections sharing short identical boilerplate text.

3. **Parent-Child Content Roll-Up & Subtree Hashing:**
   - Parent headings like `4.2 Error Codes` with empty direct content recursively roll up their child tables (`4.2.table.1`) into `effective_content` and compute `subtree_hash`.
   - Modifying child tables in `v2` properly updates the parent's subtree hash, marking historical test cases as `stale = true` (`source_modified`).

4. **Generalized Inconsistency Detection & Cross-References:**
   - Scans arbitrary sections for statements matching `"within N seconds"`, clusters them by target action domain, and detects conflicting operational specifications.
   - Automatically catches the planted CT-200 contradiction: Section 4.1 specifies emergency valve venting within 2.0s, whereas Section 4.2 Error Codes Table (Row E3) specifies auto-deflation within 1.5s in v2.
   - Detects the foreign vendor anomaly in Section 6.2 citing "Anthropic" for CardioTrack recalibration.
   - Captures and validates Section 3.3 cross-references to Sections 2.1 and 4.3 as relational graph edges.

5. **Version-Pinned Selection API & Strict Count Validation:**
   - `POST /selections` freezes source text against immutable `version_id`s.
   - LLM generation validates that the returned test case count *strictly equals* the requested count (`len(test_cases) == count`), retrying with corrective feedback upon mismatch.
   - Implements an explicit idempotency policy: cache retrieval by default (`regenerate=False`), with forced re-generation available when `regenerate=True`.

6. **Automated Test Suite (30 Tests Passing):**
   - Covers section matching edge cases, generalized timing contradictions, parent-child rollup, subtree staleness, planted inconsistencies, trailing dot preservation, exact count enforcement (1, 3, 5), OCR fallback handling, empty PDFs, skipped levels, and duplicate headings.

The service runs completely offline out-of-the-box (via a deterministic local synthesizer) or with external LLMs (OpenAI, Gemini via OpenAI-compatible endpoint, or Ollama).

I look forward to discussing my architecture, design choices, and trade-offs during the technical walkthrough!

Best regards,

**Vamsi Inampudi**  
GitHub: [@Vamsi112-max](https://github.com/Vamsi112-max)  
Email: vamsiinampudi01@gmail.com
