const TOKEN_KEY = "quudet_access_token";
const API_BASE_KEY = "api_base";
const LAST_DATASET_ID_KEY = "last_dataset_id";

const menuItems = document.querySelectorAll(".menu-item");
const pages = document.querySelectorAll(".page");

const appShell = document.querySelector("#app-shell");
const apiBaseInput = document.querySelector("#api-base-input");
const apiBaseSave = document.querySelector("#api-base-save");

const statTotal = document.querySelector("#stat-total");
const statRunning = document.querySelector("#stat-running");
const statSuccess = document.querySelector("#stat-success");
const statFailed = document.querySelector("#stat-failed"); // legacy (may be absent)
const statModels = document.querySelector("#stat-models");

const recentTaskBody = document.querySelector("#recent-task-body");
const taskTableBody = document.querySelector("#task-table-body");

const datasetFileInput = document.querySelector("#dataset-file-input");
const datasetUploadBtn = document.querySelector("#dataset-upload-btn");
const datasetInfo = document.querySelector("#dataset-info");

const startTrainBtn = document.querySelector("#start-train-btn");
const startTrainBtn2 = document.querySelector("#start-train-btn-2");
const gotoDatasetTrain = document.querySelector("#goto-dataset-train");
const startTestBtn = document.querySelector("#start-test-btn");
const startDetectBtn = document.querySelector("#start-detect-btn");
const refreshTaskBtn = document.querySelector("#refresh-task-btn");
const clearTaskBtn = document.querySelector("#clear-task-btn");
const trainModelInfo = document.querySelector("#train-model-info");
const trainExpNameInput = document.querySelector("#train-exp-name");

const detectFileInput = document.querySelector("#detect-file-input");
const detectFilePickBtn = document.querySelector("#detect-file-pick");
const detectFileName = document.querySelector("#detect-file-name");
const resolvedDetectSource = document.querySelector("#resolved-detect-source");

const taskDetailDialog = document.querySelector("#task-detail-dialog");
const taskDetailContent = document.querySelector("#task-detail-content");
const taskDetailImages = document.querySelector("#task-detail-images");
const closeDialogBtn = document.querySelector("#close-dialog-btn");
const taskLogsBtn = document.querySelector("#task-logs-btn");

const detectLatestPanel = document.querySelector("#detect-latest-panel");
const detectLatestSummary = document.querySelector("#detect-latest-summary");
const detectLatestImages = document.querySelector("#detect-latest-images");

const monitorRefreshBtn = document.querySelector("#monitor-refresh-btn");
const currentTrainInfo = document.querySelector("#current-train-info");
const metricChartsContainer = document.querySelector("#metric-charts");
const monitorTaskId = document.querySelector("#monitor-task-id");
const monitorProgressFill = document.querySelector("#monitor-progress-fill");
const monitorProgressText = document.querySelector("#monitor-progress-text");
const monitorMetricsValues = document.querySelector("#monitor-metrics-values");
const monitorNodeSelect = document.querySelector("#sel-monitor-node");
const monitorJobSelect = document.querySelector("#sel-monitor-job");
const nodesRefreshBtn = document.querySelector("#nodes-refresh-btn");
const nodesSummary = document.querySelector("#nodes-summary");
const nodesTableBody = document.querySelector("#nodes-table-body");
const trainNodeSelect = document.querySelector("#sel-train-node");
const testNodeSelect = document.querySelector("#sel-test-node");
const detectNodeSelect = document.querySelector("#sel-detect-node");
const taskNodeFilter = document.querySelector("#task-node-filter");
let metricCharts = {}; // 存储多个图表实例

// 登录相关元素
const loginPage = document.querySelector("#login-page");
const loginEmail = document.querySelector("#login-email");
const loginPassword = document.querySelector("#login-password");
const loginApi = document.querySelector("#login-api");
const loginBtn = document.querySelector("#login-btn");
const app = document.querySelector("#app");

let pollTimer = null;
let lastDetailJobId = null;
let lastRenderedDetectJobKey = null;
const trainProgressByJob = new Map();
let progressRefreshInFlight = false;

/** @type {any} */
let yoloOptions = {
  model_yamls: [],
  datasets: [],
  uploaded_datasets: [],
  scales: ["n", "s", "m", "l", "x"],
  official_weights: [],
  user_weights: [],
};

let testWeightMode = "official";
let detectWeightMode = "official";
let nodeRows = [];

function stemHasScaleSuffix(stem) {
  return /yolo(e-)?v?\d+([nslmxt])/i.test(stem);
}

function buildModelYamlRel(relPath, scale) {
  const i = relPath.lastIndexOf("/");
  const file = i >= 0 ? relPath.slice(i + 1) : relPath;
  const stem = file.replace(/\.yaml$/i, "");
  if (stemHasScaleSuffix(stem)) {
    return relPath;
  }
  if (!scale) {
    return relPath;
  }
  const dir = i >= 0 ? relPath.slice(0, i) : "";
  const name = `${stem}${scale}.yaml`;
  return dir ? `${dir}/${name}` : name;
}

function syncTrainModel() {
  const sel = document.querySelector("#sel-train-model");
  const scaleEl = document.querySelector("#sel-train-scale");
  const hidden = document.querySelector("#resolved-train-model");
  if (!sel || !scaleEl || !hidden) {
    return;
  }
  const opt = sel.selectedOptions[0];
  const locked = opt?.dataset?.locked === "true";
  scaleEl.disabled = !!locked;
  const path = sel.value;
  hidden.value = locked ? path : buildModelYamlRel(path, scaleEl.value);
  refreshModelInfo().catch(() => {});
}

/** 训练/测试页「数据集配置」：已上传项 + 内置 yaml（扁平列表，避免部分环境下 optgroup 不可见） */
function fillDataSelectWithBuiltinAndUploads(dSel, uploadedRows, preferredUploadId = null) {
  if (!dSel) {
    return;
  }
  const builtin = yoloOptions.datasets || [];
  const rows = Array.isArray(uploadedRows) ? uploadedRows : [];
  const usable = rows.filter((u) => u && u.data_yaml);
  const unusable = rows.filter((u) => u && !u.data_yaml);

  dSel.innerHTML = "";

  const addUploadsFirst = () => {
    usable.forEach((u) => {
      const o = document.createElement("option");
      o.value = u.data_yaml;
      o.textContent = `[上传 #${u.id}] ${u.filename}`;
      o.dataset.uploadId = String(u.id);
      dSel.appendChild(o);
    });
    unusable.forEach((u) => {
      const o = document.createElement("option");
      o.value = "";
      o.textContent = `[上传 #${u.id}] ${u.filename}（无 data.yaml，不可选）`;
      o.disabled = true;
      dSel.appendChild(o);
    });
  };

  addUploadsFirst();

  builtin.forEach((d) => {
    const o = document.createElement("option");
    o.value = d.path;
    o.textContent = `[内置] ${d.name}`;
    dSel.appendChild(o);
  });

  const pid =
    preferredUploadId != null && preferredUploadId !== ""
      ? String(preferredUploadId)
      : localStorage.getItem(LAST_DATASET_ID_KEY);
  if (pid) {
    const hit = Array.from(dSel.querySelectorAll("option")).find(
      (o) => o.dataset.uploadId === pid && !o.disabled && o.value
    );
    if (hit) {
      hit.selected = true;
      return;
    }
  }
  const coco8 = Array.from(dSel.querySelectorAll("option")).find(
    (o) => !o.disabled && o.value && /coco8\.yaml$/i.test(o.value)
  );
  if (coco8) {
    coco8.selected = true;
  } else {
    const first = Array.from(dSel.options).find((o) => !o.disabled && o.value);
    if (first) {
      first.selected = true;
    }
  }
}

