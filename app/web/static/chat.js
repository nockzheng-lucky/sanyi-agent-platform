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
let shadowMode = false;
let collapsedSubscriptionGroups = new Set();

async function refreshShadowMode() {
  try {
    const resp = await fetch("/api/v1/auth/me", { credentials: "same-origin" });
    if (!resp.ok) return;
    const body = await resp.json();
    shadowMode = !!(body && body.code === 0 && body.data && body.data.shadowMode);
  } catch (_e) {
    shadowMode = false;
  }
}

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

async function showChat(actor) {
  loginCard.classList.add("hidden");
  chatCard.classList.remove("hidden");
  if (needKeyBanner) needKeyBanner.classList.toggle("hidden", !actor.needsKey);
  if (actor.user) {
    accountEl.textContent = `手机号 ${actor.user.phoneMasked}`;
  } else if (actor.token) {
    accountEl.textContent = `令牌：${actor.token.name} · 已用 ${actor.token.quotaUsed} / ${actor.token.quotaTotal === -1 ? "不限" : actor.token.quotaTotal}`;
  }
  shadowMode = false;
  await refreshShadowMode();
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
  futures_gate_signal: "期货今日门信号",
  jue_direction: "诀与破诀",
  gate_condition: "门条件",
  wave_jue_combo: "走法×破诀组合",
  crypto_market: "币圈行情",
  crypto_gate_condition: "币圈门条件",
  crypto_gate_signal: "币圈今日门信号",
  crypto_wave_jue_combo: "币圈走法×破诀组合",
};

const SUBSCRIPTION_GROUPS = [
  { key: "futures", label: "期货侧", empty: "暂无期货订阅。" },
  { key: "crypto", label: "币圈侧", empty: "暂无币圈订阅。" },
];

function subscriptionDomain(factorKey) {
  return String(factorKey || "").startsWith("crypto_") ? "crypto" : "futures";
}

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

function signalStatusLabel(value, gateType) {
  if (gateType === "tian") {
    if (value === "OPEN") return "天门开";
    if (value === "CLOSED") return "天门关";
    if (value === "FORMATION_ABOVE") return "天门形成·无动作门上";
    if (value === "FORMATION_BELOW") return "天门形成·无动作门下";
  }
  if (gateType === "di") {
    if (value === "OPEN") return "地门开";
    if (value === "CLOSED") return "地门关";
    if (value === "FORMATION_ABOVE") return "地门形成·无动作门上";
    if (value === "FORMATION_BELOW") return "地门形成·无动作门下";
  }
  return GATE_STATUS_LABELS[value] || value;
}

const GATE_TYPE_LABELS = {
  tian: "天门",
  di: "地门",
};

function gateTypeLabel(value) {
  return GATE_TYPE_LABELS[value] || value;
}

