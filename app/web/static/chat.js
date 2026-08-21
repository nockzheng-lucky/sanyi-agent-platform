"use strict";

const messagesEl = document.getElementById("messages");
const signalFeedEl = document.getElementById("signalFeed");
const signalEmptyEl = document.getElementById("signalEmpty");
const signalCountEl = document.getElementById("signalCount");
const loginCard = document.getElementById("loginCard");
const chatCard = document.getElementById("chatCard");
const accountEl = document.getElementById("account");
const loginForm = document.getElementById("loginForm");
const chatForm = document.getElementById("chatForm");
const chatInput = document.getElementById("chatInput");
const sendBtn = document.getElementById("sendBtn");

let history = [];
let streaming = false;
let signalStream = null;
let soundEnabled = localStorage.getItem("sanyi.sound") !== "0";
let notifyEnabled = false;
let audioCtx = null;

function showLogin() {
  loginCard.classList.remove("hidden");
  chatCard.classList.add("hidden");
  accountEl.textContent = "";
}

function showChat(token) {
  loginCard.classList.add("hidden");
  chatCard.classList.remove("hidden");
  accountEl.textContent = `令牌：${token.name} · 已用 ${token.quotaUsed} / ${token.quotaTotal === -1 ? "不限" : token.quotaTotal}`;
  loadLatestSignals();
  connectSignalStream();
}

function maybeRequestNotification() {
  if (!("Notification" in window)) return;
  if (Notification.permission === "granted") {
    notifyEnabled = true;
  } else if (Notification.permission === "default") {
    Notification.requestPermission().then((permission) => {
      notifyEnabled = permission === "granted";
    });
  }
}

function ensureAudio() {
  if (!audioCtx) {
    const Ctx = window.AudioContext || window.webkitAudioContext;
    if (Ctx) audioCtx = new Ctx();
  }
  if (audioCtx && audioCtx.state === "suspended") audioCtx.resume();
}

function playChime() {
  if (!soundEnabled) return;
  ensureAudio();
  if (!audioCtx) return;
  const now = audioCtx.currentTime;
  [0, 0.12].forEach((offset, i) => {
    const osc = audioCtx.createOscillator();
    const gain = audioCtx.createGain();
    osc.type = "sine";
    osc.frequency.value = i === 0 ? 880 : 1174.66;
    gain.gain.setValueAtTime(0.0001, now + offset);
    gain.gain.exponentialRampToValueAtTime(0.16, now + offset + 0.02);
    gain.gain.exponentialRampToValueAtTime(0.0001, now + offset + 0.18);
    osc.connect(gain);
    gain.connect(audioCtx.destination);
    osc.start(now + offset);
    osc.stop(now + offset + 0.2);
  });
}

function signalText(ev) {
  return ev.status === "OPEN" ? "地门开" : "地门形成·无动作门上";
}

function notifySignal(ev) {
  if (!notifyEnabled || Notification.permission !== "granted") return;
  const status = signalText(ev);
  const target = ev.contract || ev.symbol;
  const title = `${target} ${ev.frequency} ${status}`;
  const body = [
    ev.summary,
    ev.generatedAt || ev.generated_at || ev.barTime || ev.bar_time ? `信号时间：${ev.generatedAt || ev.generated_at || ev.barTime || ev.bar_time}` : "",
    ev.gatePrice || ev.gate_price ? `门价 ${ev.gatePrice || ev.gate_price}` : "",
  ].filter(Boolean).join("\n");
  const notification = new Notification(title, { body, tag: ev.eventId || undefined });
  notification.onclick = () => {
    window.focus();
    notification.close();
  };
}

document.addEventListener("pointerdown", () => {
  ensureAudio();
  maybeRequestNotification();
}, { once: true });

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

function formatSignalTime(value) {
  if (!value) return "时间未知";
  const text = String(value).replace("T", " ").slice(0, 19);
  return text;
}

function refreshSignalEmptyState() {
  const hasCards = signalFeedEl.querySelector(".signal-card") !== null;
  if (signalEmptyEl) signalEmptyEl.style.display = hasCards ? "none" : "block";
}

