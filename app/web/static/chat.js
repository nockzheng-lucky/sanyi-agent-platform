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
let chatAbortController = null;
let shadowMode = false;
let collapsedSubscriptionGroups = new Set();

function isNearBottom() {
  if (!messagesEl) return true;
  return messagesEl.scrollHeight - messagesEl.scrollTop - messagesEl.clientHeight < 48;
}

function scrollToBottom(force = false) {
  if (!messagesEl) return;
  if (force || isNearBottom()) {
    messagesEl.scrollTop = messagesEl.scrollHeight;
  }
}

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
  loadSubscriptionMeta();
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

function addMessage(role, text, options = {}) {
  const div = document.createElement("div");
  div.className = `msg ${role}`;
  div.textContent = text;
  messagesEl.appendChild(div);
  const scrollMode = options.scroll || "auto";
  if (scrollMode === "force") {
    scrollToBottom(true);
  } else if (scrollMode === "auto") {
    scrollToBottom(false);
  }
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
  scrollToBottom(false);
}

const LOADED_FACTORS_KEY = "sanyi.loadedFactors";

const LEGACY_FACTOR_KEYS = [
  "dimen_gate_signal", "futures_gate_signal", "jue_direction", "gate_condition",
  "wave_jue_combo", "crypto_market", "crypto_gate_condition", "crypto_gate_signal",
  "crypto_wave_jue_combo", "futures_gate_walk2_follow", "crypto_gate_walk2_follow",
];
function getLoadedFactors() {
  try {
    const value = JSON.parse(localStorage.getItem(LOADED_FACTORS_KEY) || "[]");
    if (!Array.isArray(value)) return [];
    return value.filter((item) => item && typeof item.factorKey === "string" && item.factorKey && !LEGACY_FACTOR_KEYS.includes(item.factorKey));
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
  futures_price: "期货 · 价格 K 线",
  futures_ma: "期货 · 均线 MA52/208/832（可对比25/144/169）",
  futures_ma_triple: "期货 · 均线 MA25/144/169（可对比52/208/832）",
  futures_macd: "期货 · MACD",
  futures_rsi: "期货 · RSI3 进攻",
  futures_segment: "期货 · 走势段",
  futures_jue: "期货 · 诀",
  futures_door: "期货 · 门",
  futures_spatial: "期货 · 门价与均线",
  futures_tf: "期货 · 跨级别",
  crypto_price: "币圈 · 价格 K 线",
  crypto_ma: "币圈 · 均线 MA52/208/832（可对比25/144/169）",
  crypto_ma_triple: "币圈 · 均线 MA25/144/169（可对比52/208/832）",
  crypto_macd: "币圈 · MACD",
  crypto_rsi: "币圈 · RSI3 进攻",
  crypto_segment: "币圈 · 走势段",
  crypto_jue: "币圈 · 诀",
  crypto_door: "币圈 · 门",
  crypto_spatial: "币圈 · 门价与均线",
  crypto_tf: "币圈 · 跨级别",
};

const SUBSCRIPTION_GROUPS = [
  { key: "futures", label: "期货侧", empty: "暂无期货订阅。" },
  { key: "crypto", label: "币圈侧", empty: "暂无币圈订阅。" },
];

function subscriptionDomain(factorKey) {
  return String(factorKey || "").startsWith("crypto_") ? "crypto" : "futures";
}

const FREQUENCY_LABELS = {
  "1m": "1分钟",
  "5m": "5分钟",
  "15m": "15分钟",
  "30m": "30分钟",
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
  return String(raw).replace(/（[^）]*）/g, "").replace(/&gt;/g, ">").replace(/&lt;/g, "<").trim() || factorLabel(sub.factorKey);
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

function signatureText(signature, frequency) {
  const text = String(signature || "");
  if (text.length !== 6) return "";
  const pairs = [["价", "25"], ["价", "144"], ["价", "169"], ["25", "144"], ["25", "169"], ["144", "169"]];
  const arrows = { "+": "↑", "-": "↓", "0": "≈" };
  const level = frequency ? frequencyLabel(frequency) + "六线：" : "六线：";
  return level + pairs.map((pair, index) => {
    const left = pair[0];
    const right = pair[1];
    const arrow = arrows[text[index]] || "?";
    return `${left}${arrow}${right}`;
  }).join(" · ");
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
  if (typeof value.ma52AboveMa208 === "boolean") {
    parts.push(value.ma52AboveMa208 ? "MA52>MA208" : "MA52<MA208");
  }
  if (typeof value.ma52AboveMa832 === "boolean") {
    parts.push(value.ma52AboveMa832 ? "MA52>MA832" : "MA52<MA832");
  }
  if (typeof value.ma208AboveMa832 === "boolean") {
    parts.push(value.ma208AboveMa832 ? "MA208>MA832" : "MA208<MA832");
  }
  if (value.allowMissingMaRelation === true && (
    typeof value.ma208AboveMa832 === "boolean" ||
    typeof value.ma52AboveMa208 === "boolean" ||
    typeof value.ma52AboveMa832 === "boolean"
  )) {
    parts.push("关系缺失放行·未确认");
  }
  if (Array.isArray(value.relations) && value.relations.length) {
    const RELATION_LABELS = {
      price_ma25: "收盘价-MA25",
      price_ma144: "收盘价-MA144",
      price_ma169: "收盘价-MA169",
      ma25_ma144: "MA25-MA144",
      ma25_ma169: "MA25-MA169",
      ma144_ma169: "MA144-MA169",
      ma25_ma52: "MA25-MA52",
      ma25_ma208: "MA25-MA208",
      ma25_ma832: "MA25-MA832",
      ma144_ma52: "MA144-MA52",
      ma144_ma208: "MA144-MA208",
      ma144_ma832: "MA144-MA832",
      ma169_ma52: "MA169-MA52",
      ma169_ma208: "MA169-MA208",
      ma169_ma832: "MA169-MA832",
    };
    parts.push("关系：" + value.relations.map((item) => RELATION_LABELS[item] || item).join("、"));
  }
  if (Array.isArray(value.relationStates) && value.relationStates.length) {
    const RELATION_STATE_LABELS = { above: "上方", near: "贴线", below: "下方" };
    parts.push("状态：" + value.relationStates.map((item) => RELATION_STATE_LABELS[item] || item).join("/"));
  }
  if (Array.isArray(value.relationCrosses) && value.relationCrosses.length) {
    const RELATION_CROSS_LABELS = { cross_up: "上穿/金叉", cross_down: "下穿/死叉", none: "无穿越" };
    parts.push("穿越：" + value.relationCrosses.map((item) => RELATION_CROSS_LABELS[item] || item).join("/"));
  }
  if (value.requireAllRelations === true && Array.isArray(value.relations) && value.relations.length > 1) {
    parts.push("全部关系同时满足");
  }
  if (typeof value.tolerancePct === "number") {
    parts.push(`贴线容差±${value.tolerancePct}%`);
  }
  if (Array.isArray(value.priceZones) && value.priceZones.length) {
    const PRICE_ZONE_LABELS = { above_all: "价格在三线之上", below_all: "价格在三线之下", inside: "价格夹在三线之间" };
    parts.push(value.priceZones.map((item) => PRICE_ZONE_LABELS[item] || item).join("、"));
  }
  if (Array.isArray(value.maOrders) && value.maOrders.length) {
    const MA_ORDER_LABELS = { bull: "MA25>MA144>MA169", bear: "MA25<MA144<MA169", mixed: "三线纠缠" };
    parts.push(value.maOrders.map((item) => MA_ORDER_LABELS[item] || item).join("、"));
  }
  if (Array.isArray(value.alignments) && value.alignments.length) {
    const ALIGNMENT_LABELS = { bull: "多头排列", bear: "空头排列", mixed: "混合排列" };
    parts.push(value.alignments.map((item) => ALIGNMENT_LABELS[item] || item).join("、"));
  }
  if (Array.isArray(value.signatures) && value.signatures.length) {
    parts.push(value.signatures.map((item) => signatureText(item, "")).filter(Boolean).join(" / "));
  }
  if (value.includeDeleted === true) parts.push("含已删除");
  if (Array.isArray(value.symbols) && value.symbols.length) {
    parts.push("品种：" + value.symbols.join("、"));
  }
  if (value.maxAgeMinutes) parts.push("最近" + value.maxAgeMinutes + "分钟");
  if (value.limit) parts.push("最多" + value.limit + "条");

  return parts.length ? parts.join(" · ") : "全部信号";
}

function offsetLabel(offset) {
  if (offset === 0) return "同周期";
  return offset > 0 ? `父级+${offset}` : `子级${offset}`;
}

function conditionText(condition) {
  const layerLabel = condition.layer === "event"
    ? "事件："
    : condition.layer === "pool" || condition.role === "context"
      ? "池子："
      : "";
  const parts = [layerLabel + factorLabel(condition.factorKey), filterPartsText(condition.filters)];
  if (condition.role === "context") {
    const joinParts = [];
    const targets = Array.isArray(condition.targetFrequencies)
      ? condition.targetFrequencies
      : [];
    const offsets = Array.isArray(condition.frequencyOffsets)
      ? condition.frequencyOffsets
      : [Number(condition.frequencyOffset) || 0];
    if (targets.length) {
      joinParts.push(`目标级别：${targets.map(frequencyLabel).join("/")}`);
    }
    if (offsets.length > 1) {
      joinParts.push(`任一级：${offsets.map(offsetLabel).join("/")}`);
    } else if (!targets.length) {
      joinParts.push(offsetLabel(offsets[0]));
    }
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
  if (match.alignment) {
    const ALIGNMENT_LABELS = { bull: "多头排列", bear: "空头排列", mixed: "混合排列" };
    parts.push(ALIGNMENT_LABELS[match.alignment] || match.alignment);
  }
  if (match.priceZone) {
    const PRICE_ZONE_LABELS = { above_all: "价在三线之上", below_all: "价在三线之下", inside: "价夹在三线之间" };
    parts.push(PRICE_ZONE_LABELS[match.priceZone] || match.priceZone);
  }
  if (match.maOrder) {
    const MA_ORDER_LABELS = { bull: "MA25>MA144>MA169", bear: "MA25<MA144<MA169", mixed: "三线纠缠" };
    parts.push(MA_ORDER_LABELS[match.maOrder] || match.maOrder);
  }
  if (match.signature) {
    const sigText = signatureText(match.signature, match.frequency);
    if (sigText) parts.push(sigText);
  }
  if (match.relations && typeof match.relations === "object") {
    const RELATION_LABELS = {
      price_ma25: "C-25", price_ma144: "C-144", price_ma169: "C-169",
      ma25_ma144: "25-144", ma25_ma169: "25-169", ma144_ma169: "144-169",
      ma25_ma52: "25-52", ma25_ma208: "25-208", ma25_ma832: "25-832",
      ma144_ma52: "144-52", ma144_ma208: "144-208", ma144_ma832: "144-832",
      ma169_ma52: "169-52", ma169_ma208: "169-208", ma169_ma832: "169-832",
    };
    const RELATION_STATE_LABELS = { above: "上", near: "贴", below: "下" };
    const relationText = Object.keys(match.relations).map((key) => {
      const rel = match.relations[key] || {};
      const state = RELATION_STATE_LABELS[rel.state] || rel.state || "?";
      const pct = (rel.pct === null || rel.pct === undefined) ? "" : ` ${rel.pct}%`;
      return `${RELATION_LABELS[key] || key}:${state}${pct}`;
    });
    if (relationText.length) parts.push(relationText.join(" · "));
  }
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
    const factorKey = String(context.factorKey || "");
    if (factorKey.endsWith("_ma_triple")) {
      for (const row of context.matches.slice(0, 3)) {
        if (!row || !row.frequency) continue;
        const ORDER_LABELS = { bull: "25>144>169", bear: "25<144<169", mixed: "三线纠缠" };
        parts.push(`池@${frequencyLabel(row.frequency)} ${ORDER_LABELS[row.maOrder] || row.maOrder || "?"}`);
      }
      continue;
    }
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
  if (sub.structureComplete === false) {
    const warnEl = document.createElement("div");
    warnEl.className = "sub-error";
    warnEl.textContent = `结构不完整：${(sub.missingParts || []).join("；")}。请到 Agent 补充。`;
    card.appendChild(warnEl);
  }
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

async function loadSubscriptionMeta() {
  // 先只拉订阅定义（DB，不评估因子），让侧栏立即出现订阅条目。
  try {
    const data = await api("/api/v1/signal-subscriptions");
    renderSubscriptionSnapshot((data && data.subscriptions) || []);
  } catch (_e) {
    // 慢速 matches 接口失败时仍会给出提示；这里不打断页面。
  }
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
    try {
      const clean = new URL(window.location.href);
      if (clean.searchParams.has("token")) {
        clean.searchParams.delete("token");
        window.history.replaceState(null, "", clean.pathname + clean.search + clean.hash);
      }
    } catch (_e) {}
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
  addMessage("user", text, { scroll: "force" });

  const assistantEl = addMessage("assistant", "", { scroll: "force" });
  const toolRow = addToolRow();

  try {
    chatAbortController = new AbortController();
    const resp = await fetch("/api/chat", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ messages: [{ role: "user", content: text }], factorKeys: getLoadedFactorKeys(), persistHistory: true }),
      signal: chatAbortController.signal,
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
          scrollToBottom(false);
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
    if (err && err.name === "AbortError") {
      // 页面离开时主动取消；回来后 boot() 会从服务器历史恢复。
    } else {
      addMessage("error", err.message);
    }
  } finally {
    streaming = false;
    sendBtn.disabled = false;
    chatAbortController = null;
    chatInput.focus();
  }
});

function teardownPageConnections() {
  if (chatAbortController) {
    chatAbortController.abort();
    chatAbortController = null;
  }
  if (subscriptionStream) {
    subscriptionStream.close();
    subscriptionStream = null;
  }
  streaming = false;
  if (sendBtn) sendBtn.disabled = false;
  if (chatInput) chatInput.disabled = false;
}

window.addEventListener("pagehide", () => {
  teardownPageConnections();
});

window.addEventListener("pageshow", (event) => {
  // 浏览器 bfcache 恢复页面时，原 SSE 流已经断了，但页面 DOM 还停留在“输出中”。
  // 强制重新拉会话，恢复登录态并刷新聊天历史。
  if (event.persisted) {
    teardownPageConnections();
    boot();
  }
});

boot();
