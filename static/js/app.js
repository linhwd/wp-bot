(function () {
  "use strict";

  // ---- Theme (sáng/tối), lưu lựa chọn vào localStorage ----
  const savedTheme = localStorage.getItem("butmay-theme") || "light";
  document.body.setAttribute("data-theme", savedTheme);

  function applyThemeButtons() {
    document.querySelectorAll("[data-theme-btn]").forEach((b) => {
      b.classList.toggle("active", b.dataset.themeBtn === document.body.getAttribute("data-theme"));
    });
  }
  applyThemeButtons();

  document.querySelectorAll("[data-theme-btn]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const mode = btn.dataset.themeBtn;
      document.body.setAttribute("data-theme", mode);
      localStorage.setItem("butmay-theme", mode);
      applyThemeButtons();
    });
  });

  // ---- Ánh sáng mềm theo con trỏ toàn trang ----
  const glow = document.getElementById("cursorGlow");
  if (glow) {
    document.addEventListener("mousemove", (e) => {
      glow.style.left = e.clientX + "px";
      glow.style.top = e.clientY + "px";
    });
    document.addEventListener("mouseleave", () => (glow.style.opacity = "0"));
    document.addEventListener("mouseenter", () => (glow.style.opacity = ""));
  }

  // ---- Nghiêng nhẹ 3D + ánh sáng bám con trỏ trên card ----
  document.querySelectorAll(".card").forEach((card) => {
    card.addEventListener("mousemove", (e) => {
      const r = card.getBoundingClientRect();
      const px = (e.clientX - r.left) / r.width;
      const py = (e.clientY - r.top) / r.height;
      card.style.setProperty("--mx", px * 100 + "%");
      card.style.setProperty("--my", py * 100 + "%");
    });
  });

  // ---- Gợn mực khi bấm nút/nav ----
  document.addEventListener("click", (e) => {
    const el = e.target.closest(".btn, .nav-link, .category-chip");
    if (!el) return;
    const r = el.getBoundingClientRect();
    const size = Math.max(r.width, r.height) * 1.6;
    const span = document.createElement("span");
    span.className = "ripple";
    span.style.width = span.style.height = size + "px";
    span.style.left = e.clientX - r.left - size / 2 + "px";
    span.style.top = e.clientY - r.top - size / 2 + "px";
    const prevPosition = getComputedStyle(el).position;
    if (prevPosition === "static") el.style.position = "relative";
    el.style.overflow = el.style.overflow || "hidden";
    el.appendChild(span);
    setTimeout(() => span.remove(), 700);
  });

  // ---- AI "đang gõ chữ" thời gian thực trên trang Viết bài
  // write.js tự set text trực tiếp vào #preview-title / #preview-content,
  // ở đây ta chỉ quan sát 2 ô đó và chạy chữ lại mỗi khi nội dung đổi. ----
  (function setupWriterTypewriter() {
    const titleEl = document.getElementById("preview-title");
    const contentEl = document.getElementById("preview-content");
    if (!titleEl && !contentEl) return;

    function typewriter(el, duration) {
      const text = el.textContent;
      if (!text) return;
      el.dataset.typing = "1";
      const start = performance.now();
      function tick(now) {
        const p = Math.min(1, (now - start) / duration);
        const len = Math.round(p * text.length);
        el.textContent = text.slice(0, len) + (p < 1 ? "▌" : "");
        if (p < 1) {
          requestAnimationFrame(tick);
        } else {
          el.textContent = text;
          delete el.dataset.typing;
        }
      }
      requestAnimationFrame(tick);
    }

    function observe(el, duration) {
      if (!el) return;
      let lastText = el.textContent;
      const mo = new MutationObserver(() => {
        if (el.dataset.typing === "1") return;
        const text = el.textContent;
        if (text && text !== lastText) {
          lastText = text;
          typewriter(el, duration);
        }
      });
      mo.observe(el, { childList: true, characterData: true, subtree: true });
    }

    observe(titleEl, 450);
    observe(contentEl, 1300);
  })();

  // ---- Dropdown chuông thông báo / avatar ở thanh trên cùng ----
  function setupTopbarDropdown(btnId, dropdownId) {
    const btn = document.getElementById(btnId);
    const dropdown = document.getElementById(dropdownId);
    if (!btn || !dropdown) return;
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      const willOpen = !dropdown.classList.contains("open");
      document.querySelectorAll(".topbar-dropdown.open").forEach((d) => d.classList.remove("open"));
      if (willOpen) dropdown.classList.add("open");
    });
  }
  setupTopbarDropdown("bellBtn", "bellDropdown");
  setupTopbarDropdown("avatarBtn", "avatarDropdown");
  document.addEventListener("click", () => {
    document.querySelectorAll(".topbar-dropdown.open").forEach((d) => d.classList.remove("open"));
  });
})();
