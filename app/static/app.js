document.addEventListener("DOMContentLoaded", () => {
  // Global state
  let allVersions = [];
  let currentVersionId = null;
  let currentNodes = [];
  let selectedNode = null;
  let activeTypeFilter = "all";

  // Elements
  const tabButtons = document.querySelectorAll(".nav-tab");
  const tabPanes = document.querySelectorAll(".tab-pane");

  // Initialize
  initTabs();
  initUpload();
  initExplorer();
  initDiff();
  initQA();
  loadAllVersions();

  // ----------------------------------------------------
  // 1. Tab Navigation
  // ----------------------------------------------------
  function initTabs() {
    tabButtons.forEach(btn => {
      btn.addEventListener("click", () => {
        const targetTabId = btn.dataset.tab;
        tabButtons.forEach(b => b.classList.remove("active"));
        tabPanes.forEach(p => p.classList.remove("active"));

        btn.classList.add("active");
        const activePane = document.getElementById(targetTabId);
        if (activePane) activePane.classList.add("active");

        // Refresh tab-specific views if needed
        if (targetTabId === "explorer-tab" && !currentVersionId && allVersions.length > 0) {
          selectVersionInExplorer(allVersions[allVersions.length - 1].id);
        } else if (targetTabId === "diff-tab") {
          updateDiffSelectors();
        } else if (targetTabId === "qa-tab") {
          loadTestCases();
        }
      });
    });
  }

  function switchToTab(tabId) {
    const btn = document.querySelector(`.nav-tab[data-tab="${tabId}"]`);
    if (btn) btn.click();
  }

  // ----------------------------------------------------
  // Fetch Versions
  // ----------------------------------------------------
  async function loadAllVersions() {
    try {
      const res = await fetch("/documents");
      if (!res.ok) throw new Error("Failed to load document versions");
      allVersions = await res.json();
      
      populateVersionDropdowns();
      if (allVersions.length > 0 && !currentVersionId) {
        currentVersionId = allVersions[allVersions.length - 1].id;
        loadTreeForVersion(currentVersionId);
      }
    } catch (err) {
      console.error(err);
    }
  }

  function populateVersionDropdowns() {
    const explorerSelect = document.getElementById("explorer-version-select");
    const diffV1Select = document.getElementById("diff-v1-select");
    const diffV2Select = document.getElementById("diff-v2-select");
    const qaSelect = document.getElementById("qa-version-select");
    const filterQaVersion = document.getElementById("filter-qa-version");

    const optionsHtml = allVersions.map(v => 
      `<option value="${v.id}">Version ${v.version} — ${v.filename} (${v.node_count || 0} nodes)</option>`
    ).join("");

    if (explorerSelect) {
      explorerSelect.innerHTML = optionsHtml || '<option value="">No documents ingested yet</option>';
      if (currentVersionId) explorerSelect.value = currentVersionId;
    }

    if (diffV1Select) {
      diffV1Select.innerHTML = optionsHtml;
      if (allVersions.length >= 2) diffV1Select.value = allVersions[0].id;
    }

    if (diffV2Select) {
      diffV2Select.innerHTML = optionsHtml;
      if (allVersions.length >= 2) diffV2Select.value = allVersions[allVersions.length - 1].id;
    }

    if (qaSelect) {
      qaSelect.innerHTML = optionsHtml;
      if (currentVersionId) qaSelect.value = currentVersionId;
    }

    if (filterQaVersion) {
      const prevVal = filterQaVersion.value || "all";
      const filterOpts = `<option value="all">All Document Versions</option>` + allVersions.map(v => 
        `<option value="${v.id}">Version ${v.version} — ${v.filename}</option>`
      ).join("");
      filterQaVersion.innerHTML = filterOpts;
      if (prevVal && filterQaVersion.querySelector(`option[value="${prevVal}"]`)) {
        filterQaVersion.value = prevVal;
      }
    }
  }

  // ----------------------------------------------------
  // 2. Upload Logic
  // ----------------------------------------------------
  function initUpload() {
    const fileInput = document.getElementById("file-input");
    const dropzone = document.getElementById("dropzone-box");
    const selectedFileLabel = document.getElementById("selected-file-label");
    const uploadBtn = document.getElementById("upload-btn");
    const uploadForm = document.getElementById("upload-form");
    const docKeyInput = document.getElementById("doc-key-input");
    const uploadSpinner = document.getElementById("upload-spinner");
    const viewInExplorerBtn = document.getElementById("view-in-explorer-btn");

    dropzone.addEventListener("click", () => fileInput.click());

    dropzone.addEventListener("dragover", (e) => {
      e.preventDefault();
      dropzone.classList.add("drag-over");
    });

    dropzone.addEventListener("dragleave", () => dropzone.classList.remove("drag-over"));

    dropzone.addEventListener("drop", (e) => {
      e.preventDefault();
      dropzone.classList.remove("drag-over");
      if (e.dataTransfer.files && e.dataTransfer.files.length > 0) {
        fileInput.files = e.dataTransfer.files;
        handleFileSelected(fileInput.files[0]);
      }
    });

    fileInput.addEventListener("change", () => {
      if (fileInput.files && fileInput.files.length > 0) {
        handleFileSelected(fileInput.files[0]);
      }
    });

    function handleFileSelected(file) {
      if (file && file.name.toLowerCase().endsWith(".pdf")) {
        selectedFileLabel.textContent = `Selected: ${file.name} (${(file.size / 1024).toFixed(1)} KB)`;
        selectedFileLabel.classList.remove("hidden");
        uploadBtn.disabled = false;
      } else {
        alert("Please select a valid PDF file.");
        fileInput.value = "";
        selectedFileLabel.textContent = "";
        uploadBtn.disabled = true;
      }
    }

    uploadForm.addEventListener("submit", async (e) => {
      e.preventDefault();
      const file = fileInput.files[0];
      if (!file) return;

      const docKey = docKeyInput.value.trim() || "ct200";
      const formData = new FormData();
      formData.append("file", file);

      uploadBtn.disabled = true;
      uploadSpinner.classList.remove("hidden");

      try {
        const res = await fetch(`/documents/ingest?document_key=${encodeURIComponent(docKey)}`, {
          method: "POST",
          body: formData
        });

        if (!res.ok) {
          const errData = await res.json();
          throw new Error(errData.detail || "Failed to upload document");
        }

        const data = await res.json();
        displayUploadResult(data);
        await loadAllVersions();
      } catch (err) {
        alert(`Upload error: ${err.message}`);
      } finally {
        uploadBtn.disabled = false;
        uploadSpinner.classList.add("hidden");
      }
    });

    viewInExplorerBtn.addEventListener("click", () => {
      const vId = viewInExplorerBtn.dataset.versionId;
      if (vId) selectVersionInExplorer(vId);
    });
  }

  async function displayUploadResult(versionData) {
    const container = document.getElementById("upload-result-container");
    const vIdEl = document.getElementById("res-version-id");
    const vNumEl = document.getElementById("res-version-num");
    const vNodesEl = document.getElementById("res-node-count");
    const vHashEl = document.getElementById("res-file-hash");
    const viewBtn = document.getElementById("view-in-explorer-btn");
    const tbody = document.getElementById("upload-preview-tbody");

    container.classList.remove("hidden");
    vIdEl.textContent = versionData.id;
    vNumEl.textContent = `v${versionData.version}`;
    vNodesEl.textContent = versionData.node_count || "-";
    vHashEl.textContent = versionData.file_hash || "-";
    viewBtn.dataset.versionId = versionData.id;

    // Fetch and show preview table rows
    try {
      const treeRes = await fetch(`/documents/${versionData.id}/tree`);
      if (treeRes.ok) {
        const nodes = await treeRes.json();
        tbody.innerHTML = nodes.slice(0, 10).map(n => `
          <tr>
            <td><code>${escapeHtml(n.node_key)}</code></td>
            <td><span class="badge badge-type">${escapeHtml(n.node_type)}</span></td>
            <td>Page ${n.page}</td>
            <td><strong>${escapeHtml(n.title)}</strong></td>
            <td class="truncate" style="max-width: 320px;">${escapeHtml(n.content ? n.content.substring(0, 80) + '...' : '(No text)')}</td>
          </tr>
        `).join("");
      }
    } catch (e) {
      console.error(e);
    }
  }

  // ----------------------------------------------------
  // 3. Document Explorer & Instant Filter Logic
  // ----------------------------------------------------
  function initExplorer() {
    const versionSelect = document.getElementById("explorer-version-select");
    const searchInput = document.getElementById("explorer-search-input");
    const clearBtn = document.getElementById("clear-search-btn");
    const typeChips = document.querySelectorAll(".type-filter-chips .chip");
    const genFromNodeBtn = document.getElementById("generate-from-node-btn");

    versionSelect.addEventListener("change", (e) => {
      const selectedId = parseInt(e.target.value);
      if (selectedId) {
        currentVersionId = selectedId;
        loadTreeForVersion(currentVersionId);
      }
    });

    // Instant real-time filtering as user types
    searchInput.addEventListener("input", () => {
      const query = searchInput.value.trim();
      if (query) {
        clearBtn.classList.remove("hidden");
      } else {
        clearBtn.classList.add("hidden");
      }
      applyExplorerFilter();
    });

    clearBtn.addEventListener("click", () => {
      searchInput.value = "";
      clearBtn.classList.add("hidden");
      applyExplorerFilter();
    });

    typeChips.forEach(chip => {
      chip.addEventListener("click", () => {
        typeChips.forEach(c => c.classList.remove("active"));
        chip.classList.add("active");
        activeTypeFilter = chip.dataset.type;
        applyExplorerFilter();
      });
    });

    genFromNodeBtn.addEventListener("click", () => {
      if (selectedNode) {
        switchToTab("qa-tab");
        const qaSecInput = document.getElementById("qa-sections-input");
        const qaVerSelect = document.getElementById("qa-version-select");
        if (qaSecInput) qaSecInput.value = selectedNode.node_key;
        if (qaVerSelect && currentVersionId) qaVerSelect.value = currentVersionId;
      }
    });
  }

  function selectVersionInExplorer(versionId) {
    switchToTab("explorer-tab");
    const versionSelect = document.getElementById("explorer-version-select");
    if (versionSelect) {
      versionSelect.value = versionId;
      currentVersionId = parseInt(versionId);
      loadTreeForVersion(currentVersionId);
    }
  }

  async function loadTreeForVersion(versionId) {
    const container = document.getElementById("tree-list-container");
    container.innerHTML = '<div class="placeholder-msg">Loading document hierarchy...</div>';
    
    // Clear detail card
    document.getElementById("detail-empty-state").classList.remove("hidden");
    document.getElementById("detail-content").classList.add("hidden");
    selectedNode = null;

    try {
      const res = await fetch(`/documents/${versionId}/tree`);
      if (!res.ok) throw new Error("Failed to load hierarchy tree");
      currentNodes = await res.json();
      applyExplorerFilter();
    } catch (err) {
      container.innerHTML = `<div class="placeholder-msg text-danger">Error: ${err.message}</div>`;
    }
  }

  function applyExplorerFilter() {
    const searchInput = document.getElementById("explorer-search-input");
    const query = (searchInput ? searchInput.value : "").trim().toLowerCase();
    const countBadge = document.getElementById("node-count-badge");
    const filterActiveLabel = document.getElementById("filter-active-label");
    const container = document.getElementById("tree-list-container");

    let filtered = currentNodes.filter(n => {
      // Type filter
      if (activeTypeFilter !== "all") {
        if (activeTypeFilter === "table" && n.node_type !== "table") return false;
        if (activeTypeFilter === "heading" && n.node_type !== "heading") return false;
      }
      // Text search filter
      if (query) {
        const inKey = n.node_key.toLowerCase().includes(query);
        const inTitle = n.title.toLowerCase().includes(query);
        const inContent = n.content.toLowerCase().includes(query);
        return inKey || inTitle || inContent;
      }
      return true;
    });

    countBadge.textContent = `${filtered.length} of ${currentNodes.length} items`;
    filterActiveLabel.textContent = query 
      ? `Filtered by: "${query}"`
      : (activeTypeFilter !== "all" ? `Filtered by ${activeTypeFilter}` : "Showing all sections");

    if (filtered.length === 0) {
      container.innerHTML = '<div class="placeholder-msg">No sections or tables match your filter.</div>';
      return;
    }

    container.innerHTML = filtered.map(node => {
      const indent = Math.min((node.level || 1) - 1, 4) * 16;
      return `
        <div class="tree-item ${selectedNode && selectedNode.id === node.id ? 'active' : ''}" 
             data-id="${node.id}" 
             style="margin-left: ${indent}px;">
          <span class="item-badge">${escapeHtml(node.node_key)}</span>
          <span class="tree-item-title" title="${escapeHtml(node.title)}">${escapeHtml(node.title)}</span>
          <span class="page-tag">P.${node.page}</span>
        </div>
      `;
    }).join("");

    // Bind click events on items
    container.querySelectorAll(".tree-item").forEach(item => {
      item.addEventListener("click", () => {
        const nId = parseInt(item.dataset.id);
        const node = currentNodes.find(n => n.id === nId);
        if (node) {
          selectNodeDetail(node);
          container.querySelectorAll(".tree-item").forEach(i => i.classList.remove("active"));
          item.classList.add("active");
        }
      });
    });

    // Auto-select first item if none selected
    if (!selectedNode && filtered.length > 0) {
      selectNodeDetail(filtered[0]);
      const firstItem = container.querySelector(".tree-item");
      if (firstItem) firstItem.classList.add("active");
    }
  }

  function selectNodeDetail(node) {
    selectedNode = node;
    document.getElementById("detail-empty-state").classList.add("hidden");
    const detailContent = document.getElementById("detail-content");
    detailContent.classList.remove("hidden");

    document.getElementById("detail-key-badge").textContent = `Key: ${node.node_key}`;
    document.getElementById("detail-type-badge").textContent = node.node_type.toUpperCase();
    document.getElementById("detail-page-badge").textContent = `Page ${node.page}`;
    document.getElementById("detail-level-badge").textContent = `Level ${node.level}`;
    document.getElementById("detail-title").textContent = node.title;
    document.getElementById("detail-hash").textContent = node.content_hash;

    const renderedDiv = document.getElementById("detail-rendered-content");
    if (node.node_type === "table" || node.content.includes("|")) {
      renderedDiv.innerHTML = `<pre><code>${escapeHtml(node.content)}</code></pre>`;
    } else {
      renderedDiv.textContent = node.content || "(This section heading introduces child subsections; no independent body text)";
    }
  }

  // ----------------------------------------------------
  // 4. Version Diff Logic
  // ----------------------------------------------------
  function initDiff() {
    const runDiffBtn = document.getElementById("run-diff-btn");
    runDiffBtn.addEventListener("click", async () => {
      const v1 = document.getElementById("diff-v1-select").value;
      const v2 = document.getElementById("diff-v2-select").value;
      if (!v1 || !v2) {
        alert("Please select both a base version and target version to compare.");
        return;
      }
      if (v1 === v2) {
        alert("Please select two different document versions to compare.");
        return;
      }

      runDiffBtn.disabled = true;
      try {
        const res = await fetch(`/documents/${v1}/diff/${v2}`);
        if (!res.ok) throw new Error("Failed to compute version diff");
        const diffData = await res.json();
        renderDiffResults(diffData);
      } catch (err) {
        alert(`Diff error: ${err.message}`);
      } finally {
        runDiffBtn.disabled = false;
      }
    });
  }

  function updateDiffSelectors() {
    const v1Select = document.getElementById("diff-v1-select");
    const v2Select = document.getElementById("diff-v2-select");
    if (allVersions.length >= 2) {
      if (v1Select) v1Select.value = allVersions[0].id;
      if (v2Select) v2Select.value = allVersions[allVersions.length - 1].id;
    }
  }

  function renderDiffResults(diff) {
    const card = document.getElementById("diff-results-card");
    card.classList.remove("hidden");

    document.getElementById("diff-added-count").textContent = diff.added.length;
    document.getElementById("diff-changed-count").textContent = diff.changed.length;
    document.getElementById("diff-removed-count").textContent = diff.removed.length;

    const addedList = document.getElementById("diff-added-list");
    addedList.innerHTML = diff.added.length > 0 
      ? diff.added.map(k => `<li>+ <strong>${escapeHtml(k)}</strong></li>`).join("")
      : '<li class="text-muted">No new sections added</li>';

    const changedTbody = document.getElementById("diff-changed-tbody");
    if (diff.changed_details && diff.changed_details.length > 0) {
      changedTbody.innerHTML = diff.changed_details.map(d => `
        <tr>
          <td><code>${escapeHtml(d.node_key)}</code></td>
          <td><strong>${escapeHtml(d.title)}</strong></td>
          <td><code class="mono truncate" style="max-width: 140px; display: block;" title="${d.from_hash}">${d.from_hash.substring(0, 16)}...</code></td>
          <td><code class="mono truncate" style="max-width: 140px; display: block;" title="${d.to_hash}">${d.to_hash.substring(0, 16)}...</code></td>
        </tr>
      `).join("");
    } else {
      changedTbody.innerHTML = `<tr><td colspan="4" class="text-muted text-center">No modified sections</td></tr>`;
    }

    const removedList = document.getElementById("diff-removed-list");
    removedList.innerHTML = diff.removed.length > 0
      ? diff.removed.map(k => `<li>- <strong>${escapeHtml(k)}</strong></li>`).join("")
      : '<li class="text-muted">No sections removed</li>';
  }

  // ----------------------------------------------------
  // 5. QA Generation & Staleness Logic
  // ----------------------------------------------------
  let allQACases = [];
  let qaShowAll = false;
  let qaVersionFilter = "all";
  let qaSearchTerm = "";

  function initQA() {
    const generateBtn = document.getElementById("generate-qa-btn");
    const qaSpinner = document.getElementById("qa-spinner");
    const refreshBtn = document.getElementById("refresh-qa-btn");
    const staleOnlyCheck = document.getElementById("filter-stale-only");
    const versionFilterSelect = document.getElementById("filter-qa-version");
    const searchFilterInput = document.getElementById("filter-qa-search");
    const toggleAllBtn = document.getElementById("qa-toggle-all-btn");

    generateBtn.addEventListener("click", async () => {
      const versionId = parseInt(document.getElementById("qa-version-select").value);
      const sectionsRaw = document.getElementById("qa-sections-input").value;
      const count = parseInt(document.getElementById("qa-count-input").value) || 3;

      const nodeKeys = sectionsRaw.split(",").map(s => s.trim()).filter(Boolean);
      if (!versionId || nodeKeys.length === 0) {
        alert("Please enter at least one valid section key (e.g. 3.2, 4.1, 1.table.1).");
        return;
      }

      generateBtn.disabled = true;
      qaSpinner.classList.remove("hidden");

      try {
        const res = await fetch("/test-cases/generate", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            version_id: versionId,
            node_keys: nodeKeys,
            count: count
          })
        });

        if (!res.ok) {
          const errData = await res.json();
          throw new Error(errData.detail || "Failed to generate test cases");
        }

        // When generating, sync version filter so user sees the newly generated cases immediately
        if (versionFilterSelect) {
          versionFilterSelect.value = String(versionId);
          qaVersionFilter = String(versionId);
        }
        await loadTestCases();
      } catch (err) {
        alert(`Generation error: ${err.message}`);
      } finally {
        generateBtn.disabled = false;
        qaSpinner.classList.add("hidden");
      }
    });

    if (refreshBtn) {
      refreshBtn.addEventListener("click", () => loadTestCases());
    }

    if (staleOnlyCheck) {
      staleOnlyCheck.addEventListener("change", () => renderQACases());
    }

    if (versionFilterSelect) {
      versionFilterSelect.addEventListener("change", () => {
        qaVersionFilter = versionFilterSelect.value;
        renderQACases();
      });
    }

    if (searchFilterInput) {
      searchFilterInput.addEventListener("input", () => {
        qaSearchTerm = searchFilterInput.value.trim().toLowerCase();
        renderQACases();
      });
    }

    if (toggleAllBtn) {
      toggleAllBtn.addEventListener("click", () => {
        qaShowAll = !qaShowAll;
        renderQACases();
      });
    }
  }

  async function loadTestCases() {
    const container = document.getElementById("qa-cases-container");
    container.innerHTML = '<div class="placeholder-msg">Loading test cases...</div>';

    try {
      const res = await fetch("/test-cases");
      if (!res.ok) throw new Error("Failed to load test cases");
      allQACases = await res.json();
      // Reverse order so newly generated test cases appear at the top
      allQACases.reverse();
      renderQACases();
    } catch (err) {
      container.innerHTML = `<div class="placeholder-msg text-danger">Error: ${err.message}</div>`;
    }
  }

  function renderQACases() {
    const container = document.getElementById("qa-cases-container");
    const countInfo = document.getElementById("qa-count-info");
    const toggleAllBtn = document.getElementById("qa-toggle-all-btn");
    const toggleAllText = document.getElementById("qa-toggle-all-text");
    const staleOnlyCheck = document.getElementById("filter-stale-only");
    const staleOnly = staleOnlyCheck ? staleOnlyCheck.checked : false;

    // Filter test cases
    let filtered = allQACases.filter(tc => {
      if (staleOnly && !tc.stale) return false;
      if (qaVersionFilter !== "all" && String(tc.version_id) !== String(qaVersionFilter)) return false;
      if (qaSearchTerm) {
        const titleMatch = (tc.title || "").toLowerCase().includes(qaSearchTerm);
        const keyMatch = (tc.node_key || "").toLowerCase().includes(qaSearchTerm);
        const stepsMatch = Array.isArray(tc.steps) 
          ? tc.steps.join(" ").toLowerCase().includes(qaSearchTerm)
          : (tc.steps || "").toLowerCase().includes(qaSearchTerm);
        const expMatch = (tc.expected_result || "").toLowerCase().includes(qaSearchTerm);
        if (!titleMatch && !keyMatch && !stepsMatch && !expMatch) return false;
      }
      return true;
    });

    const total = filtered.length;

    if (total === 0) {
      countInfo.innerHTML = `<span>No test cases found matching current filters.</span>`;
      if (toggleAllBtn) toggleAllBtn.classList.add("hidden");
      container.innerHTML = `<div class="placeholder-msg">${staleOnly ? 'No stale test cases match the filter.' : 'No test cases found for the selected version or search term.'}</div>`;
      return;
    }

    // Top 5 slice vs All
    let displayCases;
    if (qaShowAll || total <= 5) {
      displayCases = filtered;
      if (total <= 5) {
        countInfo.innerHTML = `Showing all <strong>${total}</strong> test cases`;
        if (toggleAllBtn) toggleAllBtn.classList.add("hidden");
      } else {
        countInfo.innerHTML = `Showing all <strong>${total}</strong> test cases`;
        if (toggleAllBtn) {
          toggleAllBtn.classList.remove("hidden");
          toggleAllBtn.innerHTML = `
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <polyline points="18 15 12 9 6 15"></polyline>
            </svg>
            <span id="qa-toggle-all-text">Show Top 5 Only</span>
          `;
        }
      }
    } else {
      displayCases = filtered.slice(0, 5);
      const remaining = total - 5;
      countInfo.innerHTML = `Showing top <strong>5</strong> of <strong>${total}</strong> test cases (<span class="remaining-count-badge">${remaining} remaining</span>)`;
      if (toggleAllBtn) {
        toggleAllBtn.classList.remove("hidden");
        toggleAllBtn.innerHTML = `
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <polyline points="6 9 12 15 18 9"></polyline>
          </svg>
          <span id="qa-toggle-all-text">View All (${total})</span>
        `;
      }
    }

    container.innerHTML = displayCases.map(tc => {
      const steps = Array.isArray(tc.steps) ? tc.steps : [tc.steps];
      return `
        <div class="qa-case-card ${tc.stale ? 'stale' : 'active'}">
          <div class="qa-case-header">
            <div>
              <div class="qa-header-badges">
                <span class="badge badge-primary">Version ${tc.version_id} • Section ${escapeHtml(tc.node_key)}</span>
                <span class="stale-badge ${tc.stale ? 'stale' : 'active'}">
                  ${tc.stale ? `STALE (${escapeHtml(tc.stale_reason || 'MODIFIED')})` : 'ACTIVE & GROUNDED'}
                </span>
              </div>
              <h4 class="qa-case-title">${escapeHtml(tc.title)}</h4>
            </div>
            <div class="qa-case-actions">
              <button class="btn btn-refer-section btn-xs" data-version="${tc.version_id}" data-node="${escapeHtml(tc.node_key)}" title="Inspect section in Document Explorer">
                <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                  <path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"></path>
                  <polyline points="15 3 21 3 21 9"></polyline>
                  <line x1="10" y1="14" x2="21" y2="3"></line>
                </svg>
                Refer Section
              </button>
            </div>
          </div>

          <ol class="qa-steps-list">
            ${steps.map(s => `<li>${escapeHtml(s)}</li>`).join("")}
          </ol>

          <div class="qa-expected">
            <strong>Expected Result:</strong> ${escapeHtml(tc.expected_result)}
          </div>

          ${tc.source_quote ? `
            <div class="qa-quote">
              <strong>Source Grounding:</strong> "${escapeHtml(tc.source_quote)}"
            </div>
          ` : ''}
        </div>
      `;
    }).join("");

    // Wire up "Refer Section" buttons to jump to explorer
    container.querySelectorAll(".btn-refer-section").forEach(btn => {
      btn.addEventListener("click", () => {
        const vId = btn.dataset.version;
        const nKey = btn.dataset.node;
        jumpToExplorerSection(vId, nKey);
      });
    });
  }

  async function jumpToExplorerSection(versionId, nodeKey) {
    switchToTab("explorer-tab");
    const explorerSelect = document.getElementById("explorer-version-select");
    const searchInput = document.getElementById("explorer-search-input");
    const clearBtn = document.getElementById("explorer-clear-search");

    if (explorerSelect && versionId) {
      explorerSelect.value = versionId;
      currentVersionId = parseInt(versionId);
      await loadTreeForVersion(currentVersionId);
    }

    if (searchInput && nodeKey) {
      searchInput.value = nodeKey;
      if (clearBtn) clearBtn.classList.remove("hidden");
      applyExplorerFilter();
    }
  }

  function escapeHtml(str) {
    if (!str) return "";
    return String(str)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }
});
