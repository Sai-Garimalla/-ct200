# CT-200 Document Intelligence & QA Traceability — Engineering Approach
**Author:** Vamsi Inampudi ([@Vamsi112-max](https://github.com/Vamsi112-max))  
**Assignment:** Tri9T AI Engineering Internship  

---

## 1. How I Approached the Problem

When I read the update notice telling us to work directly with the raw PDF manuals rather than pre-converted Markdown, I opened up `sample_data_ct200_manual.pdf` in both a regular viewer and an internal PDF inspector. 

Medical manuals aren't blog posts. They have tight layout quirks: multi-level nested numbering (`2.1.1.1`), clinical charts embedded right inside paragraphs, tables of error codes with specific safety behaviors, and typographic ligatures. 

My goal from day one was simple: **don't build a brittle regex hack that only works on the first page, and don't build an over-engineered black box that silently eats text.** Every piece of content in the manual had to land in the right spot in the hierarchy with a verifiable content hash so that downstream QA test cases wouldn't lose traceability when the manual was bumped to v2.

---

## 2. Extraction Pipeline & Real Layout Battles

I initially tested a few standard PDF extraction libraries (`pypdf`, `pdfplumber`, and PyMuPDF). I chose **PyMuPDF (`fitz`)** because it gives raw span-level dictionaries containing exact font sizes, font family names, and bold/italic flags. Without font metadata, you can't tell the difference between a real section heading and a bullet point in this manual.

Here are the specific layout issues I ran into while inspecting the PDF and how I solved them:

### Issue 1: Page 3 Numbered List vs. Chapter Heading Collision
This was the nastiest bug in my initial prototype. Under Section `3.3 Result Display and Classification`, there is a clinical guidance list:
```text
1. Normal: systolic < 120 and diastolic < 80
2. Elevated: systolic 120–129 and diastolic < 80
3. Hypertension Stage 1: systolic 130–139 or diastolic 80–89
4. Hypertension Stage 2: systolic ≥ 140 or diastolic ≥ 90
5. Hypertensive Crisis: systolic > 180 or diastolic > 120
```
My first naive regex `^\d+(\.\d+)*\s+` matched `1.`, `2.`, `3.`, `4.`, and `5.` as top-level chapters! It literally wiped out Chapter 1 (`Device Overview`) and Chapter 4 (`Alarms and Safety Behavior`) in my database because it thought "Normal: systolic..." was Chapter 1.

**How I fixed it:** I looked at PyMuPDF's font spans. The actual chapter headings use `NimbusSans-Bold` at 16.5pt or 12.9pt. The list items on page 3 use `NimbusSans-Regular` at 11.0pt, and they have colons (`:`) right after the category title. I wrote a compound heuristic in `app/services/parser.py`:
- If a line starts with a sub-number (like `1.1` or `2.1.1.1`), it's automatically a section heading.
- If it's a single digit (`1.`, `2.`), it **must** have bold styling (`is_bold == True`) or have a font size >= 12.0pt to count as a chapter. If it's regular 11pt text with a colon, it gets treated as body text under Section 3.3.

### Issue 2: Tables on Page 2 and Page 4
Section 2.1 has a General Specifications table (`Parameter | Value`), and Section 4.2 has a critical Error Codes table (`Code | Meaning | Device Behavior`). If you extract these as raw text, the columns turn into spaghetti lines where error codes get separated from their device cutoff behaviors.

**How I fixed it:** I used PyMuPDF's `page.find_tables()` to detect table grids, convert them to standard GitHub-style Markdown tables, and track their bounding boxes (`bbox`). Then, when processing raw text blocks, I skip any text block that overlaps with a table's bounding box so table text isn't duplicated. I sort both text blocks and tables on each page by vertical coordinate ($y_0$), which naturally attaches each table to the heading right above it (`2.1` and `4.2`).

### Issue 3: Unicode Ligatures and Terminal Crashes
The font embedded in the PDF uses typographic ligatures for letter combinations:
- `\ufb01` for "fi" (e.g. `Speciﬁcations`, `Deﬂation`)
- `\ufb02` for "fl" (`Inﬂation`)
- Non-standard dashes like `\u2011` (non-breaking hyphen) and `\u2013` (en-dash).

On my Windows machine, printing these raw strings to the terminal threw `UnicodeEncodeError: 'charmap' codec can't encode character '\ufb01'`. Even worse, searching for `"Specifications"` via the API returned nothing because the database had the unicode ligature.

**How I fixed it:** I wrote a `normalize_text()` helper that runs `unicodedata.normalize("NFKD", text)` on everything. It decomposes compatibility ligatures into plain ASCII characters (`fi`, `fl`, standard hyphens, straight quotes).

### Issue 4: Skipped Level in Section 2 (`2.1.1.1`)
In Section 2, the document jumps straight from `2.1 General Specifications` to `2.1.1.1 Battery Life Under Typical Use`. There is no `2.1.1` header anywhere on the page. A strict hierarchy parser expecting `N.N.N` before `N.N.N.N` would get stuck or leave `2.1.1.1` dangling without a parent.

**How I fixed it:** I implemented an ancestor backtracking loop in `parser.py`. When looking for a parent, the parser splits the section key and trims from right to left (`2.1.1.1` -> `2.1.1` -> `2.1`). It checks its list of known headings and finds `2.1`, cleanly assigning `2.1` as the parent.

### Issue 5: Duplicate Headings Disambiguation
In real manuals, you often get duplicate headings like `1.1 General Precautions` under Safety and `2.1 General Precautions` under Maintenance, or even duplicate section numbers due to drafting errors. If you key your database strictly by raw section number without collision handling, a second `1.1` will overwrite the first one.

**How I fixed it:** `parser.py` maintains a frequency map of heading numbers. If a section number repeats, it appends a disambiguation suffix (`1.1#2`) and links to the most recent matching ancestor in the hierarchy.

### Issue 6: Parent Headings With Empty Content (The Node 4.2 Error Codes Trap)
This was a subtle architectural trap in technical manuals. Section `4.2 Error Codes` has a heading followed immediately by a Markdown table (`4.2.table.1`). The heading node itself had zero direct paragraph text (`content = ""`, `content_hash = digest("")`).
If you naive-select node `4.2` for test generation, the generator receives an empty string! Even worse, when `v2` modified error codes E3 and E6 in the table, `4.2`'s direct hash remained `digest("")`, meaning staleness checks on `4.2` would falsely claim the section was "unchanged".

**How I fixed it:** I implemented bottom-up content roll-up (`rollup_subtree_content`). Each node computes both its direct `content` and an `effective_content` that recursively aggregates all child tables, sub-paragraphs, and child sections. Alongside this, I introduced `subtree_hash`, which hashes the entire subtree. When a user selects `4.2`, the generator receives the full child error table. And when `v2` modifies rows in `4.2.table.1`, `4.2`'s `subtree_hash` changes, correctly flagging tests generated on `4.2` as `stale = true` (`source_modified`).

### Issue 7: Trailing Dots in Section Headings (`1.` vs `1`)
In earlier regex iterations, stripping trailing punctuation turned `1. Device Overview` into `1 Device Overview`. While minor, medical compliance audits require exact fidelity to official manual typography.
I updated the parser regex to `^(\d+(?:\.\d+)*\.?)\s+(.+?)$`, capturing whether a trailing dot was present on the original chapter number and preserving it (e.g. `1. Device Overview` retains the dot, while `1.1 Intended Use` retains its clean dotless sub-header style).

### Issue 8: Adaptive Font Size Thresholds (Beyond Hardcoded 12.0pt)
In my initial prototype, single-digit headings were distinguished from body text using a fixed `max_size >= 12.0` threshold. While this worked for the CT-200 sample PDF, other medical manuals might use 9pt or 14pt body text. 
I updated `parser.py` to calculate a dynamic page-level median body font size (`median_font_size`). Headings are detected using an adaptive ratio threshold `max(11.8, median_font_size * 1.15)`. This ensures that even if a document is formatted with smaller or larger base typography, chapter headings scale dynamically without manual re-tuning.

---

## 3. Data Model & Architecture Decisions

I went with **FastAPI**, **SQLAlchemy 2.0**, and **SQLite**. 

### Why SQLite with JSON Columns instead of MongoDB Atlas?
The prompt suggested MongoDB Atlas or a well-justified JSON store. I decided against MongoDB Atlas for three practical reasons:
1. **Zero setup friction for the reviewer:** I wanted anyone grading this assignment to be able to clone the repo, run `pip install -r requirements.txt`, and immediately run `pytest` without having to configure MongoDB connection strings, whitelist their IP on Atlas, or deal with network firewalls.
2. **ACID Transaction Integrity:** When someone re-ingests a manual or runs staleness updates, we are updating versions, nodes, selections, and test cases in one go. Relational foreign keys keep everything strictly tied together.
3. **NoSQL flexibility where it actually counts:** The generated test case steps (`steps: list[str]`) and selection node references are stored as structured JSON arrays inside SQLite. It gives the document-store benefits without the operational baggage of running a separate database server.

### Schema Structure
- **`DocumentVersion`**: Tracks the ingested document (`document_key="ct200"`), version number (monotonic integer: 1, 2, ...), original filename, whole-file content hash, and timestamp.
- **`DocumentNode`**: Stores every section, heading, table, and preamble. Contains `node_key`, `parent_key`, `title`, `level`, `content`, `content_hash`, and `page`.
- **`Selection`**: Represents a named group of sections pinned to a specific document version (`version_id`). It stores both the node IDs and keys so that historical selections stay frozen even if new manual revisions are uploaded later.
- **`TestCase`**: Holds generated QA test cases. Pinned to `version_id`, `node_key`, and the exact `selected_hash` from which it was created. Also tracks `stale` (boolean) and `stale_reason`.

---

## 4. Browse API & Selection API

### Browse Endpoints
- `GET /sections/top-level`: Returns top-level sections (level 1). If you don't pass a version parameter, it automatically looks up the highest version available.
- `GET /documents/{version_id}/tree`: Dumps the full parsed tree for that version.
- `GET /nodes/{node_id}`: Fetches a single node by ID and includes its immediate `children` array so the caller can traverse downward.
- `GET /nodes/{node_id}/changes`: Looks at the node's pinned version vs. the latest version in the system and returns whether that specific section was modified, deleted, or left untouched, along with a quick diff summary.
- `GET /documents/{v1}/diff/{v2}`: High-level version comparison returning `added`, `removed`, and `changed` sections.

### Selection API & The Duplicate Policy
The prompt asked to build a version-pinned selection mechanism and decide what happens when the same selection is submitted twice.

Here is how I designed it:
1. When a client calls `POST /selections`, they send a `name`, a `version_id`, and a list of node IDs or keys. The backend resolves the exact text of those nodes *at that specific version* and stores the selection.
2. In `POST /test-cases/generate`, the client passes the `selection_id`.
3. **My Duplicate Submission Policy:**
   - If someone submits the same `selection_id` again with `regenerate=False` (the default), the API acts **idempotently** and returns the previously generated test cases immediately. 
   - *Why?* In QA environments, teams frequently refresh dashboards or re-trigger generation scripts. Repeatedly burning tokens on identical selections wastes money and creates duplicate test case records in the database.
   - If the user genuinely wants fresh ideas, they can pass `"regenerate": true`, and the system will run another LLM generation batch.

---

## 5. QA Generation, Prompt Design & Staleness

### LLM Integration & Retry Loop
The generator is in `app/services/qa_generator.py`. 
- When an API key (`OPENAI_API_KEY` or `GEMINI_API_KEY`) is present, it formats the selected section text into a strict prompt demanding numerical pass/fail thresholds, error codes, and source quotations.
- The output is validated against the `QAGenerationResponse` Pydantic model.
- If the LLM returns broken JSON or misses required fields, the system doesn't just crash. It feeds the validation error back into the conversation context and retries up to 3 times.
- **Offline Fallback:** If no API key is configured (or if all 3 retries fail), the system calls `generate_local_grounded_cases()`. This is a deterministic synthesizer that parses numbers, timings, error codes, and tables directly from the selected sections to build valid test cases. This guarantees that any evaluator can test the entire pipeline end-to-end without needing an external API key.

### Staleness Tracking & The Honest Limits of Hashing
When `v2` of the manual is uploaded, `update_all_test_cases_staleness()` runs across all prior test cases:
- If the section was removed in v2: `stale = true`, `stale_reason = "source_deleted"`.
- If the section exists but its content hash changed: `stale = true`, `stale_reason = "source_modified"`.
- If the hash is identical: `stale = false`, `stale_reason = null`.

### The Limits of My Approach (Let's Be Honest)
I use SHA-256 over canonical normalized text (`" ".join(text.split())`). Collapsing whitespace prevents pagination shifts (like Section 5.3 pushing Section 6 to the next page) from triggering false diffs. 

**However, SHA-256 is syntactically binary:**
- If someone fixes a minor spelling typo ("deﬂation" to "deflation"), the hash changes. The system marks the test case as `source_modified`.
- If someone changes a safety-critical pressure limit from `280 mmHg` to `300 mmHg`, the hash also changes. The system marks the test case as `source_modified`.
- Both are treated identically by a hash comparison, even though one is cosmetic and the other is a critical safety redesign.

In a production medical device system, I would not rely solely on hashing. I would parse numerical entities and units (`[number, unit]`) into an AST and run semantic embeddings to separate cosmetic wording tweaks from specification limit changes.

---

## 6. Planted Inconsistencies Auditing & Relational Cross-References

While analyzing the v1 and v2 manual texts, I discovered three deliberately planted anomalies embedded in the CardioTrack documentation. Rather than ignoring them or letting them cause silent downstream test contradictions, I built an automated domain intelligence audit service in `app/services/inconsistency_detector.py` and exposed it via `GET /documents/{version_id}/inconsistencies` and `GET /documents/{version_id}/cross-references`:

### 1. Safety-Critical Timing Contradiction (Section 4.1 vs. Section 4.2 Table E3)
- In `v2`, Section 4.1 (*Overpressure Protection*) states:
  > "...the device immediately triggers an emergency deflation valve, halting inflation and venting the cuff within 2 seconds, independent of the main firmware control loop."
- However, in the Section 4.2 Error Codes table, the row for Code **E3** (*Overpressure condition*) in `v2` was updated to state:
  > "Auto-deflates within 1.5 seconds, displays E3"
- **The Issue:** A regulatory QA engineer generating tests from Section 4.1 expects a 2.0-second pass criterion, whereas an engineer testing Error Code E3 from Section 4.2 expects 1.5 seconds. The inconsistency detector catches this threshold delta ($2.0s \neq 1.5s$) and flags it as a `timing_contradiction` of `severity="high"`.

### 2. Foreign Vendor Anomaly (Anthropic in Section 6.2)
- In Section 6.2 (*Calibration*), both v1 and v2 contain this sentence:
  > "Anthropic recommends professional recalibration every 2 years or after 10,000 cycles, whichever comes first."
- **The Issue:** CardioTrack Technologies Inc. is the manufacturer of the CT-200. "Anthropic" is an AI research company, not a medical device OEM! This is an authentic Easter egg or LLM-assisted drafting artifact. The detector inspects manufacturer recommendation sentences for non-OEM vendor names (`anthropic`, `openai`, `deepmind`) and flags it as a `vendor_brand_anomaly` with `severity="high"`.

### 3. Cross-Reference Graph Linkage (Section 3.3 -> 2.1, 4.3)
- In Section 3.3 (*Result Display and Classification*), the body text includes:
  > "...classification indicator (see 2.1, 4.3 for related specifications and alarm thresholds)"
- **The Issue:** Isolated section parsing loses relational context. My parser extracts cited section keys into `DocumentNode.cross_references = ["2.1", "4.3"]`. The API verifies that both Section 2.1 (*General Specifications*) and Section 4.3 (*Alarm Thresholds*) exist in the document tree, and surfaces the relational graph edges at `GET /documents/{version_id}/cross-references`.

---

## 7. What I Would Do Differently With More Time

1. **Bipartite Matching for Renumbered Sections:** Right now, sections match across versions primarily by their hierarchical key (`3.2` -> `3.2`). As I discuss in `DECISIONS.md`, if an author inserts a new `2.2` and shifts all following sections down by one, node-key-only alignment treats subsequent sections as deleted/added. In production, I would use a bipartite graph matcher combining Levenshtein title similarity and embedding cosine similarity to track sections across arbitrary renumbering.
2. **True Multi-Column Layout Handling:** PyMuPDF sorts blocks by vertical $y_0$ coordinate. On a complex two-column page, it reads across columns horizontally line-by-line instead of reading down column 1 first. I would implement horizontal bounding box clustering to segment columns before sorting.
3. **OCR Pre-Processing:** For scanned bitmap PDFs, I'd integrate an OCR pass with Tesseract before handing the document to the hierarchy builder.
