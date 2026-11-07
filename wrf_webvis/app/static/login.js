(function () {
  "use strict";

  const form = document.querySelector("#loginForm");
  const status = document.querySelector("#loginStatus");
  const submit = document.querySelector("#loginSubmit");

  // 登录后只跳回本站路径（与后端 safe_next 规则一致）。
  function nextTarget() {
    const value = new URLSearchParams(location.search).get("next") || "/";
    if (!value.startsWith("/") || value.startsWith("//") || value.includes("\\") || value.startsWith("/login")) return "/";
    return value;
  }

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    status.textContent = "";
    submit.disabled = true;
    try {
      const response = await fetch("/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          username: document.querySelector("#loginUsername").value.trim(),
          password: document.querySelector("#loginPassword").value,
        }),
      });
      if (response.ok) {
        location.replace(nextTarget());
        return;
      }
      let message = `登录失败（${response.status}）`;
      try {
        const payload = await response.json();
        message = payload?.detail?.message || (typeof payload?.detail === "string" ? payload.detail : message);
      } catch (_) { /* 非 JSON */ }
      status.textContent = message;
      document.querySelector("#loginPassword").select();
    } catch (_) {
      status.textContent = "连不上服务器，请稍后再试。";
    } finally {
      submit.disabled = false;
    }
  });
})();
