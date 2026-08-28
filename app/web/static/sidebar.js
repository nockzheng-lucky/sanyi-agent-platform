"use strict";

(function () {
  if (document.getElementById("appSidebar")) return;

  const path = window.location.pathname;
  const items = [
    { href: "/chat", label: "Agent", key: "chat" },
    { href: "/factors", label: "因子列表", key: "factors" },
    { href: "/keys", label: "Key 管理", key: "keys" },
    { href: "/subscription", label: "订阅", key: "subscription" },
  ];

  const aside = document.createElement("aside");
  aside.id = "appSidebar";
  aside.className = "app-sidebar";

  const brand = document.createElement("div");
  brand.className = "side-brand";
  brand.innerHTML = '<span class="logo">三易</span><div><h1>信号系统</h1><p>Agent 平台</p></div>';

  const nav = document.createElement("nav");
  nav.className = "side-nav";
  for (const item of items) {
    const a = document.createElement("a");
    a.href = item.href;
    a.textContent = item.label;
    if (path.startsWith(item.href)) a.className = "active";
    nav.appendChild(a);
  }

  const logout = document.createElement("button");
  logout.type = "button";
  logout.className = "side-logout";
  logout.textContent = "退出登录";
  logout.addEventListener("click", async () => {
    try {
      await fetch("/api/v1/auth/logout", {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: "{}",
      });
    } catch (_e) {}
    window.location.href = "/login";
  });

  aside.appendChild(brand);
  aside.appendChild(nav);
  aside.appendChild(logout);
  document.body.prepend(aside);
  document.body.classList.add("has-sidebar");
  document.querySelectorAll(".topbar").forEach((el) => { el.style.display = "none"; });

  // 只有 admin 角色才显示管理后台入口。
  fetch("/api/v1/auth/me", { credentials: "same-origin" })
    .then((resp) => (resp.ok ? resp.json() : null))
    .then((body) => {
      if (body && body.code === 0 && body.data && body.data.role === "admin") {
        const adminLink = document.createElement("a");
        adminLink.href = "/admin";
        adminLink.textContent = "管理后台";
        if (path.startsWith("/admin")) adminLink.className = "active";
        nav.appendChild(adminLink);
      }
    })
    .catch(() => {});
})();
