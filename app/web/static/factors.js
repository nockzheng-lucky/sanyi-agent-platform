"use strict";

const LOADED_FACTORS_KEY = "sanyi.loadedFactors";
const factorGrid = document.getElementById("factorGrid");
const factorQuick = document.getElementById("factorQuick");
const factorEmpty = document.getElementById("factorEmpty");
const factorError = document.getElementById("factorError");
const loadedCount = document.getElementById("loadedCount");
const factorPageTitle = document.getElementById("factorPageTitle");
const factorPageSubtitle = document.getElementById("factorPageSubtitle");

const PAGE_DOMAIN = window.location.pathname.indexOf("/crypto") === 0 ? "crypto" : "futures";
const IS_CRYPTO = PAGE_DOMAIN === "crypto";

const GROUP_DEFINITIONS = [
  {
    key: "boundary",
    label: "三易分界 · 52 / 208 / 832",
    factorKeys: [`${PAGE_DOMAIN}_ma`],
    note: "52、208、832 不按传统均线理解，不提供支撑/压力。它们只做一件事：标记当前处于多头区还是空头区。三线共同构成大级别多空分界带，价格穿过边界只表示进入或离开对应区域，不代表会反弹或受阻。",
  },
  {
    key: "alignment",
    label: "多空排列三线 · 25 / 144 / 169",
    factorKeys: [`${PAGE_DOMAIN}_ma_triple`],
    note: "25、144、169 是真正的均线，用来判断排列是否健康。完整多头排列：25 > 144 > 169；完整空头排列：25 < 144 < 169。单条关系只能看局部，不能当作整体趋势确认。",
  },
  {
    key: "structure",
    label: "结构分界因子",
    factorKeys: [`${PAGE_DOMAIN}_door`, `${PAGE_DOMAIN}_spatial`, `${PAGE_DOMAIN}_tf`],
    note: "门是结构分界，不是传统背离指标。地门/天门标记多空结构边界；门价与均线看门所在的空间位置；跨级别看父级/子级的门与走势段结构。它们用于识别结构是否形成、打开、关闭，不等于买卖点。",
  },
  {
    key: "other",
    label: "其他因子",
    factorKeys: [
      `${PAGE_DOMAIN}_price`,
      `${PAGE_DOMAIN}_macd`,
      `${PAGE_DOMAIN}_rsi`,
      `${PAGE_DOMAIN}_segment`,
      `${PAGE_DOMAIN}_jue`,
    ],
    note: "价格、MACD、RSI、走势段、诀，用于补充行情强弱、动能和节奏。它们通常配合上面的分界因子一起使用，单看一个不构成信号。",
  },
];

const QUICK_COMBOS = [
  {
    key: "combo-parent-walk2-child-di-open",
    name: "大级别走2 + 小级别地门开",
    description: "例如 1h 走2 + 15m 地门开；组合因子板块，后续固定开放。",
    factors: ["segment", "door"],
    available: false,
  },
];

function setupPageCopy() {
  document.title = (IS_CRYPTO ? "币圈因子" : "期货因子") + " · 三易引擎";
  if (factorPageTitle) factorPageTitle.textContent = IS_CRYPTO ? "币圈因子" : "期货因子";
  if (factorPageSubtitle) {
    factorPageSubtitle.textContent =
      "因子按 三易分界 / 多空排列三线 / 结构分界 / 其他 分组展示。点每组标题旁的 ? 查看说明。";
  }
}

