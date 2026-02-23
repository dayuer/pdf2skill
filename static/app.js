/* ══════ pdf2skill — app.js ══════ */

/* ── 状态 ── */
let sessionId = localStorage.getItem("pdf2skill_session");
let selectedChunkIdx = null;

function esc(s) {
  return (s || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}
function resetSession() {
  localStorage.removeItem("pdf2skill_session");
  location.reload();
}

/* ── 拖拽上传 ── */
const dropzone = document.getElementById("upload-area");
const fileInput = document.getElementById("fileInput");
if (dropzone) {
  dropzone.addEventListener("dragover", (e) => {
    e.preventDefault();
    dropzone.style.borderColor = "#7c3aed";
  });
  dropzone.addEventListener("dragleave", () => {
    dropzone.style.borderColor = "#27272a";
  });
  dropzone.addEventListener("drop", (e) => {
    e.preventDefault();
    dropzone.style.borderColor = "#27272a";
    if (e.dataTransfer.files[0]) uploadFile(e.dataTransfer.files[0]);
  });
}
fileInput.addEventListener("change", () => {
  if (fileInput.files[0]) uploadFile(fileInput.files[0]);
});

async function uploadFile(file) {
  document.getElementById("upload-area").style.display = "none";
  document.getElementById("analysis-loading").style.display = "flex";
  const fd = new FormData();
  fd.append("file", file);
  try {
    const r = await fetch("/api/analyze", { method: "POST", body: fd });
    const data = await r.json();
    if (!r.ok) {
      alert(data.detail || "分析失败");
      location.reload();
      return;
    }
    sessionId = data.session_id;
    localStorage.setItem("pdf2skill_session", sessionId);
    showWorkspace(data);
  } catch (e) {
    alert("上传失败: " + e.message);
    location.reload();
  }
}

/* ── 展示工作区 ── */
function showWorkspace(data) {
  document.getElementById("analysis-loading").style.display = "none";
  document.getElementById("upload-area").style.display = "none";
  document.getElementById("center-placeholder").style.display = "none";
  document.getElementById("workspace").style.display = "flex";
  document.getElementById("settings-area").style.display = "block";
  document.getElementById("btn-reupload").style.display = "";
  document.getElementById("doc-name-display").textContent =
    "《" + data.doc_name + "》";
  const stag = document.getElementById("strategy-tag");
  stag.style.display = "";
  stag.textContent = data.prompt_type;
  const ctag = document.getElementById("chunk-count-tag");
  ctag.style.display = "";
  ctag.textContent = data.filtered_chunks + " chunks";

  // 文档摘要
  const cc = (data.core_components || [])
    .map((c) => '<span class="summary-tag">' + c + "</span>")
    .join("");
  const st = (data.skill_types || [])
    .map((c) => '<span class="summary-tag green">' + c + "</span>")
    .join("");
  const allTypes = [
    "技术手册",
    "叙事类",
    "方法论",
    "学术教材",
    "操作规范",
    "保险合同",
    "行业报告",
    "医学法律",
  ];
  if (data.book_type && !allTypes.includes(data.book_type))
    allTypes.push(data.book_type);
  const typeOpts = allTypes
    .map(
      (t) =>
        "<option" +
        (t === data.book_type ? " selected" : "") +
        ">" +
        t +
        "</option>",
    )
    .join("");
  const ds = document.getElementById("doc-summary");
  ds.style.display = "block";
  ds.innerHTML =
    '<div class="doc-summary"><div class="row"><span class="label">格式</span><span class="val">' +
    data.format.toUpperCase() +
    '</span><span class="label">领域</span><span class="val">' +
    (data.domains || []).join(", ") +
    '</span><span class="label">块数</span><span class="val">' +
    data.filtered_chunks +
    " / " +
    data.total_chunks +
    "</span></div>" +
    (cc || st ? '<div class="summary-tags">' + cc + st + "</div>" : "") +
    '<div style="margin-top:6px"><select id="sel-book-type" class="setting-select" onchange="autoPromptType();saveSettings()">' +
    typeOpts +
    "</select></div></div>";

  if (data.baseline_hint)
    document.getElementById("prompt-hint").value = data.baseline_hint;
  if (data.system_prompt)
    document.getElementById("system-prompt-display").value = data.system_prompt;

  document.getElementById("chunk-panel").style.display = "block";
  loadChunkList();
}

function autoPromptType() {}

async function saveSettings() {
  if (!sessionId) return;
  await fetch("/api/session/" + sessionId + "/settings", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      book_type: document.getElementById("sel-book-type")?.value || "",
    }),
  });
  try {
    const r = await fetch("/api/prompt-preview/" + sessionId);
    const pp = await r.json();
    if (pp.system_prompt)
      document.getElementById("system-prompt-display").value = pp.system_prompt;
    if (pp.baseline_hint)
      document.getElementById("prompt-hint").value = pp.baseline_hint;
  } catch (e) {}
}