function firstDefined(...values) {
  for (const value of values) {
    if (value !== null && value !== undefined && value !== "") return value;
  }
  return null;
}

function addSignalCard(ev, { isNew = false } = {}) {
  const statusText = signalText(ev);
  const badgeCls = ev.status === "OPEN" ? "open" : "formation";
  const symbol = firstDefined(ev.symbol);
  const frequency = firstDefined(ev.frequency);
  const contractValue = firstDefined(ev.contract);
  const eventTime = firstDefined(
    ev.generatedAt, ev.generated_at,
    ev.openAt, ev.open_at,
    ev.barTime, ev.bar_time
  );
  const gatePrice = firstDefined(ev.gatePrice, ev.gate_price);
  const currentPrice = firstDefined(ev.currentPrice, ev.current_price);

  const card = document.createElement("div");
  card.className = isNew ? "signal-card pushed" : "signal-card";

  const top = document.createElement("div");
  top.className = "s-top";
  const title = document.createElement("div");
  title.className = "s-title";
  title.textContent = `${contractValue || symbol} · ${frequency}`;
  const badge = document.createElement("span");
  badge.className = `s-badge ${badgeCls}`;
  badge.textContent = statusText;
  top.appendChild(title);
  top.appendChild(badge);

  const time = document.createElement("div");
  time.className = "s-row s-time";
  time.textContent = `信号时间：${formatSignalTime(eventTime)}`;

  const price = document.createElement("div");
  price.className = "s-row";
  const priceParts = [];
  if (gatePrice !== null) priceParts.push(`门价 ${gatePrice}`);
  if (currentPrice !== null) priceParts.push(`现价 ${currentPrice}`);
  price.textContent = priceParts.join(" · ");

  const contract = document.createElement("div");
  contract.className = "s-row";
  contract.textContent = contractValue ? `品种 ${symbol}` : `品种 ${symbol} · 合约待标注`;

  card.appendChild(top);
  card.appendChild(time);
  card.appendChild(contract);
  if (priceParts.length) card.appendChild(price);

  card.addEventListener("click", () => {
    const target = contractValue || symbol;
    chatInput.value = `${target} ${frequency} ${statusText}，这个信号怎么看？`;
    chatInput.focus();
  });

  signalFeedEl.prepend(card);
  while (signalFeedEl.querySelectorAll(".signal-card").length > 50) {
    signalFeedEl.lastChild.remove();
  }
  if (signalCountEl) signalCountEl.textContent = String(signalFeedEl.querySelectorAll(".signal-card").length);
  refreshSignalEmptyState();
}

async function loadLatestSignals() {
  try {
    // 只回放“服务启动后新出现”的信号；首次启动的历史 baseline 不展示。
    const data = await api("/api/v1/signal-events/latest?limit=20&includeBaseline=false");
    for (const ev of data.events.slice().reverse()) addSignalCard(ev);
  } catch (_e) {
    // 不阻塞聊天
  }
}

function connectSignalStream() {
  if (signalStream) return;
  signalStream = new EventSource("/api/v1/signal-events/stream");
  signalStream.addEventListener("signal", (msg) => {
    try {
      const ev = JSON.parse(msg.data);
      addSignalCard(ev, { isNew: true });
      playChime();
      notifySignal(ev);
    } catch (_e) {}
  });
  signalStream.onerror = () => {
    // EventSource 自动重连；这里不打断聊天。
  };
}

async function boot() {
  try {
    const data = await api("/api/chat/session");
    showChat(data.token);
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
  let currentTool = null;

  try {
    const resp = await fetch("/api/chat", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ messages: history }),
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
          currentTool = event.data.name;
          addChip(toolRow, `工具：${event.data.name}`, "");
        } else if (event.event === "tool_result") {
          const err = event.data.result && event.data.result.error;
          addChip(toolRow, err ? "因子调用失败" : "三易引擎已返回", err ? "err" : "ok");
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

boot();
