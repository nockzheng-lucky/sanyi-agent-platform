"use strict";

const LOADED_FACTORS_KEY = "sanyi.loadedFactors";
const factorGrid = document.getElementById("factorGrid");
const factorEmpty = document.getElementById("factorEmpty");
const factorError = document.getElementById("factorError");
const loadedCount = document.getElementById("loadedCount");
const factorPageTitle = document.getElementById("factorPageTitle");
const factorPageSubtitle = document.getElementById("factorPageSubtitle");

const PAGE_DOMAIN = window.location.pathname.indexOf("/crypto") === 0 ? "crypto" : "futures";
const IS_CRYPTO = PAGE_DOMAIN === "crypto";

function setupPageCopy() {
  document.title = (IS_CRYPTO ? "币圈因子" : "因子列表") + " · 三易引擎";
  if (factorPageTitle) factorPageTitle.textContent = IS_CRYPTO ? "币圈因子" : "因子列表";
  if (factorPageSubtitle) {
    factorPageSubtitle.textContent = IS_CRYPTO
      ? "影子模式专属：把币圈因子“加载到 Agent”后，订阅和筛选流程与期货因子一致。"
      : "把因子“加载到 Agent”后，回到 Agent 页面会看到已加载提醒，对话时会优先使用这些因子。";
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

function factorCostText(factor) {
  if (factor.cost === 0) return "订阅内";
  return `${factor.cost} 点/次`;
}

function showError(message) {
  if (factorError) factorError.textContent = message || "";
}

function renderFactor(factor) {
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
  badge.textContent = factor.status === "active" ? "可用" : "下线";
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
      if (!isLoaded(factor.factorKey)) {
        setLoaded(factor, true);
      }
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
  factorGrid.appendChild(card);
}

async function loadFactors() {
  setupPageCopy();
  try {
    const data = await api("/api/v1/factors?domain=" + PAGE_DOMAIN);
    const factors = (data && data.factors) || [];
    factorGrid.querySelectorAll(".factor-card").forEach((el) => el.remove());
    if (factorEmpty) factorEmpty.style.display = "none";
    for (const factor of factors) renderFactor(factor);
    updateLoadedCount();
    if (!factors.length && factorEmpty) {
      factorEmpty.textContent = IS_CRYPTO ? "暂无可用的币圈因子。" : "暂无可用的因子。";
      factorEmpty.style.display = "block";
    }
  } catch (err) {
    if (err.message.includes("401") || err.message.includes("登录已失效")) {
      window.location.href = "/login";
      return;
    }
    if (err.message.includes("403") || err.message.includes("影子模式未开放")) {
      if (factorEmpty) {
        factorEmpty.textContent = "影子模式未开放，当前账号无权访问币圈因子。";
        factorEmpty.style.display = "block";
      }
      showError("影子模式未开放");
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
