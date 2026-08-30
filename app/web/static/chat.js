"use strict";

const messagesEl = document.getElementById("messages");
const subscriptionFeedEl = document.getElementById("subscriptionFeed");
const subscriptionEmptyEl = document.getElementById("subscriptionEmpty");
const subscriptionCountEl = document.getElementById("subscriptionCount");
const subscriptionRefreshHintEl = document.getElementById("subscriptionRefreshHint");
const refreshSubscriptionsBtn = document.getElementById("refreshSubscriptions");
const loadedFactorBar = document.getElementById("loadedFactorBar");
const loadedFactorChips = document.getElementById("loadedFactorChips");
const clearLoadedFactorsBtn = document.getElementById("clearLoadedFactors");
const loginCard = document.getElementById("loginCard");
const chatCard = document.getElementById("chatCard");
const needKeyBanner = document.getElementById("needKeyBanner");
const accountEl = document.getElementById("account");
const loginForm = document.getElementById("loginForm");
const chatForm = document.getElementById("chatForm");
const chatInput = document.getElementById("chatInput");
const sendBtn = document.getElementById("sendBtn");

let history = [];
let streaming = false;
let subscriptionStream = null;

async function loadChatHistory(actor) {
  history = [];
  messagesEl.textContent = "";
  try {
    const data = await api("/api/chat/history");
    const messages = (data && data.messages) || [];
    for (const item of messages) {
      if (!item || (item.role !== "user" && item.role !== "assistant")) continue;
      const content = typeof item.content === "string" ? item.content : "";
      if (!content) continue;
      history.push({ role: item.role, content });
      addMessage(item.role, content);
    }
  } catch (_e) {
    history = [];
  }
}

function showLogin() {
  loginCard.classList.remove("hidden");
  chatCard.classList.add("hidden");
  accountEl.textContent = "";
}

