# Decision Log — CT-200 Engineering Choices & Trade-offs
**Author:** Vamsi Inampudi ([@Vamsi112-max](https://github.com/Vamsi112-max))

The assignment prompt specifically asked for direct answers to three questions representing my own reasoning rather than textbook answers. Here is my honest assessment of this system:

---

### Question 1: What's the one part of this system most likely to silently give wrong results without erroring? How would you catch it?

**My Answer:**  
The parent-child section nesting logic inside `app/services/parser.py`. 

When a manual has an unnumbered heading or a heading styled with unusual font formatting (for example, a bold sub-header that happens to be rendered at 11.5pt instead of 12pt), my parser won't classify it as a new section. Instead, it will append all of that section's paragraphs and tables to whatever previous section was active. 

The API won't throw any exceptions. It returns a clean HTTP 200, saves everything to SQLite, and looks completely fine in Swagger UI. But behind the scenes, content is silently parented to the wrong section.

**How I would catch it in production:**
1. **Automated Structural Invariant Checks:** I'd write a linting pass that flags anomalies, such as a child section having 10x more text than its parent or a section spanning 4 pages without any sub-headings.
2. **Visual Bounding-Box Overlay:** I'd generate an annotated debug PDF where detected headings are boxed in green, body text in blue, and tables in red, with their assigned section keys printed in the margin. Doing a quick visual pass on that overlay makes mis-parented text stand out immediately.

---

### Question 2: Where did you choose simplicity over correctness because of time, and what would break first if this went to production as-is?

**My Answer:**  
The cross-version node matching strategy.

To compare v1 and v2, I match nodes strictly by their section number key (`node_key`, e.g. `3.2` in v1 matches `3.2` in v2). This was fast to implement and works well on the provided sample documents where section numbering is stable.

**What would break first in production:**  
Section renumbering. In the real world, manual revisions often add or remove a section early on. For example, if someone adds a new section `2.2 Bluetooth Setup` between `2.1` and `2.2`, every single section after it shifts down by one (`2.2` becomes `2.3`, `2.3` becomes `2.4`, etc.).

My current diffing logic would look at the renumbered document and conclude that *every single section after 2.1 was deleted and replaced by a completely new section*. It would flag all existing test cases as `source_deleted`, completely wiping out QA traceability even though 99% of the text was unchanged.

**What should be done instead:**  
A bipartite graph matcher that pairs sections across versions using a weighted combination of title similarity (Levenshtein distance) and body text cosine similarity (using text embeddings). That way, even if a section moves from `2.2` to `2.3`, the system recognizes it as the same logical node.

---

### Question 3: Name one input (to your parser, your versioning matcher, or your LLM call) that you did not handle, and what your system does when it sees it.

**My Answer:**  
Multi-column page layouts (like a 2-column specifications page or side-by-side callout boxes).

**What the system does when it sees it:**  
In `app/services/parser.py`, I sort all text blocks on a page purely by their vertical coordinate ($y_0$) from top to bottom across the entire page width. 

If you feed it a two-column page, it won't read down Column 1 and then down Column 2. Instead, it will read horizontally across both columns line by line. The resulting string is a scrambled mess where sentences from Column 1 are sliced into sentences from Column 2. 

The parser doesn't crash; it hashes the garbled text, stores it with HTTP 200, and sends it directly to the LLM. The LLM then receives nonsensical half-sentences, which either causes Pydantic schema validation to fail or leads to hallucinated test cases.

---

### Additional Design Decisions

#### 4. PyMuPDF directly in Python vs External Markdown CLI
I bypassed external Markdown converters and used PyMuPDF directly. Markdown strips out font sizes and boldness flags. Without font metadata, disambiguating the Page 3 clinical list (`1. Normal...`) from Chapter 1 (`1. Device Overview`) is essentially impossible.

#### 5. Selection API & Idempotent Generation Policy
I chose to store version-pinned selections and enforce **idempotent cache returns** when `regenerate=False`. If a QA engineer triggers generation on the same selection twice by mistake, returning the existing test cases saves LLM token spend and keeps test case IDs stable across test runs. If they really want new ideas, passing `regenerate=True` forces a new LLM generation.

#### 6. SQLite with JSON Storage vs Remote MongoDB
I chose SQLite with SQLAlchemy and JSON array fields instead of spinning up MongoDB Atlas. This guarantees zero network setup friction for whoever is evaluating this submission, provides ACID transaction guarantees when updating staleness across tables, and keeps the entire setup self-contained in a single repo.

#### 7. Bottom-Up Content Roll-Up & Subtree Hashing vs Flat Leaf Nodes
When a chapter heading like `4.2 Error Codes` has an immediate table (`4.2.table.1`) and no direct paragraph text, a naive node extractor leaves `4.2` with an empty string and a static hash. 
I chose to compute both `effective_content` (bottom-up aggregation of all descendant tables and subsections) and `subtree_hash`. This ensures that downstream test generators receive rich context when selecting high-level chapters, and test cases pinned to parent headings correctly flag staleness when any nested child row changes in future manual revisions.

#### 8. Automated Anomaly Auditing for Planted Inconsistencies
Instead of treating PDF text as infallible, I added domain-specific inconsistency checks for the three planted anomalies in the CardioTrack manual:
1. **Timing Contradiction**: Flags the mismatch between Section 4.1 (2.0s emergency relief venting) and Section 4.2 Table E3 (1.5s auto-deflation).
2. **Vendor Anomaly**: Flags the presence of "Anthropic" in Section 6.2 calibration recommendations for a CardioTrack device.
3. **Relational Cross-References**: Extracts cited section links from Section 3.3 (`2.1, 4.3`) into relational graph edges, validating that cited sections actually exist in the manual hierarchy.