async function saveSystemPrompt() {
  if (!sessionId) return;
  const sp = document.getElementById("system-prompt-display").value.trim();
  await fetch("/api/session/" + sessionId + "/settings", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ system_prompt: sp }),
  });
}

function toggleSettings() {
  document.getElementById("settings-body").classList.toggle("open");
  document.getElementById("settings-arrow").classList.toggle("open");
}

/* ── Chunk 列表 ── */
let _searchTimer = null;
async function rechunkDoc() {
  if (!sessionId) return;
  const btn = event.target;
  btn.disabled = true;
  btn.textContent = "⏳…";
  try {
    const r = await fetch("/api/rechunk/" + sessionId, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ max_chars: 2000, min_chars: 200 }),
    });
    const d = await r.json();
    if (d.ok) {
      btn.textContent = "✅ " + d.filtered_chunks + "块";
      setTimeout(() => {
        btn.textContent = "🔄 重切";
        btn.disabled = false;
      }, 1500);
      loadChunkList();
    } else {
      btn.textContent = "❌";
      btn.disabled = false;
    }
  } catch (e) {
    btn.textContent = "❌";
    btn.disabled = false;
  }
}

async function loadChunkList(q) {
  try {
    const params = q
      ? "?q=" + encodeURIComponent(q) + "&page_size=50"
      : "?page_size=50";
    const r = await fetch("/api/chunks/" + sessionId + params);
    const data = await r.json();
    document.getElementById("chunk-count").textContent =
      "共 " + data.total + " 块" + (q ? "（筛选）" : "");
    document.getElementById("chunk-list").innerHTML = data.items
      .map(
        (c) =>
          '<div class="chunk-item' +
          (c.index === selectedChunkIdx ? " selected" : "") +
          '" onclick="selectChunk(' +
          c.index +
          ')" data-idx="' +
          c.index +
          '"><span class="idx">#' +
          c.index +
          "</span>" +
          esc(c.preview) +
          '<span class="path">' +
          (c.heading_path.join(" > ") || "") +
          "</span></div>",
      )
      .join("");
  } catch (e) {}
}

function searchChunks() {
  clearTimeout(_searchTimer);
  _searchTimer = setTimeout(() => {
    loadChunkList(
      document.getElementById("chunk-search").value.trim() || undefined,
    );
  }, 300);
}
function selectChunk(idx) {
  selectedChunkIdx = idx;
  document.querySelectorAll(".chunk-item").forEach((el) => {
    el.classList.toggle("selected", parseInt(el.dataset.idx) === idx);
  });
}

/* ── Studio 动作路由 ── */
function studioAction(action) {
  if (!sessionId) {
    alert("请先上传文档");
    return;
  }
  switch (action) {
    case "tune":
      runTune();
      break;
    case "sample":
      runSampleCheck();
      break;
    case "execute":
      startExecute();
      break;
    case "skills":
      loadSkillsList();
      break;
    case "graph":
      showSkillGraph();
      break;
    default:
      alert("功能即将推出");
  }
}

/* ── 调优 ── */
async function runTune() {
  if (selectedChunkIdx === null) {
    alert("请先在左栏选择一个 chunk");
    return;
  }
  const hint = document.getElementById("prompt-hint").value.trim();
  document.getElementById("tune-loading").style.display = "flex";
  document.getElementById("result-section").style.display = "none";
  document.getElementById("source-preview-section").style.display = "none";
  try {
    const r = await fetch("/api/tune/" + sessionId, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        chunk_index: selectedChunkIdx,
        prompt_hint: hint,
        system_prompt: document
          .getElementById("system-prompt-display")
          .value.trim(),
      }),
    });
    const d = await r.json();
    showTuneResult(d);
    loadTuneHistory();
    document.getElementById("st-tune").textContent = "已完成";
  } catch (e) {
    alert("调优失败: " + e.message);
  }
  document.getElementById("tune-loading").style.display = "none";
}