function showChat(actor) {
  loginCard.classList.add("hidden");
  chatCard.classList.remove("hidden");
  if (needKeyBanner) needKeyBanner.classList.toggle("hidden", !actor.needsKey);
  if (actor.user) {
    accountEl.textContent = `手机号 ${actor.user.phoneMasked}`;
  } else if (actor.token) {
    accountEl.textContent = `令牌：${actor.token.name} · 已用 ${actor.token.quotaUsed} / ${actor.token.quotaTotal === -1 ? "不限" : actor.token.quotaTotal}`;
  }
  loadChatHistory(actor);
  renderLoadedFactors();
  loadSubscriptions();
  connectSubscriptionStream();
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

function addMessage(role, text) {
  const div = document.createElement("div");
  div.className = `msg ${role}`;
  div.textContent = text;
  messagesEl.appendChild(div);
  messagesEl.scrollTop = messagesEl.scrollHeight;
  return div;
}

function addToolRow() {
  const row = document.createElement("div");
  row.className = "tool-row";
  messagesEl.appendChild(row);
  return row;
}

function addChip(row, text, cls = "") {
  const span = document.createElement("span");
  span.className = `chip ${cls}`;
  span.textContent = text;
  row.appendChild(span);
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

const LOADED_FACTORS_KEY = "sanyi.loadedFactors";

function getLoadedFactors() {
  try {
    const value = JSON.parse(localStorage.getItem(LOADED_FACTORS_KEY) || "[]");
    if (!Array.isArray(value)) return [];
    return value.filter((item) => item && typeof item.factorKey === "string" && item.factorKey);
  } catch (_e) {
    return [];
  }
}

function getLoadedFactorKeys() {
  return getLoadedFactors().map((item) => item.factorKey);
}

function renderLoadedFactors() {
  if (!loadedFactorBar || !loadedFactorChips) return;
  const factors = getLoadedFactors();
  loadedFactorChips.textContent = "";
  loadedFactorBar.classList.toggle("hidden", factors.length === 0);
  for (const factor of factors) {
    const chip = document.createElement("span");
    chip.className = "lf-chip";
    chip.textContent = factor.name || factor.factorKey;
    chip.title = factor.factorKey;
    loadedFactorChips.appendChild(chip);
  }
}

function clearLoadedFactors() {
  localStorage.removeItem(LOADED_FACTORS_KEY);
  renderLoadedFactors();
}

if (clearLoadedFactorsBtn) {
  clearLoadedFactorsBtn.addEventListener("click", clearLoadedFactors);
}

if (refreshSubscriptionsBtn) {
  refreshSubscriptionsBtn.addEventListener("click", async () => {
    if (refreshSubscriptionsBtn.disabled) return;
    refreshSubscriptionsBtn.disabled = true;
    const oldText = refreshSubscriptionsBtn.textContent;
    refreshSubscriptionsBtn.textContent = "刷新中…";
    try {
      await loadSubscriptions();
    } finally {
      refreshSubscriptionsBtn.disabled = false;
      refreshSubscriptionsBtn.textContent = oldText;
    }
  });
}

function firstDefined(...values) {
  for (const value of values) {
    if (value !== null && value !== undefined && value !== "") return value;
  }
  return null;
}

const FACTOR_LABELS = {
  dimen_gate_signal: "地门信号",
  jue_direction: "诀与破诀",
  gate_condition: "门条件",
  wave_jue_combo: "走法×破诀组合",
  crypto_market: "币圈行情",
};

const FREQUENCY_LABELS = {
  "5m": "5分钟",
  "15m": "15分钟",
  "1h": "1小时",
  "1d": "日线",
  "1w": "周线",
  "1M": "月线",
};

const DIRECTION_LABELS = {
  long: "多",
  short: "空",
  none: "无方向",
  up: "上涨",
  down: "下跌",
  flat: "平盘",
};

const GATE_STATUS_LABELS = {
  OPEN: "地门开",
  FORMATION_ABOVE: "地门形成·无动作门上",
};

function factorLabel(factorKey) {
  return FACTOR_LABELS[factorKey] || factorKey;
}

function subscriptionTitleText(sub) {
  const raw = sub.name || factorLabel(sub.factorKey);
  // Agent 生成的名称里可能带（15m/1h）这类技术括号，列表里用下方中文条件展示。
  return String(raw).replace(/（[^）]*）/g, "").trim() || factorLabel(sub.factorKey);
}

function frequencyLabel(value) {
  return FREQUENCY_LABELS[value] || value;
}

function directionLabel(value) {
  return DIRECTION_LABELS[value] || value;
}

function signalStatusLabel(value) {
  return GATE_STATUS_LABELS[value] || value;
}

function subscriptionFiltersText(filters) {
  const parts = [];
  const value = filters || {};

  if (Array.isArray(value.frequencies) && value.frequencies.length) {
    parts.push(value.frequencies.map(frequencyLabel).join("、"));
  }
  if (Array.isArray(value.states) && value.states.length) {
    parts.push(value.states.join("、"));
  }
  if (value.broken === true) parts.push("只看破诀");
  if (value.broken === false) parts.push("只看未破");
  if (Array.isArray(value.walkCodes) && value.walkCodes.length) {
    parts.push(`走${value.walkCodes.join("/")}`);
  }
  if (Array.isArray(value.walkMarks) && value.walkMarks.length) {
    parts.push(value.walkMarks.join("、"));
  }
  if (Array.isArray(value.directions) && value.directions.length) {
    parts.push(value.directions.map(directionLabel).join("/"));
  }
  if (Array.isArray(value.symbols) && value.symbols.length) {
    parts.push("品种：" + value.symbols.join("、"));
  }
  if (value.maxAgeMinutes) parts.push("最近" + value.maxAgeMinutes + "分钟");
  if (value.limit) parts.push("最多" + value.limit + "条");

  return parts.length ? parts.join(" · ") : "全部信号";
}

function subscriptionMatchTitle(match) {
  const name = firstDefined(match.name);
  const contract = firstDefined(match.contract);
  const symbol = firstDefined(match.symbol);
  if (contract && name && contract.indexOf(name) === -1) {
    return name + " " + contract;
  }
  return contract || name || symbol || "未知合约";
}

function subscriptionMatchMeta(match) {
  const freq = firstDefined(match.frequency);
  const state = firstDefined(match.state, match.status);
  const walk = firstDefined(match.walkCode, match.walkMark, match.walkState);
  const direction = firstDefined(match.direction, match.formation);
  const parts = [];
  if (freq) parts.push(frequencyLabel(freq));
  if (state) parts.push(signalStatusLabel(state));
  if (walk) parts.push(`走${walk}`);
  if (direction) parts.push(directionLabel(direction));
  if (match.changePercent !== null && match.changePercent !== undefined) {
    const prefix = Number(match.changePercent) > 0 ? "+" : "";
    parts.push(prefix + match.changePercent + "%");
  }
  return parts.join(" · ");
}

function subscriptionMatchTimeValue(match) {
  const value = firstDefined(
    match.updatedAt, match.generatedAt, match.generated_at,
    match.openAt, match.open_at,
    match.barTime, match.bar_time
  );
  if (!value) return 0;
  const parsed = Date.parse(String(value));
  return Number.isFinite(parsed) ? parsed : 0;
}

function pad2(value) {
  return String(value).padStart(2, "0");
}

function formatSubscriptionTime(value) {
  const text = String(value || "");
  const parsed = Date.parse(text);
  if (!Number.isFinite(parsed)) return text.replace("T", " ").slice(0, 19);
  const date = new Date(parsed);
  return [
    date.getFullYear() + "-" + pad2(date.getMonth() + 1) + "-" + pad2(date.getDate()),
    pad2(date.getHours()) + ":" + pad2(date.getMinutes()) + ":" + pad2(date.getSeconds()),
  ].join(" ");
}

function subscriptionMatchTimeText(match) {
  const value = firstDefined(
    match.updatedAt, match.generatedAt, match.generated_at,
    match.openAt, match.open_at,
    match.barTime, match.bar_time
  );
  if (!value) return "";
  const label = firstDefined(match.updatedAt, match.generatedAt) ? "更新时间" : "信号时间";
  return label + "：" + formatSubscriptionTime(value) + "（本地时间）";
}

function addSubscriptionMatch(parent, match) {
  const card = document.createElement("div");
  card.className = "subscription-signal";

  const top = document.createElement("div");
  top.className = "sub-s-top";
  const title = document.createElement("div");
  title.className = "sub-s-title";
  title.textContent = subscriptionMatchTitle(match);
  top.appendChild(title);

  const meta = document.createElement("div");
  meta.className = "sub-s-meta";
  meta.textContent = subscriptionMatchMeta(match);
  top.appendChild(meta);

  const timeText = subscriptionMatchTimeText(match);
  const timeEl = document.createElement("div");
  timeEl.className = "sub-s-time";
  timeEl.textContent = timeText;

  card.appendChild(top);
  if (timeEl.textContent) card.appendChild(timeEl);
  parent.appendChild(card);
}

function renderSubscriptionSnapshot(snapshot) {
  if (!subscriptionFeedEl) return;
  const subscriptions = snapshot || [];
  subscriptionFeedEl.querySelectorAll(".subscription-card").forEach((el) => el.remove());
  subscriptionFeedEl.querySelectorAll(".subscription-signal").forEach((el) => el.remove());

  if (subscriptionCountEl) subscriptionCountEl.textContent = String(subscriptions.length);
  if (!subscriptions.length) {
    if (subscriptionEmptyEl) subscriptionEmptyEl.style.display = "block";
    return;
  }
  if (subscriptionEmptyEl) subscriptionEmptyEl.style.display = "none";

  for (const sub of subscriptions) {
    const card = document.createElement("div");
    card.className = "subscription-card";

    const head = document.createElement("div");
    head.className = "sub-head";
    const title = document.createElement("div");
    title.className = "sub-title";
    title.textContent = subscriptionTitleText(sub);
    const meta = document.createElement("div");
    meta.className = "sub-meta";
    meta.textContent = factorLabel(sub.factorKey) + " · " + subscriptionFiltersText(sub.filters);
    const remove = document.createElement("button");
    remove.type = "button";
    remove.textContent = "删除";
    remove.addEventListener("click", async () => {
      try {
        await api(`/api/v1/signal-subscriptions/${sub.id}`, { method: "DELETE" });
        loadSubscriptions();
      } catch (err) {
        addMessage("error", err.message);
      }
    });
    head.appendChild(title);
    head.appendChild(remove);

    card.appendChild(head);
    card.appendChild(meta);
    if (sub.error) {
      const errEl = document.createElement("div");
      errEl.className = "sub-error";
      errEl.textContent = `订阅更新失败：${sub.error}`;
      card.appendChild(errEl);
    }
    subscriptionFeedEl.appendChild(card);

    const sortedMatches = (sub.matches || []).slice().sort((a, b) => {
      return subscriptionMatchTimeValue(b) - subscriptionMatchTimeValue(a);
    });
    for (const match of sortedMatches.slice(0, 20)) {
      addSubscriptionMatch(card, match);
    }
    if ((sub.matches || []).length > 20) {
      const more = document.createElement("div");
      more.className = "sub-more";
      more.textContent = `还有 ${sub.matches.length - 20} 条匹配`;
      card.appendChild(more);
    }
  }
}

function setSubscriptionRefreshHint(seconds) {
  if (!subscriptionRefreshHintEl) return;
  const value = Number(seconds);
  subscriptionRefreshHintEl.textContent = `目前是 ${value > 0 ? value : 30} 秒一刷新`;
}

async function loadSubscriptions() {
  try {
    const data = await api("/api/v1/signal-subscriptions/matches");
    renderSubscriptionSnapshot((data && data.subscriptions) || []);
    if (data && data.refreshSeconds) setSubscriptionRefreshHint(data.refreshSeconds);
  } catch (err) {
    if (subscriptionEmptyEl) {
      subscriptionEmptyEl.textContent = `订阅加载失败：${err.message}`;
      subscriptionEmptyEl.style.display = "block";
    }
  }
}

function connectSubscriptionStream() {
  if (subscriptionStream) return;
  subscriptionStream = new EventSource("/api/v1/signal-subscriptions/stream");
  subscriptionStream.addEventListener("hello", (msg) => {
    try {
      const data = JSON.parse(msg.data);
      if (data.refreshSeconds) setSubscriptionRefreshHint(data.refreshSeconds);
    } catch (_e) {}
  });
  subscriptionStream.addEventListener("snapshot", (msg) => {
    try {
      const data = JSON.parse(msg.data);
      renderSubscriptionSnapshot(data.subscriptions || []);
    } catch (_e) {}
  });
  subscriptionStream.onerror = () => {
    // EventSource 自动重连；同时保留现有列表。
  };
}

function parseSse(part) {
  let event = "message";
  const dataLines = [];
  for (const line of part.split("\n")) {
    if (line.startsWith("event:")) event = line.slice(6).trim();
    if (line.startsWith("data:")) dataLines.push(line.slice(5).trimStart());
  }
  if (!dataLines.length) return null;
  try {
    return { event, data: JSON.parse(dataLines.join("\n")) };
  } catch (_e) {
    return { event, data: { text: dataLines.join("\n") } };
  }
}

async function boot() {
  try {
    const resp = await fetch("/api/chat/session", { credentials: "same-origin" });
    if (resp.status === 401) {
      window.location.href = "/login";
      return;
    }
    if (resp.status === 403) {
      window.location.href = "/keys";
      return;
    }
    const body = await resp.json();
    if (resp.ok && body.code === 0) {
      showChat(body.data || {});
    } else {
      showLogin();
    }
  } catch (_e) {
    showLogin();
  }
}

loginForm.addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const token = document.getElementById("tokenInput").value.trim();
  if (!token) return;
  try {
    const data = await api("/api/chat/login", {
      method: "POST",
      body: JSON.stringify({ token }),
    });
    showChat(data.token);
  } catch (err) {
    addMessage("error", err.message);
  }
});

