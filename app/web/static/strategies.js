"use strict";

const strategyListEl = document.getElementById("strategyList");
const strategiesCountEl = document.getElementById("strategiesCount");
const strategiesRefreshHintEl = document.getElementById("strategiesRefreshHint");
const refreshStrategiesBtn = document.getElementById("refreshStrategies");
const accountEl = document.getElementById("account");

let strategiesStream = null;

async function api(url, options = {}) {
  const resp = await fetch(url, {
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  const body = await resp.json().catch(() => ({}));
  if (!resp.ok) {
    throw new Error((body.detail && body.detail.message) || body.message || `请求失败（${resp.status}）`);
  }
  return body.data;
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

const FREQUENCY_LABELS = {
  "1m": "1分钟", "5m": "5分钟", "15m": "15分钟", "30m": "30分钟", "1h": "1小时",
  "1d": "日线", "1w": "周线", "1M": "月线",
};

const GATE_STATUS_LABELS = {
  OPEN: "地门开",
  CLOSED: "地门关",
  FORMATION_ABOVE: "地门形成·无动作门上",
  FORMATION_BELOW: "地门形成·无动作门下",
};

function factorLabel(factorKey) {
  return FACTOR_LABELS[factorKey] || factorKey;
}

function subscriptionTitleText(sub) {
  const raw = sub.name || factorLabel(sub.factorKey);
  return String(raw).replace(/（[^）]*）/g, "").replace(/&gt;/g, ">").replace(/&lt;/g, "<").trim() || factorLabel(sub.factorKey);
}

function frequencyLabel(value) {
  return FREQUENCY_LABELS[value] || value;
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

function matchTitle(match) {
  const name = firstDefined(match.name);
  const contract = firstDefined(match.contract);
  const symbol = firstDefined(match.symbol);
  if (contract && name && contract.indexOf(name) === -1) {
    return name + " " + contract;
  }
  return contract || name || symbol || "未知合约";
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

function poolContextText(match) {
  const parts = [];
  for (const context of match.contexts || []) {
    if (!context || !Array.isArray(context.matches)) continue;
    const factorKey = String(context.factorKey || "");
    if (factorKey.endsWith("_ma_triple")) {
      for (const row of context.matches) {
        if (!row || !row.frequency) continue;
        const ORDER_LABELS = { bull: "25>144>169", bear: "25<144<169", mixed: "三线纠缠" };
        parts.push(`池@${frequencyLabel(row.frequency)} ${ORDER_LABELS[row.maOrder] || row.maOrder || "?"}`);
        break;
      }
    } else if (factorKey.endsWith("_door") || factorKey.endsWith("_tf")) {
      for (const row of context.matches) {
        if (!row || !row.frequency) continue;
        parts.push(`池@${frequencyLabel(row.frequency)} ${row.gateType === "tian" ? "天门" : "地门"}`);
        break;
      }
    }
    if (parts.length >= 3) break;
  }
  return parts.join(" · ");
}

function matchMeta(match) {
  const freq = firstDefined(match.frequency);
  const state = firstDefined(match.state, match.status);
  const walk = firstDefined(match.walkCode, match.walkMark, match.walkState);
  const direction = firstDefined(match.direction);
  const liveStatus = firstDefined(match.liveStatus);
  const parts = [];
  if (freq) parts.push(frequencyLabel(freq));
  if (state) {
    parts.push(signalStatusLabel(state, match.gateType));
  } else if (match.gateType) {
    parts.push(match.gateType === "tian" ? "天门" : "地门");
  }
  if (liveStatus) parts.push(liveStatus);
  if (match.formation && !/^(OPEN|CLOSED|FORMATION_ABOVE|FORMATION_BELOW)$/.test(state || "")) {
    parts.push(match.formation);
  }
  if (match.gatePrice !== null && match.gatePrice !== undefined) {
    parts.push("门价" + match.gatePrice);
  }
  if (walk) parts.push("走" + walk);
  if (direction) {
    parts.push({ long: "多", short: "空", up: "上涨", down: "下跌" }[direction] || direction);
  }
  if (match.alignment) {
    const ALIGNMENT_LABELS = { bull: "多头排列", bear: "空头排列", mixed: "混合排列" };
    parts.push(ALIGNMENT_LABELS[match.alignment] || match.alignment);
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
  const poolText = poolContextText(match);
  if (poolText) parts.push(poolText);
  if (match.changePercent !== null && match.changePercent !== undefined) {
    const prefix = Number(match.changePercent) > 0 ? "+" : "";
    parts.push(prefix + match.changePercent + "%");
  }
  if (match.suggestedLeverage !== null && match.suggestedLeverage !== undefined) {
    parts.push("建议杠杆" + match.suggestedLeverage + "x");
  }
  for (const context of match.contexts || []) {
    if (!context || !Array.isArray(context.matches)) continue;
    for (const gate of context.matches) {
      if (!gate) continue;
      const gateType = gate.gateType === "tian" ? "天门" : gate.gateType === "di" ? "地门" : "";
      const gateState = firstDefined(gate.liveStatus, gate.status);
      if (gateType) parts.push(gateType + (gateState || ""));
      if (gate.gatePrice !== null && gate.gatePrice !== undefined) {
        parts.push("门价" + gate.gatePrice);
      }
    }
  }
  return parts.join(" · ");
}

function matchTimeValue(match) {
  const value = firstDefined(
    match.openAt, match.open_at,
    match.closeAt, match.close_at,
    match.eventAt, match.event_at,
    match.barTime, match.bar_time,
    match.generatedAt, match.generated_at,
    match.updatedAt
  );
  if (!value) return 0;
  const parsed = Date.parse(String(value));
  return Number.isFinite(parsed) ? parsed : 0;
}

function formatTime(value, withSeconds = true) {
  const text = String(value || "");
  const parsed = Date.parse(text);
  if (!Number.isFinite(parsed)) return text.replace("T", " ").slice(0, withSeconds ? 19 : 16);
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: withSeconds ? "2-digit" : undefined,
    hourCycle: "h23",
  }).formatToParts(new Date(parsed));
  const getPart = (type) => {
    const part = parts.find((item) => item.type === type);
    return part ? part.value : "00";
  };
  const date = [getPart("year"), getPart("month"), getPart("day")].join("-");
  const hhmm = [getPart("hour"), getPart("minute")].join(":");
  if (!withSeconds) return date + " " + hhmm;
  return date + " " + hhmm + ":" + getPart("second");
}

function matchTimeText(match) {
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
  const state = firstDefined(match.state, match.status);
  const label = hasOpenTime
    ? "开门时间"
    : hasCloseTime
      ? "关门时间"
      : state && String(state).includes("FORMATION")
        ? "形成时间"
        : "更新时间";
  return label + "：" + formatTime(value, false) + "（北京时间）";
}

function buildMatchCard(match, cls = "") {
  const card = document.createElement("div");
  card.className = "strategy-match " + cls;

  const top = document.createElement("div");
  top.className = "strategy-match-top";
  const title = document.createElement("div");
  title.className = "strategy-match-title";
  title.textContent = matchTitle(match);
  top.appendChild(title);

  const meta = document.createElement("div");
  meta.className = "strategy-match-meta";
  meta.textContent = matchMeta(match);
  top.appendChild(meta);

  const timeText = matchTimeText(match);
  const timeEl = document.createElement("div");
  timeEl.className = "strategy-match-time";
  timeEl.textContent = timeText;

  card.appendChild(top);
  if (timeEl.textContent) card.appendChild(timeEl);
  return card;
}

function buildSentCard(row) {
  const card = buildMatchCard(row.payload || {}, "is-sent");
  const sentTime = document.createElement("div");
  sentTime.className = "strategy-match-sent";
  sentTime.textContent = "推送：" + formatTime(row.sentAt, true) + "（北京时间）";
  card.appendChild(sentTime);
  return card;
}

function buildZone(title, items, options = {}) {
  const zone = document.createElement("section");
  zone.className = "strategy-zone " + (options.cls || "");
  const head = document.createElement("div");
  head.className = "strategy-zone-head";
  const label = document.createElement("span");
  label.className = "strategy-zone-label";
  label.textContent = title;
  const count = document.createElement("span");
  count.className = "strategy-zone-count";
  const totalCount = options.totalCount === undefined ? items.length : options.totalCount;
  count.textContent = String(totalCount);
  head.appendChild(label);
  head.appendChild(count);

  const body = document.createElement("div");
  body.className = "strategy-zone-body";
  if (!items.length) {
    const empty = document.createElement("p");
    empty.className = "strategy-zone-empty";
    empty.textContent = options.empty || "当前没有符合项。";
    body.appendChild(empty);
  } else {
    for (const item of items) {
      body.appendChild(options.builder ? options.builder(item) : buildMatchCard(item));
    }
    if (totalCount > items.length) {
      const more = document.createElement("p");
      more.className = "strategy-zone-more";
      more.textContent = `仅展示前 ${items.length} 条，共 ${totalCount} 条。`;
      body.appendChild(more);
    }
  }
  zone.appendChild(head);
  zone.appendChild(body);
  return zone;
}

function modeLabel(mode) {
  if (mode === "trigger") return "触发型";
  if (mode === "pool") return "池型";
  return "自动";
}

function showPoolZone(sub) {
  if (sub.mode === "pool") return true;
  return Array.isArray(sub.conditions) && sub.conditions.length > 1;
}

function conditionBrief(condition) {
  const parts = [factorLabel(condition.factorKey)];
  const filters = condition.filters || {};
  if (Array.isArray(filters.frequencies) && filters.frequencies.length) {
    parts.push(filters.frequencies.map(frequencyLabel).join("、"));
  }
  if (typeof filters.ma52AboveMa208 === "boolean") {
    parts.push(filters.ma52AboveMa208 ? "52>208" : "52<208");
  }
  if (typeof filters.ma52AboveMa832 === "boolean") {
    parts.push(filters.ma52AboveMa832 ? "52>832" : "52<832");
  }
  if (typeof filters.ma208AboveMa832 === "boolean") {
    parts.push(filters.ma208AboveMa832 ? "208>832" : "208<832");
  }
  if (Array.isArray(filters.relations) && filters.relations.length) {
    const RELATION_LABELS = {
      price_ma25: "价-25", price_ma144: "价-144", price_ma169: "价-169",
      ma25_ma144: "25-144", ma25_ma169: "25-169", ma144_ma169: "144-169",
      ma25_ma52: "25-52", ma25_ma208: "25-208", ma25_ma832: "25-832",
      ma144_ma52: "144-52", ma144_ma208: "144-208", ma144_ma832: "144-832",
      ma169_ma52: "169-52", ma169_ma208: "169-208", ma169_ma832: "169-832",
    };
    parts.push(filters.relations.map((item) => RELATION_LABELS[item] || item).join("、"));
  }
  if (Array.isArray(filters.relationCrosses) && filters.relationCrosses.length) {
    parts.push(filters.relationCrosses.map((item) => item === "cross_up" ? "上穿" : item === "cross_down" ? "下穿" : item).join("/"));
  }
  if (Array.isArray(filters.relationStates) && filters.relationStates.length) {
    const STATE_LABELS = { above: "上方", below: "下方", near: "贴线" };
    parts.push(filters.relationStates.map((item) => STATE_LABELS[item] || item).join("/"));
  }
  if (Array.isArray(filters.maOrders) && filters.maOrders.length) {
    const ORDER_LABELS = { bull: "25>144>169", bear: "25<144<169", mixed: "三线纠缠" };
    parts.push(filters.maOrders.map((item) => ORDER_LABELS[item] || item).join("/"));
  }
  if (Array.isArray(filters.alignments) && filters.alignments.length) {
    const ALIGNMENT_LABELS = { bull: "多头排列", bear: "空头排列", mixed: "混合排列" };
    parts.push(filters.alignments.map((item) => ALIGNMENT_LABELS[item] || item).join("/"));
  }
  const targets = Array.isArray(condition.targetFrequencies) ? condition.targetFrequencies : [];
  if (targets.length) parts.push("目标级别：" + targets.map(frequencyLabel).join("/"));
  const offsets = Array.isArray(condition.frequencyOffsets) ? condition.frequencyOffsets : [];
  if (offsets.length > 1) {
    parts.push("任一级：" + offsets.map((item) => (item > 0 ? `+${item}` : String(item))).join("/"));
  } else if (offsets.length === 1 && offsets[0] !== 0) {
    parts.push(offsets[0] > 0 ? `父级+${offsets[0]}` : `子级${offsets[0]}`);
  }
  return parts.join(" · ");
}

function buildStrategyCard(sub) {
  const card = document.createElement("article");
  card.className = "strategy-card";

  const head = document.createElement("div");
  head.className = "strategy-card-head";
  const titleArea = document.createElement("div");
  titleArea.className = "strategy-card-title-area";
  const title = document.createElement("h3");
  title.textContent = subscriptionTitleText(sub);
  titleArea.appendChild(title);
  const meta = document.createElement("div");
  meta.className = "strategy-card-meta";
  meta.textContent = factorLabel(sub.factorKey) + " · " + modeLabel(sub.mode);
  titleArea.appendChild(meta);
  head.appendChild(titleArea);

  const actions = document.createElement("div");
  actions.className = "strategy-card-actions";
  const remove = document.createElement("button");
  remove.type = "button";
  remove.textContent = "删除";
  remove.addEventListener("click", async () => {
    if (!window.confirm(`确认删除订阅「${subscriptionTitleText(sub)}」？`)) return;
    try {
      await api(`/api/v1/signal-subscriptions/${sub.id}`, { method: "DELETE" });
      loadStrategies();
    } catch (err) {
      window.alert(err.message);
    }
  });
  actions.appendChild(remove);
  head.appendChild(actions);
  card.appendChild(head);

  const eventConditions = Array.isArray(sub.eventConditions) ? sub.eventConditions : [];
  const poolConditions = Array.isArray(sub.poolConditions) ? sub.poolConditions : [];
  if (eventConditions.length || poolConditions.length) {
    const structure = document.createElement("div");
    structure.className = "strategy-card-structure";
    const lines = [];
    if (eventConditions.length) {
      lines.push("事件条件：" + eventConditions.map(conditionBrief).join(" + "));
    }
    if (poolConditions.length) {
      lines.push("候选池：" + poolConditions.map(conditionBrief).join(" + "));
    }
    structure.textContent = lines.join("；");
    card.appendChild(structure);
  }

  if (sub.structureComplete === false) {
    const err = document.createElement("div");
    err.className = "strategy-card-error";
    err.textContent = `结构不完整：${(sub.missingParts || []).join("；")}。请到 Agent 补充。`;
    card.appendChild(err);
  }
  if (sub.error) {
    const err = document.createElement("div");
    err.className = "strategy-card-error";
    err.textContent = `评估失败：${sub.error}`;
    card.appendChild(err);
  }
  if (Array.isArray(sub.conditionErrors) && sub.conditionErrors.length) {
    for (const conditionError of sub.conditionErrors) {
      const err = document.createElement("div");
      err.className = "strategy-card-error";
      err.textContent = `条件 ${factorLabel(conditionError.factorKey)} 更新失败：${conditionError.error}`;
      card.appendChild(err);
    }
  }
  if (sub.poolError) {
    const err = document.createElement("div");
    err.className = "strategy-card-error";
    err.textContent = `基础池更新失败：${sub.poolError}`;
    card.appendChild(err);
  }
  if (Array.isArray(sub.poolConditionErrors) && sub.poolConditionErrors.length) {
    for (const conditionError of sub.poolConditionErrors) {
      const err = document.createElement("div");
      err.className = "strategy-card-error";
      err.textContent = `基础池条件 ${factorLabel(conditionError.factorKey)} 更新失败：${conditionError.error}`;
      card.appendChild(err);
    }
  }

  const pool = Array.isArray(sub.pool) ? sub.pool : [];
  const signals = Array.isArray(sub.signals) ? sub.signals : [];
  const sentToday = Array.isArray(sub.sentToday) ? sub.sentToday : [];

  const loadingText = "正在加载漏斗数据…";
  if (showPoolZone(sub)) {
    card.appendChild(buildZone(
      `基础池 · 当前符合`,
      pool.slice(0, 300),
      { cls: "zone-pool", empty: sub.loading ? loadingText : "当前没有品种满足背景条件。", totalCount: pool.length }
    ));
  }
  if (sub.mode === "trigger") {
    const rawCandidates = Number(sub.triggerCandidates || 0);
    const signalEmpty = sub.loading
      ? loadingText
      : signals.length
        ? "当前没有刚触发的信号。"
        : rawCandidates > 0
          ? `当前没有通过全部条件的信号；原始触发候选 ${rawCandidates} 个，被背景条件过滤。`
          : "当前没有原始触发候选（最近一根 K 线没有上穿/下穿动作）。";
    card.appendChild(buildZone(
      `当前触发 · 发信号`,
      signals.slice(0, 300),
      { cls: "zone-signal", empty: signalEmpty, totalCount: signals.length }
    ));
  }
  card.appendChild(buildZone(
    `今日已推送 · 仅当天`,
    sentToday.slice(0, 500),
    { cls: "zone-sent", empty: sub.loading ? loadingText : "今天还没有推送记录。", builder: buildSentCard, totalCount: sentToday.length }
  ));

  return card;
}

function renderStrategies(snapshot) {
  const subscriptions = Array.isArray(snapshot) ? snapshot : [];
  strategyListEl.textContent = "";
  if (strategiesCountEl) strategiesCountEl.textContent = String(subscriptions.length);
  if (!subscriptions.length) {
    const empty = document.createElement("p");
    empty.className = "strategy-empty";
    empty.innerHTML = "暂无策略订阅。可以回到 <a href=\"/chat\">Agent</a> 让它帮你创建。";
    strategyListEl.appendChild(empty);
    return;
  }
  const sorted = subscriptions.slice().sort((a, b) => Number(b.id || 0) - Number(a.id || 0));
  for (const sub of sorted) {
    strategyListEl.appendChild(buildStrategyCard(sub));
  }
}

function setRefreshHint(seconds) {
  if (!strategiesRefreshHintEl) return;
  const value = Number(seconds);
  strategiesRefreshHintEl.textContent = `目前是 ${value > 0 ? value : 30} 秒一刷新`;
}

async function loadStrategiesMeta() {
  // 先只拉订阅定义，页面立即出卡片骨架；漏斗数据随后由 /strategies 填充。
  try {
    const data = await api("/api/v1/signal-subscriptions");
    const subscriptions = ((data && data.subscriptions) || []).map((sub) => ({
      ...sub,
      pool: [],
      signals: [],
      sentToday: [],
      loading: true,
    }));
    renderStrategies(subscriptions);
  } catch (_e) {
    // 快速接口失败时等慢速接口兜底。
  }
}

async function loadStrategies() {
  try {
    const data = await api("/api/v1/signal-subscriptions/strategies");
    renderStrategies((data && data.subscriptions) || []);
    if (data && data.refreshSeconds) setRefreshHint(data.refreshSeconds);
  } catch (err) {
    strategyListEl.textContent = "";
    const error = document.createElement("p");
    error.className = "strategy-empty";
    error.textContent = `加载失败：${err.message}`;
    strategyListEl.appendChild(error);
  }
}

function connectStrategiesStream() {
  if (strategiesStream) return;
  strategiesStream = new EventSource("/api/v1/signal-subscriptions/strategies/stream");
  strategiesStream.addEventListener("hello", (msg) => {
    try {
      const data = JSON.parse(msg.data);
      if (data.refreshSeconds) setRefreshHint(data.refreshSeconds);
    } catch (_e) {}
  });
  strategiesStream.addEventListener("snapshot", (msg) => {
    try {
      const data = JSON.parse(msg.data);
      renderStrategies(data.subscriptions || []);
    } catch (_e) {}
  });
  strategiesStream.onerror = () => {
    // EventSource 自动重连，保留当前列表。
  };
}

window.addEventListener("sanyi:subscription-updated", () => {
  loadStrategies();
});

if (refreshStrategiesBtn) {
  refreshStrategiesBtn.addEventListener("click", async () => {
    if (refreshStrategiesBtn.disabled) return;
    refreshStrategiesBtn.disabled = true;
    const oldText = refreshStrategiesBtn.textContent;
    refreshStrategiesBtn.textContent = "刷新中…";
    try {
      await loadStrategies();
    } finally {
      refreshStrategiesBtn.disabled = false;
      refreshStrategiesBtn.textContent = oldText;
    }
  });
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
    const actor = (resp.ok && body.code === 0 && body.data) || {};
    if (actor.user) {
      accountEl.textContent = `手机号 ${actor.user.phoneMasked}`;
    } else if (actor.token) {
      accountEl.textContent = `令牌：${actor.token.name}`;
    }
    await loadStrategiesMeta();
    loadStrategies();
    connectStrategiesStream();
  } catch (_e) {
    strategyListEl.textContent = "";
    const error = document.createElement("p");
    error.className = "strategy-empty";
    error.textContent = "登录状态检查失败，请刷新页面重试。";
    strategyListEl.appendChild(error);
  }
}

boot();