function showTuneResult(d) {
  document.getElementById("source-preview-section").style.display = "block";
  document.getElementById("source-chunk-idx").textContent = d.chunk_index;
  document.getElementById("source-preview").textContent = d.source_text || "";
  const sec = document.getElementById("result-section");
  sec.style.display = "flex";
  const skills = d.extracted_skills || [];
  const passed = skills.filter((s) => s.status !== "failed").length;
  document.getElementById("result-stats").textContent =
    "v" +
    (d.version || "?") +
    " · " +
    passed +
    "✅ " +
    (skills.length - passed) +
    "❌";
  document.getElementById("result-cards").innerHTML =
    skills
      .map(
        (s) =>
          '<div class="skill-card' +
          (s.status === "failed" ? " fail" : "") +
          '"><div class="skill-name">' +
          esc(s.name || "(unnamed)") +
          '</div><div class="skill-trigger">' +
          esc(s.trigger || "") +
          '</div><span class="skill-domain">' +
          esc(s.domain || "general") +
          '</span><div class="skill-body">' +
          esc(s.body || "") +
          "</div></div>",
      )
      .join("") || '<div class="empty-hint">无可提取内容</div>';
}

/* ── 版本历史 ── */
async function loadTuneHistory() {
  try {
    const r = await fetch("/api/tune-history/" + sessionId);
    const history = await r.json();
    if (!history.length) return;
    document.getElementById("version-section").style.display = "block";
    document.getElementById("version-timeline").innerHTML = history
      .map(
        (h, i) =>
          '<div class="version-dot' +
          (i === history.length - 1 ? " active" : "") +
          '" onclick="replayVersion(' +
          i +
          ')" title="chunk#' +
          h.chunk_index +
          " " +
          h.timestamp +
          '">v' +
          h.version +
          "</div>",
      )
      .join("");
    window._tuneHistory = history;
  } catch (e) {}
}

function replayVersion(idx) {
  const h = window._tuneHistory[idx];
  if (!h) return;
  document.getElementById("prompt-hint").value = h.prompt_hint || "";
  selectedChunkIdx = h.chunk_index;
  document.querySelectorAll(".chunk-item").forEach((el) => {
    el.classList.toggle("selected", parseInt(el.dataset.idx) === h.chunk_index);
  });
  showTuneResult({
    chunk_index: h.chunk_index,
    source_text: h.source_text_preview || "",
    extracted_skills: h.extracted_skills || [],
    version: h.version,
  });
  document
    .querySelectorAll(".version-dot")
    .forEach((el, i) => el.classList.toggle("active", i === idx));
}

/* ── 抽样验证 ── */
async function runSampleCheck() {
  document.getElementById("sample-section").style.display = "block";
  document.getElementById("sample-cards").innerHTML =
    '<div class="loading-text"><div class="spinner"></div><span>批量提取和校验中...</span></div>';
  try {
    const r = await fetch("/api/sample-check/" + sessionId, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ sample_size: 5 }),
    });
    const d = await r.json();
    const passRate = d.total > 0 ? ((d.passed / d.total) * 100).toFixed(0) : 0;
    document.getElementById("sample-stats").innerHTML =
      '<span class="' +
      (passRate >= 60 ? "sample-pass" : "sample-fail") +
      '">通过率 ' +
      passRate +
      "% (" +
      d.passed +
      "/" +
      d.total +
      ")</span>";
    document.getElementById("sample-cards").innerHTML = (d.results || [])
      .map(
        (item) =>
          '<div class="sample-card"><div style="display:flex;justify-content:space-between;margin-bottom:3px"><span>#' +
          item.chunk_index +
          "</span><span>" +
          (item.skills || []).length +
          ' skills</span></div><div style="color:#52525b;font-size:10px">' +
          esc((item.source_preview || "").substring(0, 100)) +
          "</div>" +
          (item.skills || [])
            .map(
              (s) =>
                '<span class="summary-tag" style="margin-top:3px">' +
                esc(s.name) +
                "</span>",
            )
            .join("") +
          "</div>",
      )
      .join("");
    document.getElementById("st-sample").textContent =
      "通过率 " + passRate + "%";
  } catch (e) {
    document.getElementById("sample-cards").innerHTML =
      '<div style="color:#f87171">验证失败: ' + e.message + "</div>";
  }
}