chatForm.addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const text = chatInput.value.trim();
  if (!text || streaming) return;
  streaming = true;
  sendBtn.disabled = true;
  chatInput.value = "";
  history.push({ role: "user", content: text });
  addMessage("user", text);

  const assistantEl = addMessage("assistant", "");
  const toolRow = addToolRow();

  try {
    const resp = await fetch("/api/chat", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ messages: [{ role: "user", content: text }], factorKeys: getLoadedFactorKeys(), persistHistory: true }),
    });
    if (!resp.ok) {
      const body = await resp.json().catch(() => ({}));
      throw new Error(body.detail?.message || body.message || `请求失败（${resp.status}）`);
    }

    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let assistantText = "";

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const parts = buffer.split("\n\n");
      buffer = parts.pop() || "";
      for (const part of parts) {
        const event = parseSse(part);
        if (!event) continue;
        if (event.event === "delta") {
          assistantText += event.data.text || "";
          assistantEl.textContent = assistantText;
          messagesEl.scrollTop = messagesEl.scrollHeight;
        } else if (event.event === "tool_call") {
          addChip(toolRow, `工具：${event.data.name}`, "");
        } else if (event.event === "tool_result") {
          const err = event.data.result && event.data.result.error;
          addChip(toolRow, err ? "工具调用失败" : "三易引擎已返回", err ? "err" : "ok");
        } else if (event.event === "filter_update") {
          addChip(toolRow, `筛选条件已更新`, "ok");
        } else if (event.event === "subscription_update") {
          addChip(toolRow, `订阅已更新`, "ok");
          loadSubscriptions();
        } else if (event.event === "error") {
          assistantEl.textContent += `\n[错误] ${event.data.message}`;
        }
      }
    }

    if (assistantText) {
      history.push({ role: "assistant", content: assistantText });
    } else {
      history.push({ role: "assistant", content: "（模型未返回文本）" });
    }
  } catch (err) {
    addMessage("error", err.message);
  } finally {
    streaming = false;
    sendBtn.disabled = false;
    chatInput.focus();
  }
});

boot();
