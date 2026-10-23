# Submission Email Draft

**Subject:** Tri9T AI Internship Assignment Submission — CT-200 Document Intelligence — [Your Name]

**To:** hiring@tri9t.ai (or the designated submission email)

---

Hi Tri9T AI Engineering Team,

Please find my submission for the **AI Engineering Internship Assignment — CT-200 Document Intelligence & QA Traceability**.

### Repository & Documentation Links
* **GitHub Repository:** `[Insert Your GitHub Repository URL Here]`
* **Engineering Approach Document:** `[Insert Link to APPROACH.md on GitHub]`
* **Decision Log:** `[Insert Link to DECISIONS.md on GitHub]`

---

### Key Highlights of the Implementation

1. **Direct PDF Parsing without Markdown Intermediate:**
   - Evaluated `sample_data_ct200_manual.pdf` and resolved the Page 3 clinical list collision (`1. Normal...`, `2. Elevated...` under Section 3.3) by utilizing PyMuPDF typography metadata (point size, bold flags) rather than pure regex.
   - Handled skipped hierarchical levels (e.g. `2.1.1.1` connecting to `2.1`), decomposed embedded Unicode ligatures (`\ufb01` -> `fi`), and extracted structured tables into Markdown.
2. **Document Versioning & Canonical Whitespace Invariance:**
   - Ingesting `v2` creates a discrete new revision while preserving `v1`. Content hashes are whitespace-collapsed, ensuring pagination shifts (e.g. Section 5.3 pushing Section 6 onto new pages) do not trigger false diffs.
3. **Version-Pinned Selection API & Idempotent Generation:**
   - Implemented `POST /selections` and `GET /selections/{id}` to freeze source text against immutable `version_id`s.
   - Enforced an explicit duplicate selection policy: idempotent cache retrieval by default (`regenerate=False`), with forced re-generation on demand (`regenerate=True`).
4. **Automated Staleness & Impact Tracking:**
   - When `v2` is uploaded, older test cases are evaluated against the latest version. Modified sections (`3.2`, `2.1.1.1`, `4.2.table.1`, `4.3`) are flagged with `stale = true` and `stale_reason = "source_modified"`, while untouched sections (`1.1`, `3.1`, `6.1`) remain clean.
5. **Robust Test Suite (12 Tests Passing):**
   - Includes 5 explicit irregularity tests covering empty PDFs, skipped levels, list collisions, ragged tables, and duplicate headings, along with end-to-end integration testing.

The system runs completely offline out-of-the-box (with an offline grounded synthesizer) or with external LLMs via `OPENAI_API_KEY`.

I look forward to discussing the architecture, design choices, and trade-offs during the technical walkthrough!

Best regards,

**[Your Name]**  
[Your Phone Number]  
[Your LinkedIn Profile]  
[Your Portfolio / Website]
