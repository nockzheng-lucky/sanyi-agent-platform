"use strict";

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

function setError(el, message) {
  if (el) el.textContent = message || "";
}

function initLogin() {
  const form = document.getElementById("loginForm");
  if (!form) return;
  form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    setError(document.getElementById("loginError"), "");
    try {
      await api("/api/v1/auth/login", {
        method: "POST",
        body: JSON.stringify({
          phone: document.getElementById("phone").value.trim(),
          password: document.getElementById("password").value,
        }),
      });
      window.location.href = "/chat";
    } catch (err) {
      setError(document.getElementById("loginError"), err.message);
    }
  });
}

function initRegister() {
  const form = document.getElementById("registerForm");
  if (!form) return;
  const sendBtn = document.getElementById("sendCodeBtn");
  sendBtn.addEventListener("click", async () => {
    setError(document.getElementById("formError"), "");
    setError(document.getElementById("formHint"), "");
    sendBtn.disabled = true;
    try {
      const data = await api("/api/v1/auth/sms-code", {
        method: "POST",
        body: JSON.stringify({ phone: document.getElementById("phone").value.trim(), purpose: "register" }),
      });
      const hint = document.getElementById("formHint");
      if (hint) {
        hint.textContent = data.debugCode
          ? `验证码已发送（MOCK 模式，测试验证码：${data.debugCode}）`
          : "验证码已发送，请查收短信";
      }
      if (data.debugCode) document.getElementById("code").value = data.debugCode;
    } catch (err) {
      setError(document.getElementById("formError"), err.message);
    } finally {
      sendBtn.disabled = false;
    }
  });

  form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    setError(document.getElementById("formError"), "");
    try {
      await api("/api/v1/auth/register", {
        method: "POST",
        body: JSON.stringify({
          phone: document.getElementById("phone").value.trim(),
          code: document.getElementById("code").value.trim(),
          password: document.getElementById("password").value,
          agree: document.getElementById("agree").checked,
        }),
      });
      window.location.href = "/chat";
    } catch (err) {
      setError(document.getElementById("formError"), err.message);
    }
  });
}

function initKeys() {
  const form = document.getElementById("keyForm");
  if (!form) return;

  async function refresh() {
    const list = document.getElementById("keyList");
    list.textContent = "";
    try {
      const data = await api("/api/v1/keys");
      const me = await api("/api/v1/auth/me");
      document.getElementById("account").textContent = `手机号 ${me.phoneMasked}`;
      if (!data.keys.length) {
        const empty = document.createElement("p");
        empty.className = "hint";
        empty.textContent = "还没有 Key，请先申请一个。";
        list.appendChild(empty);
        return;
      }
      for (const key of data.keys) {
        const row = document.createElement("div");
        row.className = "key-row";
        const info = document.createElement("div");
        info.innerHTML = `<strong>${key.name}</strong> · <span>${key.tokenPrefix}…</span><br>` +
          `<small>状态：${key.status} · 有效期：${key.expiresAt || "长期"} · 额度：${key.quotaUsed}/${key.quotaTotal === -1 ? "不限" : key.quotaTotal}</small>`;
        const actions = document.createElement("div");
        actions.className = "key-actions";
        if (key.status === "active") {
          const rotate = document.createElement("button");
          rotate.textContent = "轮换";
          rotate.type = "button";
          rotate.addEventListener("click", async () => {
            try {
              const created = await api(`/api/v1/keys/${key.id}/rotate`, { method: "POST", body: "{}" });
              showNewKey(created);
              await refresh();
            } catch (err) { alert(err.message); }
          });
          const revoke = document.createElement("button");
          revoke.textContent = "撤销";
          revoke.type = "button";
          revoke.addEventListener("click", async () => {
            if (!confirm(`确定撤销 Key「${key.name}」？撤销后立即失效。`)) return;
            try {
              await api(`/api/v1/keys/${key.id}/revoke`, { method: "POST", body: "{}" });
              await refresh();
            } catch (err) { alert(err.message); }
          });
          actions.appendChild(rotate);
          actions.appendChild(revoke);
        }
        row.appendChild(info);
        row.appendChild(actions);
        list.appendChild(row);
      }
    } catch (err) {
      if (err.message.includes("401") || err.message.includes("登录")) {
        window.location.href = "/login";
      } else {
        alert(err.message);
      }
    }
  }

  function showNewKey(created) {
    const box = document.getElementById("newKey");
    box.classList.remove("hidden");
    box.textContent = `新 Key（只显示一次，请立即保存）：${created.token}`;
  }

  form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    try {
      const created = await api("/api/v1/keys", {
        method: "POST",
        body: JSON.stringify({
          name: document.getElementById("keyName").value.trim(),
          expiresInDays: parseInt(document.getElementById("expiresDays").value || "0", 10) || null,
          allowIps: document.getElementById("allowIps").value.trim(),
        }),
      });
      showNewKey(created);
      await refresh();
    } catch (err) { alert(err.message); }
  });

  const logout = document.getElementById("logoutLink");
  if (logout) {
    logout.addEventListener("click", async (ev) => {
      ev.preventDefault();
      try { await api("/api/v1/auth/logout", { method: "POST", body: "{}" }); } catch (_e) {}
      window.location.href = "/login";
    });
  }

  refresh();
}

if (document.getElementById("loginForm")) initLogin();
if (document.getElementById("registerForm")) initRegister();
if (document.getElementById("keyForm")) initKeys();