function filterPartsText(filters) {
  const parts = [];
  const value = filters || {};

  if (Array.isArray(value.frequencies) && value.frequencies.length) {
    parts.push(value.frequencies.map(frequencyLabel).join("、"));
  }
  if (Array.isArray(value.combos) && value.combos.length) {
    const COMBO_LABELS = {
      walk2_break20: "走2破20诀",
      walkB_break80: "走B破80诀",
      walkC_break20: "走C破20诀",
    };
    parts.push(value.combos.map((item) => COMBO_LABELS[item] || item).join("、"));
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
  if (Array.isArray(value.gateTypes) && value.gateTypes.length) {
    parts.push(value.gateTypes.map(gateTypeLabel).join("、"));
  }
  if (Array.isArray(value.eventTypes) && value.eventTypes.length) {
    const EVENT_TYPE_LABELS = {
      open: "开门",
      close: "关门",
      formation: "形成门",
      formationAbove: "形成·无动作门上",
      formationBelow: "形成·无动作门下",
    };
    parts.push(value.eventTypes.map((item) => EVENT_TYPE_LABELS[item] || item).join("、"));
  }
  if (Array.isArray(value.actions) && value.actions.length) {
    const ACTION_LABELS = { open: "开门", close: "关门" };
    parts.push(value.actions.map((item) => ACTION_LABELS[item] || item).join("、"));
  }
  if (Array.isArray(value.liveStatuses) && value.liveStatuses.length) {
    parts.push(value.liveStatuses.join("、"));
  }
  if (value.ma208Mode) {
    const MA208_MODE_LABELS = {
      near: "在MA208附近",
      above: "在MA208以上",
      nearOrAbove: "在MA208附近或以上",
    };
    const anchor = value.ma208Anchor === "currentPrice" ? "现价" : "门价";
    const tolerance = Number(value.ma208TolerancePct || 1);
    const toleranceText = value.ma208Mode === "above" ? "" : `（±${tolerance}%）`;
    parts.push(`${anchor}${MA208_MODE_LABELS[value.ma208Mode] || value.ma208Mode}${toleranceText}`);
  }
  if (value.includeDeleted === true) parts.push("含已删除");
  if (Array.isArray(value.symbols) && value.symbols.length) {
    parts.push("品种：" + value.symbols.join("、"));
  }
  if (value.maxAgeMinutes) parts.push("最近" + value.maxAgeMinutes + "分钟");
  if (value.limit) parts.push("最多" + value.limit + "条");

  return parts.length ? parts.join(" · ") : "全部信号";
}

function conditionText(condition) {
  const parts = [factorLabel(condition.factorKey), filterPartsText(condition.filters)];
  if (condition.role === "context") {
    const joinParts = [];
    if (Number(condition.frequencyOffset) === 1) joinParts.push("父级周期");
    if (condition.sideRule === "below") joinParts.push("门价在下方");
    if (condition.sideRule === "above") joinParts.push("门价在上方");
    if (joinParts.length) parts.push(joinParts.join(" · "));
  }
  return parts.join("：");
}

function subscriptionFiltersText(sub) {
  const conditions = Array.isArray(sub.conditions) ? sub.conditions : [];
  if (conditions.length > 1) {
    return conditions.map(conditionText).join(" + ");
  }
  const value = (conditions[0] && conditions[0].filters) || sub.filters || {};
  return filterPartsText(value);
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
  const direction = firstDefined(match.direction);
  const gateType = match.gateType ? gateTypeLabel(match.gateType) : null;
  const liveStatus = firstDefined(match.liveStatus);
  const parts = [];
  if (freq) parts.push(frequencyLabel(freq));
  if (state) {
    parts.push(signalStatusLabel(state, match.gateType));
  } else if (gateType) {
    parts.push(gateType);
  }
  if (liveStatus) parts.push(liveStatus);
  const canonicalGateState = /^(OPEN|CLOSED|FORMATION_ABOVE|FORMATION_BELOW)$/.test(state || "");
  if (match.formation && !canonicalGateState) parts.push(match.formation);
  if (match.gatePrice !== null && match.gatePrice !== undefined) {
    parts.push("门价" + match.gatePrice);
  }
  if (match.ma208 !== null && match.ma208 !== undefined) {
    const distance = firstDefined(match.gateMa208DistancePct);
    if (distance !== null && distance !== undefined) {
      const value = Number(distance);
      if (Math.abs(value) < 0.005) {
        parts.push("门价贴MA208");
      } else if (value > 0) {
        parts.push(`门价高于MA208 ${value}%`);
      } else {
        parts.push(`门价低于MA208 ${Math.abs(value)}%`);
      }
    } else {
      parts.push("MA208 " + match.ma208);
    }
  }
  if (walk) parts.push(`走${walk}`);
  if (direction) parts.push(directionLabel(direction));
  if (match.changePercent !== null && match.changePercent !== undefined) {
    const prefix = Number(match.changePercent) > 0 ? "+" : "";
    parts.push(prefix + match.changePercent + "%");
  }
  if (match.suggestedLeverage !== null && match.suggestedLeverage !== undefined) {
    parts.push(`建议杠杆${match.suggestedLeverage}x`);
  }
  if (match.volatilityPct !== null && match.volatilityPct !== undefined) {
    parts.push(`波动率${match.volatilityPct}%`);
  }
  for (const context of match.contexts || []) {
    if (!context || !Array.isArray(context.matches)) continue;
    for (const gate of context.matches) {
      if (!gate) continue;
      const gateType = gateTypeLabel(gate.gateType);
      const liveStatus = firstDefined(gate.liveStatus, gate.status);
      const gateText = [gateType, liveStatus].filter(Boolean).join("");
      if (gateText) parts.push(gateText);
      if (gate.gatePrice !== null && gate.gatePrice !== undefined) {
        parts.push("门价" + gate.gatePrice);
      }
    }
  }
  return parts.join(" · ");
}

function subscriptionMatchTimeValue(match) {
  const value = firstDefined(
    match.openAt, match.open_at,
    match.closeAt, match.close_at,
    match.eventAt, match.event_at,
    match.barTime, match.bar_time,
    match.generatedAt, match.generated_at,
    match.updatedAt,
    match.crossTime, match.t2Time, match.t1Time
  );
  if (!value) return 0;
  const parsed = Date.parse(String(value));
  return Number.isFinite(parsed) ? parsed : 0;
}

function formatSubscriptionTime(value) {
  const text = String(value || "");
  const parsed = Date.parse(text);
  if (!Number.isFinite(parsed)) return text.replace("T", " ").slice(0, 19);
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hourCycle: "h23",
  }).formatToParts(new Date(parsed));
  const getPart = (type) => {
    const part = parts.find((item) => item.type === type);
    return part ? part.value : "00";
  };
  return [
    getPart("year") + "-" + getPart("month") + "-" + getPart("day"),
    getPart("hour") + ":" + getPart("minute") + ":" + getPart("second"),
  ].join(" ");
}

