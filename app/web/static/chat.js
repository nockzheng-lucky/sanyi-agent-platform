"use strict";

const messagesEl = document.getElementById("messages");
const signalFeedEl = document.getElementById("signalFeed");
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

function addSignalCard(ev) {
  const statusText = ev.status === "OPEN" ? "地门开" : "地门形成·无动作门上";
  const card = document.createElement("div");
  card.className = "signal-card";
  const title = document.createElement("div");
  title.className = "s-title";
  title.textContent = `${ev.symbol} ${ev.frequency} ${statusText}`;
  const meta = document.createElement("div");
  meta.className = "s-meta";
  meta.textContent = ev.summary || "";
  const time = document.createElement("div");
  time.className = "s-time";
  time.textContent = `信号时间：${formatSignalTime(ev.generatedAt || ev.openAt || ev.barTime)}`;
  card.appendChild(title);
  card.appendChild(meta);
  card.appendChild(time);
  card.addEventListener("click", () => {
    chatInput.value = `${ev.symbol} ${ev.frequency} ${statusText}，这个信号怎么看？`;
    chatInput.focus();
  });
  signalFeedEl.prepend(card);
  while (signalFeedEl.children.length > 50) signalFeedEl.lastChild.remove();
}

async function loadLatestSignals() {
  try {
    const data = await api("/api/v1/signal-events/latest?limit=20");
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
      addSignalCard(JSON.parse(msg.data));
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
