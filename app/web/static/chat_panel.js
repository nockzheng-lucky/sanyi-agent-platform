"use strict";

const chatPanelMessagesEl = document.getElementById("agentChatMessages");
const chatPanelForm = document.getElementById("agentChatForm");
const chatPanelInput = document.getElementById("agentChatInput");
const chatPanelSendBtn = document.getElementById("agentChatSend");

let chatPanelStreaming = false;
let chatPanelAbort = null;
let chatPanelSyncing = false;

function chatPanelIsNearBottom() {
  if (!chatPanelMessagesEl) return true;
  return chatPanelMessagesEl.scrollHeight - chatPanelMessagesEl.scrollTop - chatPanelMessagesEl.clientHeight < 48;
}

function chatPanelScrollToBottom(force = false) {
  if (!chatPanelMessagesEl) return;
  if (force || chatPanelIsNearBottom()) {
    chatPanelMessagesEl.scrollTop = chatPanelMessagesEl.scrollHeight;
  }
}

async function chatPanelApi(url, options = {}) {
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

function chatPanelAddMessage(role, text, options = {}) {
  const div = document.createElement("div");
  div.className = `msg ${role}`;
  div.textContent = text;
  chatPanelMessagesEl.appendChild(div);
  const scrollMode = options.scroll || "auto";
  if (scrollMode === "force") {
    chatPanelScrollToBottom(true);
  } else if (scrollMode === "auto") {
    chatPanelScrollToBottom(false);
  }
  return div;
}

function chatPanelParseSse(part) {
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

async function chatPanelLoadHistory() {
  if (chatPanelSyncing || chatPanelStreaming) return;
  chatPanelSyncing = true;
  const stickToBottom = chatPanelIsNearBottom();
  const previousScrollTop = chatPanelMessagesEl.scrollTop;
  try {
    const data = await chatPanelApi("/api/chat/history");
    const messages = (data && data.messages) || [];
    chatPanelMessagesEl.textContent = "";
    if (!messages.length) {
      const empty = document.createElement("p");
      empty.className = "agent-chat-empty";
      empty.textContent = "还没有对话。你可以在这里直接问 Agent。";
      chatPanelMessagesEl.appendChild(empty);
      return;
    }
    for (const item of messages) {
      if (!item || (item.role !== "user" && item.role !== "assistant")) continue;
      if (typeof item.content === "string" && item.content) {
        chatPanelAddMessage(item.role, item.content, { scroll: "none" });
      }
    }
    if (stickToBottom) {
      chatPanelScrollToBottom(true);
    } else {
      chatPanelMessagesEl.scrollTop = previousScrollTop;
    }
  } catch (_e) {
    // 登录失效由 boot 处理；网络抖动保留当前内容。
  } finally {
    chatPanelSyncing = false;
  }
}

function chatPanelLoadedFactorKeys() {
  try {
    const value = JSON.parse(localStorage.getItem("sanyi.loadedFactors") || "[]");
    if (!Array.isArray(value)) return [];
    return value.filter((item) => item && typeof item.factorKey === "string" && item.factorKey).map((item) => item.factorKey);
  } catch (_e) {
    return [];
  }
}

async function chatPanelSubmit(text) {
  chatPanelStreaming = true;
  chatPanelSendBtn.disabled = true;
  chatPanelInput.value = "";
  chatPanelAddMessage("user", text, { scroll: "force" });

  const assistantEl = chatPanelAddMessage("assistant", "", { scroll: "force" });
  const toolRow = document.createElement("div");
  toolRow.className = "tool-row";
  chatPanelMessagesEl.appendChild(toolRow);
  chatPanelScrollToBottom(true);

  try {
    chatPanelAbort = new AbortController();
    const resp = await fetch("/api/chat", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        messages: [{ role: "user", content: text }],
        factorKeys: chatPanelLoadedFactorKeys(),
        persistHistory: true,
      }),
      signal: chatPanelAbort.signal,
    });
    if (!resp.ok) {
      const body = await resp.json().catch(() => ({}));
      throw new Error((body.detail && body.detail.message) || body.message || `请求失败（${resp.status}）`);
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
        const event = chatPanelParseSse(part);
        if (!event) continue;
        if (event.event === "delta") {
          assistantText += event.data.text || "";
          assistantEl.textContent = assistantText;
          chatPanelScrollToBottom(false);
        } else if (event.event === "tool_call") {
          const chip = document.createElement("span");
          chip.className = "chip";
          chip.textContent = "查询三易引擎…";
          toolRow.appendChild(chip);
        } else if (event.event === "tool_result") {
          const chip = document.createElement("span");
          chip.className = "chip " + (event.data.result && event.data.result.error ? "err" : "ok");
          chip.textContent = event.data.result && event.data.result.error ? "查询失败" : "三易引擎已返回";
          toolRow.appendChild(chip);
        } else if (event.event === "subscription_update") {
          const chip = document.createElement("span");
          chip.className = "chip ok";
          chip.textContent = "订阅已更新";
          toolRow.appendChild(chip);
          window.dispatchEvent(new CustomEvent("sanyi:subscription-updated"));
        } else if (event.event === "filter_update") {
          const chip = document.createElement("span");
          chip.className = "chip ok";
          chip.textContent = "筛选条件已更新";
          toolRow.appendChild(chip);
        } else if (event.event === "error") {
          assistantEl.textContent += `\n[错误] ${event.data.message}`;
        }
      }
    }
    if (!assistantText) assistantEl.textContent = "（模型未返回文本）";
  } catch (err) {
    if (!(err && err.name === "AbortError")) {
      assistantEl.textContent = `请求失败：${err.message}`;
    }
  } finally {
    chatPanelStreaming = false;
    chatPanelSendBtn.disabled = false;
    chatPanelAbort = null;
    chatPanelInput.focus();
  }
}

if (chatPanelForm) {
  chatPanelForm.addEventListener("submit", (ev) => {
    ev.preventDefault();
    const text = chatPanelInput.value.trim();
    if (!text || chatPanelStreaming) return;
    chatPanelSubmit(text);
  });
}

window.addEventListener("pagehide", () => {
  if (chatPanelAbort) {
    chatPanelAbort.abort();
    chatPanelAbort = null;
  }
  chatPanelStreaming = false;
  if (chatPanelSendBtn) chatPanelSendBtn.disabled = false;
});

async function chatPanelBoot() {
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
    await chatPanelLoadHistory();
    setInterval(() => {
      chatPanelLoadHistory();
    }, 5000);
  } catch (_e) {
    chatPanelMessagesEl.textContent = "";
    const error = document.createElement("p");
    error.className = "agent-chat-empty";
    error.textContent = "对话加载失败，请刷新页面重试。";
    chatPanelMessagesEl.appendChild(error);
  }
}

chatPanelBoot();