function subscriptionMatchTimeText(match) {
  const value = firstDefined(
    match.openAt, match.open_at,
    match.closeAt, match.close_at,
    match.eventAt, match.event_at,
    match.barTime, match.bar_time,
    match.generatedAt, match.generated_at,
    match.updatedAt
  );
  if (!value) return "";
  const hasOpenTime = firstDefined(match.openAt, match.open_at);
  const hasCloseTime = firstDefined(match.closeAt, match.close_at);
  const hasUpdateTime = firstDefined(match.updatedAt, match.generatedAt, match.generated_at);
  const state = firstDefined(match.state, match.status);
  const label = hasOpenTime
    ? "开门时间"
    : hasCloseTime
      ? "关门时间"
      : state && String(state).includes("FORMATION")
        ? "形成时间"
        : hasUpdateTime
          ? "更新时间"
          : "信号时间";
  return label + "：" + formatSubscriptionTime(value) + "（北京时间）";
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

function enableHorizontalWheel(element) {
  if (!element || element.dataset.hwheel === "1") return;
  element.dataset.hwheel = "1";
  element.addEventListener(
    "wheel",
    (event) => {
      if (Math.abs(event.deltaY) > Math.abs(event.deltaX)) {
        event.preventDefault();
        element.scrollLeft += event.deltaY;
      }
    },
    { passive: false }
  );
}

function buildSubscriptionCard(sub) {
  const card = document.createElement("div");
  card.className = "subscription-card";

  const head = document.createElement("div");
  head.className = "sub-head";
  const title = document.createElement("div");
  title.className = "sub-title";
  title.textContent = subscriptionTitleText(sub);
  const meta = document.createElement("div");
  meta.className = "sub-meta";
  meta.textContent = factorLabel(sub.factorKey) + " · " + subscriptionFiltersText(sub);
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
  if (Array.isArray(sub.conditionErrors) && sub.conditionErrors.length) {
    for (const conditionError of sub.conditionErrors) {
      const errEl = document.createElement("div");
      errEl.className = "sub-error";
      errEl.textContent = `条件 ${factorLabel(conditionError.factorKey)} 更新失败：${conditionError.error}`;
      card.appendChild(errEl);
    }
  }
  const strip = document.createElement("div");
  strip.className = "sub-signal-strip";
  card.appendChild(strip);

  const sortedMatches = (sub.matches || []).slice().sort((a, b) => {
    return subscriptionMatchTimeValue(b) - subscriptionMatchTimeValue(a);
  });
  for (const match of sortedMatches) {
    addSubscriptionMatch(strip, match);
  }
  return card;
}

function buildSubscriptionGroup(group, subscriptions) {
  const section = document.createElement("section");
  section.className = "subscription-group";
  if (collapsedSubscriptionGroups.has(group.key)) {
    section.classList.add("collapsed");
  }

  const head = document.createElement("button");
  head.type = "button";
  head.className = "subscription-group-head";
  head.addEventListener("click", () => {
    const collapsing = !section.classList.contains("collapsed");
    section.classList.toggle("collapsed", collapsing);
    if (collapsing) {
      collapsedSubscriptionGroups.add(group.key);
    } else {
      collapsedSubscriptionGroups.delete(group.key);
    }
  });

  const label = document.createElement("span");
  label.className = "subscription-group-label";
  label.textContent = group.label;

  const signalCount = subscriptions.reduce((sum, sub) => sum + (sub.matches || []).length, 0);
  const count = document.createElement("span");
  count.className = "subscription-group-count";
  count.textContent = `${subscriptions.length} 订阅 · ${signalCount} 信号`;

  const chevron = document.createElement("span");
  chevron.className = "subscription-group-chevron";
  chevron.textContent = "\u25be";
  chevron.setAttribute("aria-hidden", "true");

  head.appendChild(label);
  head.appendChild(count);
  head.appendChild(chevron);

  const body = document.createElement("div");
  body.className = "subscription-group-body";
  if (!subscriptions.length) {
    const empty = document.createElement("p");
    empty.className = "group-empty";
    empty.textContent = group.empty;
    body.appendChild(empty);
  } else {
    for (const sub of subscriptions) {
      body.appendChild(buildSubscriptionCard(sub));
    }
  }

  section.appendChild(head);
  section.appendChild(body);
  return section;
}

function renderSubscriptionSnapshot(snapshot) {
  if (!subscriptionFeedEl) return;
  const subscriptions = Array.isArray(snapshot) ? snapshot : [];
  subscriptionFeedEl.textContent = "";

  if (subscriptionCountEl) subscriptionCountEl.textContent = String(subscriptions.length);
  const visibleGroups = SUBSCRIPTION_GROUPS.filter(
    (group) => group.key !== "crypto" || shadowMode
  );
  for (const group of visibleGroups) {
    const groupSubs = subscriptions.filter(
      (sub) => subscriptionDomain(sub.factorKey) === group.key
    );
    subscriptionFeedEl.appendChild(buildSubscriptionGroup(group, groupSubs));
  }
  subscriptionFeedEl.querySelectorAll(".subscription-group-body").forEach(enableHorizontalWheel);
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
    if (subscriptionFeedEl && !subscriptionFeedEl.querySelector(".subscription-group")) {
      subscriptionFeedEl.textContent = "";
      const error = document.createElement("p");
      error.className = "group-empty";
      error.textContent = `订阅加载失败：${err.message}`;
      subscriptionFeedEl.appendChild(error);
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