function updateDatasetSelectHints(uploadedRows, datasetsLoadError) {
  const trainHint = document.querySelector("#dataset-select-hint");
  const testHint = document.querySelector("#test-dataset-select-hint");
  const rows = Array.isArray(uploadedRows) ? uploadedRows : [];
  const usable = rows.filter((u) => u && u.data_yaml);
  const textFor = () => {
    if (datasetsLoadError) {
      return `无法加载已上传列表：${datasetsLoadError}（请检查 API 地址、是否登录、控制台网络请求）`;
    }
    if (!rows.length) {
      return "当前账号在服务器上还没有上传记录；上传成功后会出现在上方「上传数据集」旁提示，并出现在本下拉里。";
    }
    return `已从服务器加载 ${rows.length} 个上传文件，其中 ${usable.length} 个包含可用的 data 配置。无 yaml 的项呈灰色不可选。`;
  };
  const t = textFor();
  if (trainHint) {
    trainHint.textContent = t;
  }
  if (testHint) {
    testHint.textContent = t;
  }
}

function getSelectedUploadIdFromDataSelect(selectSelector) {
  const sel = document.querySelector(selectSelector);
  const opt = sel?.selectedOptions?.[0];
  const raw = opt?.dataset?.uploadId;
  if (raw === undefined || raw === "") {
    return null;
  }
  const n = Number(raw);
  return Number.isFinite(n) ? n : null;
}

function fillTrainYoloSelects(uploadedRows = []) {
  const mSel = document.querySelector("#sel-train-model");
  const dSel = document.querySelector("#sel-train-data");
  const sSel = document.querySelector("#sel-train-scale");
  const pSel = document.querySelector("#sel-train-pretrained");
  if (!mSel || !dSel || !sSel || !pSel) {
    return;
  }

  mSel.innerHTML = "";
  const byFolder = {};
  (yoloOptions.model_yamls || []).forEach((m) => {
    const f = m.folder || "models";
    (byFolder[f] ||= []).push(m);
  });
  Object.keys(byFolder)
    .sort()
    .forEach((folder) => {
      const og = document.createElement("optgroup");
      og.label = folder;
      byFolder[folder].forEach((m) => {
        const o = document.createElement("option");
        o.value = m.path;
        o.textContent = m.name;
        o.dataset.locked = m.scale_locked ? "true" : "false";
        og.appendChild(o);
      });
      mSel.appendChild(og);
    });

  let pick = Array.from(mSel.querySelectorAll("option")).find((o) => /yolov8\.yaml$/i.test(o.value));
  if (!pick && mSel.options.length) {
    pick = mSel.options[0];
  }
  if (pick) {
    pick.selected = true;
  }

  sSel.innerHTML = "";
  (yoloOptions.scales || ["n", "s", "m", "l", "x"]).forEach((s) => {
    const o = document.createElement("option");
    o.value = s;
    o.textContent = s.toUpperCase();
    sSel.appendChild(o);
  });
  const nOpt = Array.from(sSel.options).find((o) => o.value === "n");
  if (nOpt) {
    nOpt.selected = true;
  }

  fillDataSelectWithBuiltinAndUploads(dSel, uploadedRows);

  pSel.innerHTML = "";
  const empty = document.createElement("option");
  empty.value = "";
  empty.textContent = "（不指定，由训练流程默认）";
  pSel.appendChild(empty);
  (yoloOptions.official_weights || []).forEach((w) => {
    const o = document.createElement("option");
    o.value = w.value;
    o.textContent = `${w.group} · ${w.label}`;
    pSel.appendChild(o);
  });

  syncTrainModel();
}

function fillOptimizerSelect() {
  const sel = document.querySelector("#sel-train-optimizer");
  if (!sel) {
    return;
  }
  sel.innerHTML = "";
  const opts = [
    { v: "", t: "auto（推荐）" },
    { v: "SGD", t: "SGD" },
    { v: "Adam", t: "Adam" },
    { v: "AdamW", t: "AdamW" },
    { v: "RMSProp", t: "RMSProp" },
    { v: "NAdam", t: "NAdam" },
    { v: "RAdam", t: "RAdam" },
  ];
  opts.forEach((o) => {
    const op = document.createElement("option");
    op.value = o.v;
    op.textContent = o.t;
    sel.appendChild(op);
  });
  sel.value = "";
}

function fmtNum(n) {
  if (n === null || n === undefined) return "-";
  if (typeof n !== "number" || Number.isNaN(n)) return "-";
  if (n >= 1e9) return `${(n / 1e9).toFixed(2)}B`;
  if (n >= 1e6) return `${(n / 1e6).toFixed(2)}M`;
  if (n >= 1e3) return `${(n / 1e3).toFixed(2)}K`;
  return String(n);
}

function safeText(s) {
  if (s === null || s === undefined) return "-";
  return String(s);
}

function fillNodeSelect(selectEl, rows = []) {
  if (!selectEl) return;
  const old = selectEl.value || "";
  selectEl.innerHTML = '<option value="">自动/本机</option>';
  rows.forEach((n) => {
    const caps = n.capabilities || {};
    const gpu = caps.has_gpu ? " GPU" : "";
    const kind = caps.node_kind === "remote" ? " [远程]" : " [本地]";
    const op = document.createElement("option");
    op.value = n.id;
    op.textContent = `${n.display_name}${kind}${gpu} (${n.status})`;
    selectEl.appendChild(op);
  });
  if (old) {
    selectEl.value = old;
  }
}

function fillTaskNodeFilter(rows = []) {
  if (!taskNodeFilter) return;
  const old = taskNodeFilter.value || "";
  taskNodeFilter.innerHTML = '<option value="">全部节点</option>';
  rows.forEach((n) => {
    const op = document.createElement("option");
    op.value = n.id;
    op.textContent = `${n.display_name} (${n.id})`;
    taskNodeFilter.appendChild(op);
  });
  if (old) taskNodeFilter.value = old;
}

function fillMonitorNodeSelect(rows = []) {
  if (!monitorNodeSelect) return;
  const old = monitorNodeSelect.value || "__local__";
  monitorNodeSelect.innerHTML = '<option value="__local__">本机（未分配节点）</option>';
  rows.forEach((n) => {
    const op = document.createElement("option");
    op.value = n.id;
    op.textContent = `${n.display_name} (${n.status})`;
    monitorNodeSelect.appendChild(op);
  });
  if ([...monitorNodeSelect.options].some((o) => o.value === old)) {
    monitorNodeSelect.value = old;
  }
}

function trainJobsForNode(jobs, nodeId) {
  const trains = jobs.filter((j) => j.job_type === "train");
  if (nodeId === "__local__") {
    return trains.filter((j) => !j.assigned_node_id);
  }
  if (nodeId) {
    return trains.filter((j) => j.assigned_node_id === nodeId);
  }
  return trains;
}

function resolveMonitorJob(jobs, nodeId, explicitJobId) {
  const pool = trainJobsForNode(jobs, nodeId);
  if (explicitJobId) {
    return pool.find((j) => j.id === explicitJobId) || jobs.find((j) => j.id === explicitJobId) || null;
  }
  const active = pool
    .filter((j) => j.status === "RUNNING" || j.status === "PENDING_ASSIGN")
    .sort((a, b) => new Date(b.started_at || b.created_at) - new Date(a.started_at || a.created_at));
  return active[0] || null;
}

