"use strict";

(function () {
  if (document.getElementById("appSidebar")) return;

  const path = window.location.pathname;
  const items = [
    { href: "/chat", label: "信号聊天", key: "chat" },
    { href: "/keys", label: "Key 管理", key: "keys" },
    { href: "/subscription", label: "订阅", key: "subscription" },
    { href: "/admin", label: "管理后台", key: "admin" },
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
})();
