"use strict";

async function api(url, options = {}) {
  const resp = await fetch(url, {
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  const body = await resp.json().catch(() => ({}));
  if (!resp.ok) throw new Error(body.detail?.message || body.message || `请求失败（${resp.status}）`);
  return body.data;
}

function showToast(message) {
  let toast = document.getElementById("toast");
  if (!toast) {
    toast = document.createElement("div");
    toast.id = "toast";
    toast.className = "toast";
    document.body.appendChild(toast);
  }
  toast.textContent = message;
  toast.classList.add("show");
  clearTimeout(showToast._timer);
  showToast._timer = setTimeout(() => toast.classList.remove("show"), 1800);
}

async function refresh() {
  const sub = await api("/api/v1/subscription");
  const status = document.getElementById("status");
  status.textContent = sub.expiresAt
    ? `当前权益：${sub.plan || "已开通"}，到期 ${String(sub.expiresAt).replace("T", " ").slice(0, 19)}`
    : "当前权益：未开通";
}

document.getElementById("giftForm").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const error = document.getElementById("giftError");
  error.textContent = "";
  try {
    const result = await api("/api/v1/subscription/redeem", {
      method: "POST",
      body: JSON.stringify({ code: document.getElementById("giftCode").value.trim() }),
    });
    showToast(`兑换成功，增加 ${result.periodDays} 天`);
    document.getElementById("giftCode").value = "";
    await refresh();
  } catch (err) {
    error.textContent = err.message;
  }
});

refresh().catch((err) => {
  if (err.message.includes("401")) window.location.href = "/login";
  else document.getElementById("giftError").textContent = err.message;
});