function fillMonitorJobSelect(jobs, nodeId) {
  if (!monitorJobSelect) return;
  const old = monitorJobSelect.value || "";
  const pool = trainJobsForNode(jobs, nodeId);
  monitorJobSelect.innerHTML = '<option value="">自动（该节点当前运行中）</option>';
  pool
    .filter((j) => j.status === "RUNNING" || j.status === "PENDING_ASSIGN" || j.status === "PENDING")
    .sort((a, b) => new Date(b.started_at || b.created_at) - new Date(a.started_at || a.created_at))
    .forEach((j) => {
      const op = document.createElement("option");
      op.value = j.id;
      const shortId = j.id.slice(0, 8);
      op.textContent = `${shortId}… ${j.status} ${j.project_name || ""}`.trim();
      monitorJobSelect.appendChild(op);
    });
  if (old && [...monitorJobSelect.options].some((o) => o.value === old)) {
    monitorJobSelect.value = old;
  }
}

function computeTrainProgress(metrics, job) {
  if (metrics?.ok) {
    const total = Number(metrics.epochs_total || 0);
    const done = Number(metrics.epochs_done || 0);
    const pct = Number(metrics.progress_percent ?? 0);
    if (total > 0) {
      return { pct, done, total };
    }
  }
  const total = Number(job?.payload?.epochs || 0);
  if (job?.status === "SUCCESS") {
    return { pct: 100, done: total, total };
  }
  return { pct: job?.progress || 0, done: 0, total };
}

async function refreshNodes() {
  try {
    const rows = await apiFetch("/api/v1/nodes");
    nodeRows = Array.isArray(rows) ? rows : [];
    fillNodeSelect(trainNodeSelect, nodeRows);
    fillNodeSelect(testNodeSelect, nodeRows);
    fillNodeSelect(detectNodeSelect, nodeRows);
    fillTaskNodeFilter(nodeRows);
    fillMonitorNodeSelect(nodeRows);
    if (nodesSummary) {
      const online = nodeRows.filter((n) => n.status === "ONLINE").length;
      nodesSummary.textContent = `共 ${nodeRows.length} 台节点，在线 ${online} 台`;
    }
    if (nodesTableBody) {
      nodesTableBody.innerHTML = "";
      if (!nodeRows.length) {
        const tr = document.createElement("tr");
        tr.innerHTML = "<td colspan='9'>暂无节点（请先启动 agent）</td>";
        nodesTableBody.appendChild(tr);
      } else {
        nodeRows.forEach((n) => {
          const caps = n.capabilities || {};
          const gpuCount = caps.gpu_count ? `(${caps.gpu_count})` : "";
          const osLabel = caps.os_type === "windows" ? "Win" : caps.os_type === "linux" ? "Linux" : "?";
          const kindLabel = caps.node_kind === "remote" ? "远程" : "本地";
          const tr = document.createElement("tr");
          tr.innerHTML = `
            <td class="td-id">${safeText(n.id)}</td>
            <td>${safeText(n.display_name)}</td>
            <td class="${n.status === "ONLINE" ? "ok" : "fail"}">${safeText(n.status)}</td>
            <td class="td-kind">${kindLabel}</td>
            <td class="td-os">${osLabel} ${caps.os_type || "-"}</td>
            <td class="td-gpu">${caps.has_gpu ? "✅ GPU" + gpuCount : "❌"}</td>
            <td class="td-num">${safeText(n.max_concurrent_jobs)}</td>
            <td class="td-num">${safeText(n.running_jobs)}</td>
            <td class="td-time">${n.last_seen_at ? formatDisplayTime(n.last_seen_at) : "-"}</td>
          `;
          nodesTableBody.appendChild(tr);
        });
      }
    }
  } catch {
    nodeRows = [];
    fillNodeSelect(trainNodeSelect, []);
    fillNodeSelect(testNodeSelect, []);
    fillNodeSelect(detectNodeSelect, []);
    fillTaskNodeFilter([]);
    fillMonitorNodeSelect([]);
    if (nodesSummary) {
      nodesSummary.textContent = "暂无节点数据";
    }
    if (nodesTableBody) {
      nodesTableBody.innerHTML = "<tr><td colspan='9'>暂无节点数据</td></tr>";
    }
  }
}

async function refreshModelInfo() {
  if (!trainModelInfo) return;
  const yaml = document.querySelector("#sel-train-model")?.value || "";
  const scale = document.querySelector("#sel-train-scale")?.value || "n";
  if (!yaml) {
    trainModelInfo.textContent = "请选择模型以查看信息";
    return;
  }
  try {
    const data = await apiFetch(`/api/v1/options/model-info?yaml_path=${encodeURIComponent(yaml)}&scale=${encodeURIComponent(scale)}`);
    const info = data.info || {};
    if (!info.ok) {
      trainModelInfo.textContent = "无法读取模型信息";
      return;
    }
    const pills = [];
    pills.push(`<span class="pill">YAML: ${info.yaml}</span>`);
    if (info.parameters) pills.push(`<span class="pill">参数量: ${fmtNum(info.parameters)}</span>`);
    if (info.layers) pills.push(`<span class="pill">层数: ${info.layers}</span>`);
    if (info.gflops) pills.push(`<span class="pill">GFLOPS: ${info.gflops}</span>`);
    trainModelInfo.innerHTML = pills.join("");
  } catch {
    trainModelInfo.textContent = "无法读取模型信息（请确认后端已启动）";
  }
}

function fillTestDataSelect(uploadedRows = []) {
  const dSel = document.querySelector("#sel-test-data");
  fillDataSelectWithBuiltinAndUploads(dSel, uploadedRows);
}

function refillWeightSelect(selectId, hiddenId, mode) {
  const sel = document.querySelector(selectId);
  const hidden = document.querySelector(hiddenId);
  if (!sel || !hidden) {
    return;
  }
  sel.innerHTML = "";
  if (mode === "official") {
    const by = {};
    (yoloOptions.official_weights || []).forEach((w) => {
      (by[w.group] ||= []).push(w);
    });
    Object.keys(by).forEach((g) => {
      const og = document.createElement("optgroup");
      og.label = g;
      by[g].forEach((w) => {
        const o = document.createElement("option");
        o.value = w.value;
        o.textContent = w.label;
        og.appendChild(o);
      });
      sel.appendChild(og);
    });
  } else if (!(yoloOptions.user_weights || []).length) {
    const o = document.createElement("option");
    o.value = "";
    o.textContent = "暂无用户 .pt（请先训练，输出在 runs/ 或任务 artifacts）";
    sel.appendChild(o);
  } else {
    (yoloOptions.user_weights || []).forEach((w) => {
      const o = document.createElement("option");
      o.value = w.path;
      o.textContent = w.path;
      sel.appendChild(o);
    });
  }
  hidden.value = sel.value || "";
}

function wireSegmentRow(rowId, selectId, hiddenId, setMode) {
  const row = document.querySelector(rowId);
  if (!row) {
    return;
  }
  row.querySelectorAll(".segment").forEach((btn) => {
    btn.addEventListener("click", () => {
      row.querySelectorAll(".segment").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      const mode = btn.dataset.mode;
      setMode(mode);
      refillWeightSelect(selectId, hiddenId, mode);
    });
  });
}