/* ── 全量执行 ── */
function _logToEventLog(type, msg) {
  const log = document.getElementById("event-log");
  log.style.display = "block";
  const now = new Date().toLocaleTimeString("zh-CN", { hour12: false });
  log.innerHTML +=
    '<div class="log-line"><span class="log-time">' +
    now +
    '</span><span class="log-type">' +
    esc(type) +
    '</span><span class="log-msg">' +
    esc(msg) +
    "</span></div>";
  log.scrollTop = log.scrollHeight;
}

function startExecute() {
  if (!confirm("开始全量执行？将使用当前策略处理所有 chunk。")) return;
  document.getElementById("execute-section").style.display = "block";
  document.getElementById("pbar").style.width = "0";
  document.getElementById("ptext").textContent = "准备中...";
  document.getElementById("execute-result").innerHTML = "";
  document.getElementById("event-log").innerHTML = "";
  document.getElementById("event-log").style.display = "none";

  const src = new EventSource("/api/execute/" + sessionId);
  src.addEventListener("phase", (e) => {
    const d = JSON.parse(e.data);
    document.getElementById("ptext").textContent = d.message;
    if (d.done && d.total)
      document.getElementById("pbar").style.width =
        (d.done / d.total) * 100 + "%";
    _logToEventLog("阶段", d.message);
  });
  src.addEventListener("progress", (e) => {
    const d = JSON.parse(e.data);
    const pct = ((d.completed / d.total) * 100).toFixed(0);
    document.getElementById("pbar").style.width = pct + "%";
    const eta =
      d.eta_s > 60 ? (d.eta_s / 60).toFixed(0) + "m" : d.eta_s.toFixed(0) + "s";
    document.getElementById("ptext").textContent =
      d.completed +
      "/" +
      d.total +
      " (" +
      pct +
      "%) | 💾 " +
      (d.skills_on_disk || 0) +
      " Skills | ⏱" +
      d.elapsed_s.toFixed(0) +
      "s ETA " +
      eta;
    document.getElementById("st-execute").textContent = pct + "%";
  });
  // 新增事件：批次开始
  src.addEventListener("batch_start", (e) => {
    const d = JSON.parse(e.data);
    _logToEventLog("批次", d.message);
  });
  // 新增事件：单 Skill 校验通过
  src.addEventListener("skill_validated", (e) => {
    const d = JSON.parse(e.data);
    _logToEventLog("提取", "✅ " + d.name + " [" + d.domain + "]");
  });
  // 新增事件：批次校验统计
  src.addEventListener("validation", (e) => {
    const d = JSON.parse(e.data);
    _logToEventLog("校验", d.message);
  });
  src.addEventListener("complete", (e) => {
    src.close();
    const d = JSON.parse(e.data);
    document.getElementById("pbar").style.width = "100%";
    document.getElementById("ptext").textContent =
      "✅ 完成！" + d.final_skills + " Skills";
    document.getElementById("st-execute").textContent =
      d.final_skills + " Skills";
    _logToEventLog("完成", "共 " + d.final_skills + " Skills");
    const typeColors = {
      factual: "#3b82f6",
      procedural: "#22c55e",
      relational: "#f59e0b",
    };
    const skills = (d.skills || [])
      .map(
        (s) =>
          '<div class="skill-card"><div class="skill-name">' +
          esc(s.name) +
          '</div><div class="skill-trigger">' +
          esc(s.trigger) +
          '</div><span class="skill-domain">' +
          esc(s.domain) +
          '</span> <span style="padding:2px 7px;border-radius:4px;font-size:10px;background:' +
          (typeColors[s.sku_type] || "#666") +
          "20;color:" +
          (typeColors[s.sku_type] || "#aaa") +
          '">' +
          esc(s.sku_type || "") +
          '</span><div class="skill-body">' +
          esc(s.body) +
          "</div></div>",
      )
      .join("");
    const skuInfo = d.sku_stats
      ? " | 📋" +
        (d.sku_stats.factual || 0) +
        " 事实 ⚙️" +
        (d.sku_stats.procedural || 0) +
        " 程序 🔗" +
        (d.sku_stats.relational || 0) +
        " 关系"
      : "";
    document.getElementById("execute-result").innerHTML =
      '<div style="margin-top:6px"><span class="val hl">' +
      d.final_skills +
      " SKUs</span> · " +
      d.elapsed_s +
      "s" +
      skuInfo +
      "</div>" +
      skills;
    loadSkillsList();
  });
  src.addEventListener("error", (e) => {
    try {
      const d = JSON.parse(e.data);
      _logToEventLog("错误", d.message || "未知错误");
    } catch (_) {}
  });
  src.onerror = () => {
    src.close();
    document.getElementById("ptext").textContent = "❌ 连接中断";
    _logToEventLog("系统", "SSE 连接中断");
  };
}