async function api(url, options = {}) {
  const resp = await fetch(url, {
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  const body = await resp.json().catch(() => ({}));
  if (!resp.ok) {
    throw new Error(body.detail?.message || body.message || `请求失败（${resp.status}）`);
  }
  return body.data;
}

function getLoadedFactors() {
  try {
    const value = JSON.parse(localStorage.getItem(LOADED_FACTORS_KEY) || "[]");
    if (!Array.isArray(value)) return [];
    return value.filter((item) => item && typeof item.factorKey === "string" && item.factorKey);
  } catch (_e) {
    return [];
  }
}

function saveLoadedFactors(factors) {
  localStorage.setItem(LOADED_FACTORS_KEY, JSON.stringify(factors));
}

function isLoaded(factorKey) {
  return getLoadedFactors().some((item) => item.factorKey === factorKey);
}

function setLoaded(factor, loaded) {
  let factors = getLoadedFactors().filter((item) => item.factorKey !== factor.factorKey);
  if (loaded) factors.push({ factorKey: factor.factorKey, name: factor.name });
  saveLoadedFactors(factors);
  updateLoadedCount();
}

function updateLoadedCount() {
  if (loadedCount) loadedCount.textContent = `已加载 ${getLoadedFactors().length} 个`;
}

function showError(message) {
  if (factorError) factorError.textContent = message || "";
}

function factorCostText(factor) {
  if (factor.cost === 0) return "订阅内";
  return `${factor.cost} 点/次`;
}

function renderQuickCombos() {
  if (!factorQuick) return;
  factorQuick.innerHTML = "";
  const head = document.createElement("div");
  head.className = "section-head";
  head.textContent = "组合因子（快速调用）";
  factorQuick.appendChild(head);
  const hint = document.createElement("p");
  hint.className = "section-hint";
  hint.textContent = "这里后续放固定组合，例如“大级别走2 + 小级别地门开”，点击即可快速调用。";
  factorQuick.appendChild(hint);
  const grid = document.createElement("div");
  grid.className = "combo-grid";
  for (const combo of QUICK_COMBOS) {
    const card = document.createElement("div");
    card.className = "combo-card";
    const name = document.createElement("div");
    name.className = "combo-name";
    name.textContent = combo.name;
    const desc = document.createElement("div");
    desc.className = "combo-desc";
    desc.textContent = combo.description;
    const badge = document.createElement("span");
    badge.className = combo.available ? "f-badge active" : "f-badge inactive";
    badge.textContent = combo.available ? "可用" : "即将开放";
    card.appendChild(name);
    card.appendChild(desc);
    card.appendChild(badge);
    grid.appendChild(card);
  }
  factorQuick.appendChild(grid);
}

function renderFactor(factor, container) {
  const loaded = isLoaded(factor.factorKey);
  const card = document.createElement("div");
  card.className = factor.status === "active" ? "factor-card" : "factor-card inactive";
  if (loaded) card.classList.add("loaded");

  const head = document.createElement("div");
  head.className = "f-head";
  const name = document.createElement("div");
  name.className = "f-name";
  name.textContent = factor.name || factor.factorKey;
  const badge = document.createElement("span");
  badge.className = factor.status === "active" ? "f-badge active" : "f-badge inactive";
  badge.textContent = factor.status === "active" ? "基础因子" : "下线";
  head.appendChild(name);
  head.appendChild(badge);

  const key = document.createElement("div");
  key.className = "f-key";
  key.textContent = factor.factorKey;

  const desc = document.createElement("div");
  desc.className = "f-desc";
  desc.textContent = factor.description || "";

  const meta = document.createElement("div");
  meta.className = "f-meta";
  const tags = (factor.tags || []).slice(0, 4).join(" · ");
  meta.textContent = [factorCostText(factor), tags].filter(Boolean).join(" · ");

  const actions = document.createElement("div");
  actions.className = "f-actions";
  const loadBtn = document.createElement("button");
  loadBtn.type = "button";
  loadBtn.className = "load-btn" + (loaded ? " loaded" : "");
  loadBtn.textContent = loaded ? "取消加载" : "加载到 Agent";
  const askBtn = document.createElement("button");
  askBtn.type = "button";
  askBtn.textContent = "去 Agent 提问";

  if (factor.status === "active") {
    loadBtn.addEventListener("click", () => {
      const next = !isLoaded(factor.factorKey);
      setLoaded(factor, next);
      loadBtn.textContent = next ? "取消加载" : "加载到 Agent";
      loadBtn.classList.toggle("loaded", next);
      card.classList.toggle("loaded", next);
    });
    askBtn.addEventListener("click", () => {
      if (!isLoaded(factor.factorKey)) setLoaded(factor, true);
      window.location.href = "/chat";
    });
  } else {
    loadBtn.disabled = true;
    askBtn.disabled = true;
  }
  actions.appendChild(loadBtn);
  actions.appendChild(askBtn);
  card.appendChild(head);
  card.appendChild(key);
  if (desc.textContent) card.appendChild(desc);
  card.appendChild(meta);
  card.appendChild(actions);
  container.appendChild(card);
}

function renderBasicGroups(factors) {
  factorGrid.innerHTML = "";
  if (factorEmpty) factorEmpty.style.display = "none";
  if (!factors.length) {
    if (factorEmpty) {
      factorEmpty.textContent = IS_CRYPTO ? "暂无可用的币圈基础因子。" : "暂无可用的期货基础因子。";
      factorEmpty.style.display = "block";
    }
    return;
  }
      for (const definition of GROUP_DEFINITIONS) {
      const items = factors.filter((factor) => definition.factorKeys.includes(factor.factorKey));
      if (!items.length) continue;
      const details = document.createElement("details");
      details.className = "factor-group";
      details.open = true;
      const summary = document.createElement("summary");
      summary.className = "factor-group-summary";
      const label = document.createElement("span");
      label.textContent = definition.label;
      const count = document.createElement("span");
      count.className = "factor-group-count";
      count.textContent = `${items.length} 个`;
      const help = document.createElement("button");
      help.type = "button";
      help.className = "factor-group-help";
      help.textContent = "?";
      help.setAttribute("aria-label", "查看说明");
      summary.appendChild(label);
      summary.appendChild(count);
      summary.appendChild(help);
      details.appendChild(summary);

      const note = document.createElement("div");
      note.className = "factor-group-note";
      note.textContent = definition.note;
      details.appendChild(note);

      const grid = document.createElement("div");
      grid.className = "factor-grid";
      for (const factor of items) renderFactor(factor, grid);
      details.appendChild(grid);
      factorGrid.appendChild(details);

      help.addEventListener("click", (event) => {
        event.preventDefault();
        event.stopPropagation();
        note.classList.toggle("open");
      });
  }
}

async function loadFactors() {
  setupPageCopy();
  renderQuickCombos();
  try {
    const data = await api("/api/v1/factors?domain=" + PAGE_DOMAIN);
    const factors = (data && data.factors) || [];
    renderBasicGroups(factors);
    updateLoadedCount();
  } catch (err) {
    if (err.message.includes("401") || err.message.includes("登录已失效")) {
      window.location.href = "/login";
      return;
    }
    if (factorEmpty) {
      factorEmpty.textContent = `因子加载失败：${err.message}`;
      factorEmpty.style.display = "block";
    }
    showError(err.message);
  }
}

updateLoadedCount();
loadFactors();

window.addEventListener("pageshow", (event) => {
  if (event.persisted) loadFactors();
});