async function loadYoloOptions() {
  const [yoloSettled, dsSettled] = await Promise.allSettled([
    apiFetch("/api/v1/options/yolo"),
    apiFetch("/api/v1/datasets"),
  ]);

  let datasetsError = null;
  let uploadedRows = [];
  if (dsSettled.status === "fulfilled") {
    const raw = dsSettled.value;
    uploadedRows = Array.isArray(raw) ? raw : [];
  } else {
    datasetsError =
      dsSettled.reason instanceof Error ? dsSettled.reason.message : String(dsSettled.reason);
    console.warn("已上传数据集列表加载失败", dsSettled.reason);
  }

  if (yoloSettled.status === "fulfilled") {
    yoloOptions = yoloSettled.value;
    if (statModels) {
      statModels.textContent = String((yoloOptions?.user_weights || []).length || 0);
    }
    if (trainExpNameInput) {
      try {
        const sug = await apiFetch("/api/v1/options/suggest-train-name?prefix=quudet-train");
        if (sug?.ok && typeof sug.suggested === "string") {
          const cur = (trainExpNameInput.value || "").trim();
          if (!cur || /^quudet-train\\d*$/i.test(cur)) {
            trainExpNameInput.value = sug.suggested;
          }
        }
      } catch {
        // ignore
      }
    }
  } else {
    console.warn("加载 YOLO 选项失败（请确认已启动 API 且仓库含 ultralytics-main）", yoloSettled.reason);
  }

  fillTrainYoloSelects(uploadedRows);
  fillOptimizerSelect();
  fillTestDataSelect(uploadedRows);
  refillWeightSelect("#sel-test-weight", "#resolved-test-model", testWeightMode);
  refillWeightSelect("#sel-detect-weight", "#resolved-detect-model", detectWeightMode);
  updateDatasetSelectHints(uploadedRows, datasetsError);
  await refreshModelInfo().catch(() => {});
}

function defaultApiBase() {
  const saved = localStorage.getItem(API_BASE_KEY);
  if (saved && saved.trim()) {
    return saved.replace(/\/$/, "");
  }
  if (window.location.protocol === "file:") {
    return "http://127.0.0.1:8000";
  }
  if (window.location.port === "8080") {
    return "";
  }
  return "http://127.0.0.1:8000";
}

function apiUrl(path) {
  const base = defaultApiBase();
  const p = path.startsWith("/") ? path : `/${path}`;
  if (!base) {
    return p;
  }
  return `${base}${p}`;
}

function authHeaders() {
  const token = localStorage.getItem(TOKEN_KEY);
  return token ? { Authorization: `Bearer ${token}` } : {};
}

async function apiFetch(path, opts = {}) {
  const headers = { ...(opts.headers || {}), ...authHeaders() };
  const res = await fetch(apiUrl(path), { ...opts, headers });
  if (res.status === 401) {
    localStorage.removeItem(TOKEN_KEY);
    throw new Error("未授权：若后端已开启登录（DISABLE_AUTH=false），请先获取 Token。");
  }
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || res.statusText);
  }
  const ct = res.headers.get("content-type") || "";
  if (ct.includes("application/json")) {
    return res.json();
  }
  return res.text();
}

function collectPayload(sectionRoot) {
  const root = typeof sectionRoot === "string" ? document.querySelector(sectionRoot) : sectionRoot;
  const payload = {};
  root.querySelectorAll("[data-field]").forEach((el) => {
    const key = el.dataset.field;
    const val = (el.value || "").trim();
    if (key && val) {
      payload[key] = val;
    }
  });
  return payload;
}

function collectPayloadFromSelector(sel) {
  return collectPayload(document.querySelector(sel));
}

function initApp() {
  // 直接显示主应用，跳过登录
  const savedApiBase = localStorage.getItem(API_BASE_KEY);
  if (!savedApiBase) {
    // 如果没有设置 API 地址，使用默认值
    localStorage.setItem(API_BASE_KEY, "http://127.0.0.1:8000");
  }
  showApp();
}

function showLogin() {
  if (loginPage) {
    loginPage.style.display = "block";
  }
  if (app) {
    app.style.display = "none";
  }
  // 填充保存的 API 地址
  if (loginApi) {
    const saved = localStorage.getItem(API_BASE_KEY);
    loginApi.value = saved || defaultApiBase() || "http://127.0.0.1:8000";
  }
}

function showApp() {
  if (loginPage) {
    loginPage.style.display = "none";
  }
  if (app) {
    app.style.display = "block";
  }
  startPolling();
  refreshAll();
}

async function doLogin() {
  const email = loginEmail?.value?.trim();
  const password = loginPassword?.value?.trim();
  const apiBase = loginApi?.value?.trim() || defaultApiBase() || "http://127.0.0.1:8000";
  
  if (!email || !password) {
    alert("请输入邮箱和密码");
    return;
  }
  
  try {
    // 保存 API 地址
    localStorage.setItem(API_BASE_KEY, apiBase.replace(/\/$/, ""));
    
    // 调用登录 API - 后端期望 username 和 password
    const res = await fetch(apiUrl("/api/v1/auth/login"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username: email, password }),
    });
    
    if (!res.ok) {
      const err = await res.text();
      throw new Error(err || "登录失败");
    }
    
    const data = await res.json();
    localStorage.setItem(TOKEN_KEY, data.access_token);
    
    // 显示主应用
    showApp();
  } catch (e) {
    alert(`登录失败: ${e.message}`);
  }
}

function toStatusClass(status) {
  if (status === "SUCCESS") {
    return "ok";
  }
  if (status === "FAILED") {
    return "fail";
  }
  return "pending";
}

