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

function row(text, actions = []) {
  const div = document.createElement("div");
  div.className = "key-row";
  const info = document.createElement("div");
  info.innerHTML = text;
  div.appendChild(info);
  const box = document.createElement("div");
  box.className = "key-actions";
  for (const a of actions) box.appendChild(a);
  div.appendChild(box);
  return div;
}

function button(text, onClick) {
  const b = document.createElement("button");
  b.type = "button";
  b.textContent = text;
  b.addEventListener("click", onClick);
  return b;
}

async function refresh() {
  const users = await api("/api/v1/admin/users");
  const userList = document.getElementById("userList");
  userList.textContent = "";
  for (const u of users.users) {
    const statusText = u.status === "active" ? "启用" : "禁用";
    const action = button(
      u.status === "active" ? "禁用" : "启用",
      async () => {
        await api(`/api/v1/admin/users/${u.id}/status`, {
          method: "POST",
          body: JSON.stringify({ status: u.status === "active" ? "disabled" : "active" }),
        });
        refresh();
      }
    );
    userList.appendChild(row(
      `<strong>${u.phone_masked}</strong> · ${statusText} · ${u.role} · Key ${u.key_count}`,
      [action]
    ));
  }

  const keys = await api("/api/v1/admin/keys");
  const keyList = document.getElementById("keyList");
  keyList.textContent = "";
  for (const k of keys.keys) {
    const revoke = button("撤销", async () => {
      if (!confirm(`撤销 ${k.phoneMasked} 的 Key「${k.name}」？`)) return;
      await api(`/api/v1/admin/keys/${k.id}/revoke`, { method: "POST", body: "{}" });
      refresh();
    });
    keyList.appendChild(row(
      `<strong>${k.name}</strong> · ${k.phoneMasked} · ${k.tokenPrefix}…<br><small>${k.status} · ${k.expiresAt || "长期"}</small>`,
      k.status === "active" ? [revoke] : []
    ));
  }

  const subs = await api("/api/v1/admin/subscription-requests");
  const subList = document.getElementById("subList");
  subList.textContent = "";
  if (!subs.requests.length) {
    subList.innerHTML = '<p class="hint">暂无待确认申请</p>';
  }
  for (const r of subs.requests) {
    const actions = [];
    if (r.status === "pending_payment") {
      actions.push(button("确认收款", async () => {
        if (!confirm(`确认收到 ${r.phone_masked} 的 ${r.plan} 款项？确认后自动开启权益。`)) return;
        await api(`/api/v1/admin/subscription-requests/${r.id}/confirm`, { method: "POST", body: JSON.stringify({ note: "线下确认" }) });
        refresh();
      }));
      actions.push(button("驳回", async () => {
        await api(`/api/v1/admin/subscription-requests/${r.id}/reject`, { method: "POST", body: JSON.stringify({ note: "驳回" }) });
        refresh();
      }));
    }
    subList.appendChild(row(
      `<strong>${r.phone_masked}</strong> · ${r.plan} · ¥${(r.amount_cents / 100).toFixed(2)} / ${r.period_days}天<br><small>${r.status} · ${r.payment_note || ""} · ${r.created_at}</small>`,
      actions
    ));
  }

  const usage = await api("/api/v1/admin/usage");
  const usageList = document.getElementById("usageList");
  usageList.textContent = "";
  for (const u of usage.usage.slice(0, 30)) {
    usageList.appendChild(row(
      `${u.phone_masked || "-"} · ${u.service}/${u.action} · ${u.factor_key || u.model || "-"}<br><small>${u.input_tokens} in / ${u.output_tokens} out · ${u.status} · ${u.created_at}</small>`
    ));
  }
}

(async () => {
  try {
    await refresh();
  } catch (err) {
    if (err.message.includes("403") || err.message.includes("管理员")) {
      alert("你不是管理员");
      window.location.href = "/chat";
    } else if (err.message.includes("401") || err.message.includes("登录")) {
      window.location.href = "/login";
    } else {
      alert(err.message);
    }
  }
})();