/* ── 知识图谱 ── */
async function showSkillGraph() {
  document.getElementById("graph-section").style.display = "block";
  document.getElementById("graph-loading").style.display = "flex";
  document.getElementById("graph-container").innerHTML = "";
  document.getElementById("top-skills-section").style.display = "none";
  document.getElementById("graph-stats").textContent = "";
  try {
    const r = await fetch("/api/session/" + sessionId + "/skill-graph", {
      method: "POST",
    });
    const d = await r.json();
    if (!r.ok) {
      document.getElementById("graph-container").innerHTML =
        '<div class="empty-hint">' + esc(d.error || "构建失败") + "</div>";
      document.getElementById("graph-loading").style.display = "none";
      return;
    }

    // 统计
    const stats = d.statistics || {};
    document.getElementById("graph-stats").textContent =
      stats.total_nodes + " 节点 · " + stats.total_edges + " 边 · " + stats.clusters + " 聚类";

    // Mermaid 渲染
    const container = document.getElementById("graph-container");
    if (d.mermaid) {
      const id = "mermaid-graph-" + Date.now();
      try {
        const { svg } = await mermaid.render(id, d.mermaid);
        container.innerHTML = svg;
      } catch (e) {
        container.innerHTML =
          '<pre style="color:#71717a;font-size:11px;white-space:pre-wrap">' +
          esc(d.mermaid) + "</pre>";
      }
    } else {
      container.innerHTML = '<div class="empty-hint">无图谱数据</div>';
    }

    // Top Skills 排行
    if (d.top_skills && d.top_skills.length) {
      document.getElementById("top-skills-section").style.display = "block";
      const rows = d.top_skills
        .map(
          (s, i) =>
            "<tr><td class='rank'>#" +
            (i + 1) +
            "</td><td>" +
            esc(s.name) +
            '</td><td><span class="summary-tag">' +
            esc(s.domain) +
            "</span></td><td>" +
            (s.pagerank * 100).toFixed(1) +
            "%</td></tr>",
        )
        .join("");
      document.getElementById("top-skills-table").innerHTML =
        '<table class="top-rank-table"><thead><tr><th></th><th>Skill</th><th>领域</th><th>PageRank</th></tr></thead><tbody>' +
        rows +
        "</tbody></table>";
    }

    document.getElementById("st-graph").textContent =
      stats.total_nodes + " 节点";
  } catch (e) {
    document.getElementById("graph-container").innerHTML =
      '<div style="color:#f87171">图谱构建失败: ' + e.message + "</div>";
  }
  document.getElementById("graph-loading").style.display = "none";
}

/* ── 技能列表加载 ── */
async function loadSkillsList() {
  if (!sessionId) return;
  try {
    const r = await fetch("/api/session/" + sessionId + "/skills");
    const skills = await r.json();
    if (!skills.length) {
      document.getElementById("skill-list").innerHTML =
        '<div class="empty-hint">尚未提取</div>';
      return;
    }
    document.getElementById("st-skills").textContent = skills.length + "个";
    document.getElementById("skill-list").innerHTML = skills
      .slice(0, 30)
      .map(
        (s) =>
          '<div class="skill-list-item"><div class="sname">' +
          esc(s.name || "") +
          '</div><div class="smeta">' +
          esc(s.domain || "") +
          " · " +
          esc(s.sku_type || "") +
          "</div></div>",
      )
      .join("");
  } catch (e) {}
}

/* ── 页面恢复 ── */
(async function () {
  if (!sessionId) return;
  try {
    const r = await fetch("/api/session/" + sessionId + "/state");
    if (!r.ok) {
      localStorage.removeItem("pdf2skill_session");
      return;
    }
    const st = await r.json();
    showWorkspace(st.meta);
    loadTuneHistory();
    loadSkillsList();
    try {
      const pr = await fetch("/api/prompt-preview/" + sessionId);
      if (pr.ok) {
        const pp = await pr.json();
        if (pp.baseline_hint && !document.getElementById("prompt-hint").value)
          document.getElementById("prompt-hint").value = pp.baseline_hint;
        document.getElementById("system-prompt-display").value =
          pp.system_prompt || "";
      }
    } catch (e) {}
  } catch (e) {
    localStorage.removeItem("pdf2skill_session");
  }
})();