function formatDisplayTime(iso) {
  if (!iso) {
    return "-";
  }
  const d = new Date(iso);
  return d.toLocaleString('zh-CN', {
    timeZone: 'Asia/Shanghai',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false
  }).replace(/\//g, '-');
}

async function refreshDashboard() {
  const stats = await apiFetch("/api/v1/dashboard/stats");
  if (!statTotal || !statRunning || !statSuccess) {
    return;
  }
  statTotal.textContent = stats.total;
  statRunning.textContent = stats.running;
  statSuccess.textContent = stats.success;
  if (statFailed) {
    statFailed.textContent = stats.failed;
  }
  if (statModels) {
    statModels.textContent = String((yoloOptions?.user_weights || []).length || 0);
  }
}

// 存储上次渲染的任务数据，用于智能更新
let lastJobsData = [];

function renderTaskTables(jobs) {
  if (!recentTaskBody || !taskTableBody) {
    return;
  }
  const selectedNode = taskNodeFilter?.value || "";
  const shownJobs = selectedNode ? jobs.filter((j) => (j.assigned_node_id || "") === selectedNode) : jobs;

  // 完全重新渲染
  lastJobsData = [...shownJobs];
  recentTaskBody.innerHTML = "";
  taskTableBody.innerHTML = "";
  if (!shownJobs.length) {
    const empty = document.createElement("tr");
    empty.innerHTML = '<td colspan="8">暂无任务</td>';
    taskTableBody.appendChild(empty);

    const emptyRecent = document.createElement("tr");
    emptyRecent.innerHTML = '<td colspan="7">暂无最近任务</td>';
    recentTaskBody.appendChild(emptyRecent);
    return;
  }

  const progressCellHtml = (job) => {
    const cached = trainProgressByJob.get(job.id);
    const progress = cached?.pct ?? (job.status === "SUCCESS" ? 100 : (job.progress || 0));
    const text = cached && cached.total > 0
      ? `${cached.done}/${cached.total} (${progress}%)`
      : `${progress}%`;
    return `
      <td data-progress-job="${job.id}">
        <div class="progress-bar">
          <div class="progress-fill" style="width: ${progress}%"></div>
        </div>
        <div class="progress-text">${text}</div>
      </td>
    `;
  };

  shownJobs.slice(0, 5).forEach((job) => {
    const row = document.createElement("tr");
    row.innerHTML = `
      <td>${job.id.slice(0, 12)}...</td>
      <td>${job.job_type}</td>
      <td class="${toStatusClass(job.status)}">${job.status}</td>
      <td>${job.assigned_node_id || "-"}</td>
      ${progressCellHtml(job)}
      <td>${formatDisplayTime(job.created_at)}</td>
      <td class="link" data-id="${job.id}">查看详情</td>
    `;
    row.querySelector(".link").addEventListener("click", () => showTaskDetail(job.id));
    recentTaskBody.appendChild(row);
  });

  shownJobs.forEach((job) => {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${job.id}</td>
      <td>${job.job_type}</td>
      <td class="${toStatusClass(job.status)}">${job.status}</td>
      <td>${job.assigned_node_id || "-"}</td>
      ${progressCellHtml(job)}
      <td>${formatDisplayTime(job.created_at)}</td>
      <td>${job.project_name || "-"}</td>
      <td class="link" data-id="${job.id}">查看详情</td>
    `;
    tr.querySelector(".link").addEventListener("click", () => showTaskDetail(job.id));
    taskTableBody.appendChild(tr);
  });
}

async function refreshJobs() {
  const jobs = await apiFetch("/api/v1/jobs");
  renderTaskTables(jobs);
  renderDetectLatest(jobs).catch(() => {});
  refreshAllTrainProgressByEpoch(jobs).catch(() => {});
}

async function refreshAllTrainProgressByEpoch(allJobs) {
  if (progressRefreshInFlight) return;
  const jobs = Array.isArray(allJobs) ? allJobs : [];
  const trainJobs = jobs.filter((j) => j.job_type === "train").slice(0, 20);
  if (!trainJobs.length) return;
  progressRefreshInFlight = true;
  try {
    await Promise.all(trainJobs.map(async (job) => {
      let pct = job.status === "SUCCESS" ? 100 : 0;
      let done = 0;
      let total = 0;
      try {
        const [detail, metrics] = await Promise.all([
          apiFetch(`/api/v1/jobs/${job.id}`),
          apiFetch(`/api/v1/jobs/${job.id}/metrics`),
        ]);
        const prog = computeTrainProgress(metrics, detail);
        pct = prog.pct;
        done = prog.done;
        total = prog.total;
      } catch {
        pct = job.status === "SUCCESS" ? 100 : (job.progress || 0);
      }
      trainProgressByJob.set(job.id, { pct, done, total });
      const text = total > 0 ? `${done}/${total} (${pct}%)` : `${pct}%`;
      document.querySelectorAll(`[data-progress-job="${job.id}"]`).forEach((cell) => {
        cell.innerHTML = `
          <div class="progress-bar">
            <div class="progress-fill" style="width: ${pct}%"></div>
          </div>
          <div class="progress-text">${text}</div>
        `;
      });
    }));
  } finally {
    progressRefreshInFlight = false;
  }
}

function summarizeDetectResultSummary(s) {
  if (!s || typeof s !== "string") return "";
  const lines = s
    .split(/\r?\n/)
    .map((x) => x.trim())
    .filter(Boolean);
  // Prefer Ultralytics "image ..." lines that usually contain class counts.
  const best = lines.find((ln) => /image\s*\d+\/\d+/i.test(ln)) || lines[lines.length - 1] || "";
  return best;
}

function escapeHtml(s) {
  return String(s)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function extractDetectCaptionsFromLogs(logText) {
  if (!logText || typeof logText !== "string") return [];
  const lines = logText.split(/\r?\n/);
  const segments = [];
  const shapeRe = /\b\d+x\d+\b/;
  for (let i = 0; i < lines.length; i++) {
    const clsLine = lines[i].trim();
    // Ultralytics formats:
    // 1) "640x512 7 persons, ..." (sometimes)
    // 2) "image 1/1 D:\\...\\xxx.jpg: 480x640 4 persons, ..." (common)
    if (!shapeRe.test(clsLine)) continue;
    const looksLikeImageMetrics =
      /^\d+x\d+\s+/i.test(clsLine) || /image\s+\d+\/\d+/i.test(clsLine);
    if (!looksLikeImageMetrics) continue;

    let speedLine = "";
    let learnLine = "";
    for (let j = i + 1; j < Math.min(lines.length, i + 8); j++) {
      const t = lines[j].trim();
      if (!speedLine && /^Speed:/i.test(t)) speedLine = t;
      if (!learnLine && /^Learn more at/i.test(t)) learnLine = t;
    }

    const fileMatch = clsLine.match(/([A-Za-z0-9_.-]+\.(?:jpg|jpeg|png|webp))/i);
    const caption = [clsLine, speedLine, learnLine].filter(Boolean).join("\n");
    segments.push({ fileName: fileMatch ? fileMatch[1] : null, caption });
  }
  return segments;
}

async function renderDetectLatest(allJobs) {
  if (!detectLatestPanel || !detectLatestImages || !detectLatestSummary) return;
  const jobs = Array.isArray(allJobs) ? allJobs : [];

  const latest = jobs
    .filter((j) => j.job_type === "detect")
    .sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime())[0];

  if (!latest) {
    detectLatestPanel.style.display = "none";
    detectLatestImages.innerHTML = "";
    detectLatestSummary.textContent = "";
    return;
  }

  detectLatestPanel.style.display = "block";
  const renderKey = `${latest.id}:${latest.status || "UNKNOWN"}`;
  if (lastRenderedDetectJobKey === renderKey) return;
  lastRenderedDetectJobKey = renderKey;

  if (latest.status !== "SUCCESS") {
    detectLatestSummary.textContent = `检测进行中：${latest.id.slice(0, 12)}...（状态：${latest.status}）`;
    detectLatestImages.innerHTML = "";
    return;
  }

  const inputSrc = latest.payload?.source || "";
  const inputBase = inputSrc ? inputSrc.split(/[\\/]/).pop() : "";
  const summaryLine = summarizeDetectResultSummary(latest.result_summary) || `任务 ${latest.id} 已完成`;
  detectLatestSummary.textContent = inputBase ? `${summaryLine}（输入：${inputBase}）` : summaryLine;

  detectLatestImages.innerHTML = "";
  const [images, logs] = await Promise.all([
    apiFetch(`/api/v1/jobs/${latest.id}/images`),
    apiFetch(`/api/v1/jobs/${latest.id}/logs?tail=12000`),
  ]);

  if (images.ok && Array.isArray(images.images) && images.images.length) {
    const captions = extractDetectCaptionsFromLogs(logs?.content || "");
    const lastCaption = captions.length ? captions[captions.length - 1]?.caption || "" : "";
    const captionByFile = {};
    (captions || []).forEach((c) => {
      if (c?.fileName && !captionByFile[c.fileName]) {
        captionByFile[c.fileName] = c.caption;
      }
    });

    detectLatestImages.innerHTML = images.images
      .map((it, idx) => {
        const baseName = (it.path || "").split("/").pop() || "";
        const capRaw = captionByFile[baseName] || captions[idx]?.caption || lastCaption || "（未解析到检测概述）";
        const cap = inputBase ? `输入：${inputBase}\n${capRaw}` : capRaw;
        const capEsc = escapeHtml(cap);
        return `
          <div class="task-detail-tile">
            <img src="${apiUrl(it.url)}" alt="detect result">
            <div class="task-detail-caption"><pre>${capEsc}</pre></div>
          </div>
        `;
      })
      .join("");
  }
}



async function refreshMonitorChart() {
  try {
    const jobs = await apiFetch("/api/v1/jobs");
    const nodeId = monitorNodeSelect?.value || "__local__";
    const explicitJobId = monitorJobSelect?.value || "";
    fillMonitorJobSelect(jobs, nodeId);
    const latestJob = resolveMonitorJob(jobs, nodeId, explicitJobId);

    if (!latestJob) {
      if (currentTrainInfo) {
        const nodeLabel =
          nodeId === "__local__"
            ? "本机"
            : nodeRows.find((n) => n.id === nodeId)?.display_name || nodeId;
        currentTrainInfo.textContent = `${nodeLabel}：当前无运行中的训练任务`;
      }
      if (metricChartsContainer) {
        metricChartsContainer.innerHTML = '<div style="text-align: center; color: var(--text-secondary); padding: 40px;">暂无训练任务</div>';
      }
      // 清空左侧信息
      if (monitorTaskId) monitorTaskId.textContent = "-";
      if (monitorProgressFill) monitorProgressFill.style.width = "0%";
      if (monitorProgressText) monitorProgressText.textContent = "-";
      if (monitorMetricsValues) monitorMetricsValues.innerHTML = "";
      // 清空图表实例
      metricCharts = {};
      return;
    }
    
    const nodeLabel =
      latestJob.assigned_node_id
        ? nodeRows.find((n) => n.id === latestJob.assigned_node_id)?.display_name || latestJob.assigned_node_id
        : "本机";
    if (currentTrainInfo) {
      currentTrainInfo.textContent = `监控 ${nodeLabel} · ${latestJob.id.slice(0, 8)}… (${latestJob.status})`;
    }

    const latestJobDetail = await apiFetch(`/api/v1/jobs/${latestJob.id}`);

    if (monitorTaskId) {
      monitorTaskId.textContent = `任务ID: ${latestJob.id} · 节点: ${latestJob.assigned_node_id || "本机"}`;
    }

    const metrics = await apiFetch(`/api/v1/jobs/${latestJob.id}/metrics`);
    if (!metrics.ok) {
      console.log("暂无指标数据:", metrics.reason);
      const progPending = computeTrainProgress(metrics, latestJobDetail);
      if (monitorProgressFill && monitorProgressText) {
        monitorProgressFill.style.width = `${progPending.pct}%`;
        monitorProgressText.textContent =
          progPending.total > 0
            ? `${progPending.done}/${progPending.total} (${progPending.pct}%)`
            : `${progPending.pct}%`;
      }
      if (metricChartsContainer) {
        const box = "div";
        metricChartsContainer.innerHTML =
          `<${box} style="text-align: center; color: var(--text-secondary); padding: 40px;">暂无指标数据: ${metrics.reason || "等待 results.csv"}</${box}>`;
      }
      if (monitorMetricsValues) monitorMetricsValues.innerHTML = "";
      metricCharts = {};
      return;
    }
    if (metrics.type === "txt") {
      console.log("该任务只有 results.txt，暂时不支持自动曲线解析");
      if (metricChartsContainer) {
        metricChartsContainer.innerHTML = '<div style="text-align: center; color: var(--text-secondary); padding: 40px;">该任务只有 results.txt，暂时不支持自动曲线解析</div>';
      }
      if (monitorMetricsValues) monitorMetricsValues.innerHTML = "";
      metricCharts = {};
      return;
    }
    
    const series = metrics.series || {};
    const keys = Object.keys(series);
    if (!keys.length) {
      console.log("results.csv 无可用指标列");
      if (metricChartsContainer) {
        metricChartsContainer.innerHTML = '<div style="text-align: center; color: var(--text-secondary); padding: 40px;">results.csv 无可用指标列</div>';
      }
      if (monitorMetricsValues) monitorMetricsValues.innerHTML = "";
      metricCharts = {};
      return;
    }

    const x = metrics.x || [];
    const prog = computeTrainProgress(metrics, latestJobDetail);
    if (monitorProgressFill && monitorProgressText) {
      monitorProgressFill.style.width = `${prog.pct}%`;
      monitorProgressText.textContent =
        prog.total > 0 ? `${prog.done}/${prog.total} (${prog.pct}%)` : `${prog.pct}%`;
    }
    trainProgressByJob.set(latestJob.id, prog);
    
    const keyMetricRules = [
      { key: "box_loss", label: "Box Loss", color: "#F53F3F" },
      { key: "cls_loss", label: "Cls Loss", color: "#F53F3F" },
      { key: "dfl_loss", label: "DFL Loss", color: "#F53F3F" },
      { key: "mAP50-95", label: "mAP50-95", color: "#00B42A" },
      { key: "mAP50", label: "mAP50", color: "#00B42A" },
      { key: "precision", label: "Precision", color: "#165DFF" },
      { key: "recall", label: "Recall", color: "#FF7D00" },
    ];
    const pickMetricKey = (needle) => keys.find((k) => k.toLowerCase().includes(needle.toLowerCase()));
    const keyMetricItems = keyMetricRules
      .map((rule) => ({ rule, key: pickMetricKey(rule.key) }))
      .filter((it) => !!it.key);

    // 更新左侧关键指标数值
    if (monitorMetricsValues) {
      let metricsHtml = '';
      keyMetricItems.forEach(({ rule, key }) => {
        const values = series[key] || [];
        const latestValue = values.length > 0 ? values[values.length - 1] : 0;
        metricsHtml += `<div style="display: flex; justify-content: space-between; align-items: center;">
          <span style="color: var(--text-secondary);">${rule.label}:</span>
          <span style="color: ${rule.color}; font-weight: 600;">${Number(latestValue).toFixed(4)}</span>
        </div>`;
      });
      monitorMetricsValues.innerHTML = metricsHtml;
    }
    
    // 清空图表容器
    if (metricChartsContainer) {
      metricChartsContainer.innerHTML = '';
    }
    
    // 绘制核心指标曲线（loss/mAP/precision/recall）
    const plotKeys = keys.filter((k) => /(loss|map|precision|recall)/i.test(k));
    (plotKeys.length ? plotKeys : keys).forEach((k) => {
      // 为不同类型的指标设置不同颜色
      let color = getRandomColor();
      if (k.includes("mAP")) {
        color = 'rgba(75, 192, 192, 0.8)'; // 绿色 - mAP 相关
      } else if (k.includes("loss")) {
        color = 'rgba(255, 99, 132, 0.8)'; // 红色 - 损失相关
      } else if (k.includes("precision")) {
        color = 'rgba(54, 162, 235, 0.8)'; // 蓝色 - 精度相关
      } else if (k.includes("recall")) {
        color = 'rgba(255, 206, 86, 0.8)'; // 黄色 - 召回率相关
      }
      
      // 创建图表容器（固定高度，防止布局抖动）
      const chartItem = document.createElement('div');
      chartItem.className = 'chart-item';
      const safeId = k.replace(/[^a-zA-Z0-9]/g, '-');
      chartItem.innerHTML = `
        <h3>${k}</h3>
        <div class="chart-canvas-wrap">
          <canvas id="chart-${safeId}"></canvas>
        </div>
      `;
      if (metricChartsContainer) {
        metricChartsContainer.appendChild(chartItem);
      }
      
      // 获取 canvas 元素
      const canvasId = `chart-${safeId}`;
      const canvas = document.getElementById(canvasId);
      if (!canvas) return;
      
      // 创建或更新图表
      const chartData = {
        labels: x,
        datasets: [{
          label: k,
          data: series[k] || [],
          borderColor: color,
          backgroundColor: color.replace('0.8', '0.1'),
          borderWidth: 2,
          tension: 0.25,
          pointRadius: 2,
          pointHoverRadius: 4
        }]
      };
      
      const chartConfig = {
        type: 'line',
        data: chartData,
        options: {
          responsive: true,
          maintainAspectRatio: false,
          plugins: {
            legend: {
              display: false
            },
            tooltip: {
              mode: 'index',
              intersect: false
            }
          },
          scales: {
            x: {
              title: {
                display: true,
                text: 'Epoch'
              }
            },
            y: {
              title: {
                display: true,
                text: 'Value'
              },
              beginAtZero: k.includes('loss')
            }
          }
        }
      };
      
      // 销毁旧图表
      if (metricCharts[k]) {
        metricCharts[k].destroy();
      }
      
      // 创建新图表
      metricCharts[k] = new Chart(canvas, chartConfig);
    });
  } catch (e) {
    console.error("刷新监控图表失败:", e);
    if (metricChartsContainer) {
      metricChartsContainer.innerHTML = `<div style="text-align: center; color: var(--danger); padding: 40px;">刷新图表失败: ${e.message}</div>`;
    }
    metricCharts = {};
  }
}

function getRandomColor() {
  // 生成随机颜色
  const letters = '0123456789ABCDEF';
  let color = 'rgba(';
  for (let i = 0; i < 3; i++) {
    color += parseInt(letters[Math.floor(Math.random() * 16)]) * 16 + parseInt(letters[Math.floor(Math.random() * 16)]);
    if (i < 2) color += ',';
  }
  color += ', 0.8)';
  return color;
}

async function showTaskDetail(jobId) {
  lastDetailJobId = jobId;
  const job = await apiFetch(`/api/v1/jobs/${jobId}`);
  taskDetailContent.textContent = JSON.stringify(job, null, 2);
  if (taskDetailImages) {
    taskDetailImages.innerHTML = "";
    try {
      const [images, logs] = await Promise.all([
        apiFetch(`/api/v1/jobs/${jobId}/images`),
        apiFetch(`/api/v1/jobs/${jobId}/logs?tail=12000`),
      ]);
      if (images.ok && Array.isArray(images.images) && images.images.length) {
        const captions = extractDetectCaptionsFromLogs(logs?.content || "");
        const lastCaption = captions.length ? captions[captions.length - 1]?.caption || "" : "";
        const captionByFile = {};
        (captions || []).forEach((c) => {
          if (c?.fileName && !captionByFile[c.fileName]) {
            captionByFile[c.fileName] = c.caption;
          }
        });
        const inputSrc = job?.payload?.source || "";
        const inputBase = inputSrc ? inputSrc.split(/[\\/]/).pop() : "";
        taskDetailImages.innerHTML = images.images
          .map((it, idx) => {
            const baseName = (it.path || "").split("/").pop() || "";
            const capRaw = captionByFile[baseName] || captions[idx]?.caption || lastCaption || "（未解析到检测概述）";
            const cap = inputBase ? `输入：${inputBase}\n${capRaw}` : capRaw;
            const capEsc = escapeHtml(cap);
            return `
              <div class="task-detail-tile">
                <img src="${apiUrl(it.url)}" alt="result">
                <div class="task-detail-caption"><pre>${capEsc}</pre></div>
              </div>
            `;
          })
          .join("");
      } else {
        taskDetailImages.innerHTML = "";
      }
    } catch {
      taskDetailImages.innerHTML = "";
    }
  }
  taskDetailDialog.showModal();
}

async function showTaskLogs() {
  if (!lastDetailJobId) {
    return;
  }
  const logs = await apiFetch(`/api/v1/jobs/${lastDetailJobId}/logs?tail=12000`);
  taskDetailContent.textContent = logs.content || "";
}

async function createJob(jobType, payload, projectName, datasetId, extra = {}) {
  const targetNodeId = payload._target_node_id || null;
  delete payload._target_node_id;
  const body = {
    job_type: jobType,
    project_name: projectName || payload.project || null,
    dataset_id: datasetId,
    target_node_id: targetNodeId,
    execution_target: extra.execution_target || null,
    required_gpu: extra.required_gpu || false,
    payload,
  };
  const job = await apiFetch("/api/v1/jobs", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  alert(`任务已创建：${job.id}`);
  await refreshAll();
}

async function refreshAll() {
  try {
    await refreshDashboard();
    await refreshJobs();
    await refreshNodes();
    refreshMonitorChart().catch(() => {});
    renderDatasetHint();
  } catch (e) {
    console.error(e);
  }
  await loadYoloOptions();
}

function renderDatasetHint() {
  const id = localStorage.getItem(LAST_DATASET_ID_KEY);
  datasetInfo.textContent = id ? `最近上传的数据集 ID：${id}（可选关联到任务）` : "尚未上传数据集（可直接用内置示例路径跑通流程）";
}

function getLastDatasetId() {
  const raw = localStorage.getItem(LAST_DATASET_ID_KEY);
  return raw ? Number(raw) : null;
}

function startPolling() {
  stopPolling();
  // 每5秒刷新一次，平衡实时性和用户体验
  pollTimer = setInterval(async () => {
    try {
      // 获取最新任务数据
      const jobs = await apiFetch("/api/v1/jobs");
      
      // 智能更新表格（只更新进度，不重新渲染整个表格）
      renderTaskTables(jobs);
      
      // 刷新仪表盘统计
      await refreshDashboard();
      
      // 如果当前在监控页面，刷新图表
      const monitorPage = document.querySelector("#monitor");
      if (monitorPage && monitorPage.classList.contains("active")) {
        await refreshMonitorChart().catch(() => {});
      }
    } catch (e) {
      console.error("轮询刷新失败:", e);
    }
  }, 5000);
}

function stopPolling() {
  if (pollTimer) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
}

async function uploadDataset(file) {
  const fd = new FormData();
  fd.append("file", file);
  const res = await fetch(apiUrl("/api/v1/datasets/upload"), {
    method: "POST",
    headers: authHeaders(),
    body: fd,
  });
  if (res.status === 401) {
    localStorage.removeItem(TOKEN_KEY);
    alert("上传失败：后端可能要求登录（DISABLE_AUTH=false）。");
    return;
  }
  if (!res.ok) {
    alert(await res.text());
    return;
  }
  const data = await res.json();
  localStorage.setItem(LAST_DATASET_ID_KEY, String(data.id));
  renderDatasetHint();
  await refreshUploadedDatasetOptions(data.id);
  const hint = data.data_yaml
    ? "已在「数据集配置」中选择该上传项。"
    : "未识别到 data.yaml：请确认 ZIP 内含 yaml，或改为直接上传 data.yaml。";
  alert(`上传成功，数据集 ID=${data.id}\n${hint}`);
}

async function refreshUploadedDatasetOptions(preferredUploadId = null) {
  let uploadedRows = [];
  let err = null;
  try {
    const raw = await apiFetch("/api/v1/datasets");
    uploadedRows = Array.isArray(raw) ? raw : [];
  } catch (e) {
    err = e instanceof Error ? e.message : String(e);
  }
  fillDataSelectWithBuiltinAndUploads(document.querySelector("#sel-train-data"), uploadedRows, preferredUploadId);
  fillDataSelectWithBuiltinAndUploads(document.querySelector("#sel-test-data"), uploadedRows, preferredUploadId);
  updateDatasetSelectHints(uploadedRows, err);
}

async function uploadDetectFile(file) {
  const fd = new FormData();
  fd.append("file", file);
  const res = await fetch(apiUrl("/api/v1/uploads/detect-file"), {
    method: "POST",
    headers: authHeaders(),
    body: fd,
  });
  if (!res.ok) {
    throw new Error(await res.text());
  }
  return res.json();
}

menuItems.forEach((btn) => {
  btn.addEventListener("click", () => {
    menuItems.forEach((item) => item.classList.remove("active"));
    pages.forEach((page) => page.classList.remove("active"));
    btn.classList.add("active");
    const page = document.getElementById(btn.dataset.page);
    if (page) {
      page.classList.add("active");
    }
  });
});

if (apiBaseSave && apiBaseInput) {
  apiBaseSave.addEventListener("click", () => {
    const v = (apiBaseInput.value || "").trim();
    if (v) {
      localStorage.setItem(API_BASE_KEY, v.replace(/\/$/, ""));
    } else {
      localStorage.removeItem(API_BASE_KEY);
    }
    refreshAll().catch((e) => console.error(e));
  });
}

if (monitorRefreshBtn) {
  monitorRefreshBtn.addEventListener("click", () => {
    refreshMonitorChart().catch((e) => alert(e.message));
  });
}

monitorNodeSelect?.addEventListener("change", () => {
  if (monitorJobSelect) monitorJobSelect.value = "";
  refreshMonitorChart().catch((e) => console.error(e));
});

monitorJobSelect?.addEventListener("change", () => {
  refreshMonitorChart().catch((e) => console.error(e));
});

if (nodesRefreshBtn) {
  nodesRefreshBtn.addEventListener("click", () => {
    refreshNodes().catch((e) => alert(e.message));
  });
}

// 实时监控功能 - 定时刷新训练曲线
let monitorPollTimer = null;



datasetUploadBtn?.addEventListener("click", () => datasetFileInput?.click());
datasetFileInput?.addEventListener("change", () => {
  const [file] = datasetFileInput.files;
  if (!file) {
    return;
  }
  uploadDataset(file).catch((e) => alert(e.message));
});

detectFilePickBtn?.addEventListener("click", () => detectFileInput?.click());
detectFileInput?.addEventListener("change", () => {
  const f = detectFileInput?.files?.[0];
  if (detectFileName) {
    detectFileName.textContent = f ? f.name : "未选择";
  }
  if (resolvedDetectSource) {
    resolvedDetectSource.value = "";
  }
});

function readExecutionTarget(prefix) {
  const sel = document.querySelector(`#sel-${prefix}-target`);
  return sel ? sel.value || null : null;
}
function readRequireGpu(prefix) {
  const chk = document.querySelector(`#chk-${prefix}-require-gpu`);
  return chk ? chk.checked : false;
}

startTrainBtn?.addEventListener("click", () => {
  syncTrainModel();
  const payload = collectPayloadFromSelector("#dataset");
  payload._target_node_id = trainNodeSelect?.value || "";
  const ds = getSelectedUploadIdFromDataSelect("#sel-train-data");
  createJob("train", payload, payload.project, ds, {
    execution_target: readExecutionTarget("train"),
    required_gpu: readRequireGpu("train"),
  }).catch((e) => alert(e.message));
});

startTrainBtn2?.addEventListener("click", () => {
  syncTrainModel();
  const payload = collectPayloadFromSelector("#dataset");
  payload._target_node_id = trainNodeSelect?.value || "";
  const ds = getSelectedUploadIdFromDataSelect("#sel-train-data");
  createJob("train", payload, payload.project, ds, {
    execution_target: readExecutionTarget("train"),
    required_gpu: readRequireGpu("train"),
  }).catch((e) => alert(e.message));
});

gotoDatasetTrain?.addEventListener("click", () => {
  document.querySelector('[data-page="dataset"]')?.click();
});

startTestBtn?.addEventListener("click", () => {
  const h = document.querySelector("#resolved-test-model");
  const s = document.querySelector("#sel-test-weight");
  if (h && s) {
    h.value = s.value || "";
  }
  const payload = collectPayloadFromSelector("#test");
  payload._target_node_id = testNodeSelect?.value || "";
  if (!payload.model) {
    alert("请选择模型权重");
    return;
  }
  const ds = getSelectedUploadIdFromDataSelect("#sel-test-data");
  createJob("val", payload, payload.project, ds, {
    execution_target: readExecutionTarget("test"),
  }).catch((e) => alert(e.message));
});

startDetectBtn?.addEventListener("click", () => {
  (async () => {
    const h = document.querySelector("#resolved-detect-model");
    const s = document.querySelector("#sel-detect-weight");
    if (h && s) {
      h.value = s.value || "";
    }

    const file = detectFileInput?.files?.[0];
    if (!file) {
      alert("请选择要检测的图片/视频文件");
      return;
    }

    const uploaded = await uploadDetectFile(file);
    if (resolvedDetectSource) {
      resolvedDetectSource.value = uploaded?.stored_path || "";
    }

    const payload = collectPayloadFromSelector("#detect");
    payload._target_node_id = detectNodeSelect?.value || "";
    if (!payload.model) {
      alert("请选择模型权重");
      return;
    }
    if (!payload.source) {
      alert("检测文件上传失败：未获取到 source 路径");
      return;
    }
    const ds = getLastDatasetId();
    createJob("detect", payload, payload.project, ds, {
      execution_target: readExecutionTarget("detect"),
    }).catch((e) => alert(e.message));
  })().catch((e) => alert(e.message));
});

refreshTaskBtn?.addEventListener("click", () => refreshAll().catch((e) => alert(e.message)));

clearTaskBtn?.addEventListener("click", () => {
  if (!confirm("确认清空全部任务？")) {
    return;
  }
  apiFetch("/api/v1/jobs", { method: "DELETE" })
    .then(() => refreshAll())
    .catch((e) => alert(e.message));
});

taskNodeFilter?.addEventListener("change", () => {
  refreshJobs().catch((e) => alert(e.message));
});

closeDialogBtn?.addEventListener("click", () => taskDetailDialog?.close());
taskLogsBtn?.addEventListener("click", () => showTaskLogs().catch((e) => alert(e.message)));
taskDetailDialog?.addEventListener("click", (event) => {
  if (event.target === taskDetailDialog) {
    taskDetailDialog.close();
  }
});

document.querySelector("#sel-train-model")?.addEventListener("change", syncTrainModel);
document.querySelector("#sel-train-scale")?.addEventListener("change", syncTrainModel);
document.querySelector("#sel-test-weight")?.addEventListener("change", () => {
  const h = document.querySelector("#resolved-test-model");
  const s = document.querySelector("#sel-test-weight");
  if (h && s) {
    h.value = s.value || "";
  }
});
document.querySelector("#sel-detect-weight")?.addEventListener("change", () => {
  const h = document.querySelector("#resolved-detect-model");
  const s = document.querySelector("#sel-detect-weight");
  if (h && s) {
    h.value = s.value || "";
  }
});

wireSegmentRow("#seg-test-weight", "#sel-test-weight", "#resolved-test-model", (m) => {
  testWeightMode = m;
});
wireSegmentRow("#seg-detect-weight", "#sel-detect-weight", "#resolved-detect-model", (m) => {
  detectWeightMode = m;
});

// 登录按钮事件
if (loginBtn) {
  loginBtn.addEventListener("click", () => {
    doLogin().catch((e) => alert(e.message));
  });
}

// 回车键登录
if (loginPassword) {
  loginPassword.addEventListener("keypress", (e) => {
    if (e.key === "Enter") {
      doLogin().catch((err) => alert(err.message));
    }
  });
}

initApp();
