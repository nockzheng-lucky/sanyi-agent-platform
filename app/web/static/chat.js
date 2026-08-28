"use strict";

const messagesEl = document.getElementById("messages");
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
  renderLoadedFactors();
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
      body: JSON.stringify({ messages: history, factorKeys: getLoadedFactorKeys() }),
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
