(function () {
  "use strict";

  const MAX_SLOTS = window.MAX_WRITE_SLOTS || 1;
  const tabsEl = document.getElementById("slotTabs");
  const containerEl = document.getElementById("slotsContainer");
  const template = document.getElementById("slotTemplate");
  const batchPreviewTemplate = document.getElementById("batchPreviewItemTemplate");
  const batchImageTemplate = document.getElementById("batchImageItemTemplate");
  if (!tabsEl || !containerEl || !template) return;

  const slots = {}; // slotId -> { id, num, el, active, state:{step, topic, categoryId, coverFile, inlineFile} }
  let nextSlotNum = 1;

  function q(el, role) {
    return el.querySelector('[data-role="' + role + '"]');
  }

  function escapeHtml(s) {
    const d = document.createElement("div");
    d.textContent = s;
    return d.innerHTML;
  }

  async function postJSON(url, body) {
    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    });

    const rawText = await res.text();
    let data;
    try {
      data = JSON.parse(rawText);
    } catch (parseErr) {
      // Server trả về HTML (trang lỗi 404/500, hoặc bị redirect sang trang đăng nhập) thay vì JSON -
      // thường do server chưa được restart với code mới nhất, hoặc phiên đăng nhập đã hết hạn.
      if (res.status === 404) {
        throw new Error("Không tìm thấy API (" + url + "). Server có thể chưa được restart với code mới nhất.");
      }
      if (rawText.includes("Đăng nhập") || rawText.includes("login")) {
        throw new Error("Phiên đăng nhập đã hết hạn. Vui lòng tải lại trang và đăng nhập lại.");
      }
      throw new Error("Server trả về phản hồi không hợp lệ (mã " + res.status + "). Hãy thử tải lại trang.");
    }

    if (!res.ok) throw new Error(data.error || "Có lỗi xảy ra");
    return data;
  }

  function setBusy(btn, labelEl, busyText) {
    btn.disabled = true;
    labelEl.innerHTML = '<span class="spinner"></span> ' + busyText;
  }
  function unsetBusy(btn, labelEl, normalText) {
    btn.disabled = false;
    labelEl.textContent = normalText;
  }

  // ---- Hiệu ứng "chạy chữ" - gõ từng ký tự lên màn hình như AI đang viết thật ----
  function typeText(el, text, speed, cursor, onDone) {
    el.textContent = "";
    let i = 0;
    function tick() {
      if (i <= text.length) {
        if (cursor) {
          el.innerHTML = escapeHtml(text.slice(0, i)) + '<span class="type-cursor"></span>';
        } else {
          el.textContent = text.slice(0, i);
        }
        i++;
        setTimeout(tick, speed);
      } else {
        if (cursor) el.innerHTML = escapeHtml(text);
        if (onDone) onDone();
      }
    }
    tick();
  }

  function playArticleTypingIn(scopeEl, title, headkey, content, contentHtml, onFullyDone) {
    const aiStatus = q(scopeEl, "ai-status");
    const titleEl = q(scopeEl, "preview-title");
    const headkeyEl = q(scopeEl, "preview-headkey");
    const contentEl = q(scopeEl, "preview-content");
    const confirmBtn = q(scopeEl, "btn-confirm");

    aiStatus.style.display = "inline-flex";
    aiStatus.innerHTML = '<span class="ai-dot"></span> AI đang soạn tiêu đề...';
    confirmBtn.disabled = true;
    titleEl.textContent = "";
    headkeyEl.textContent = "";
    contentEl.textContent = "";
    contentEl.classList.remove("preview-box-formatted");

    typeText(titleEl, title, 26, true, () => {
      headkeyEl.textContent = headkey;
      aiStatus.innerHTML = '<span class="ai-dot"></span> AI đang viết nội dung...';
      typeText(contentEl, content, 4, true, () => {
        // Gõ chữ xong - thay bằng bản HTML có định dạng đúng thật (tiêu đề mục, đoạn văn...)
        // giống hệt lúc bài được đăng, thay vì để nguyên dạng chữ thuần không rõ cấu trúc.
        if (contentHtml) {
          contentEl.innerHTML = contentHtml;
          contentEl.classList.add("preview-box-formatted");
        }
        aiStatus.innerHTML = '<span class="ai-dot" style="animation:none;opacity:1"></span> Hoàn tất soạn thảo';
        confirmBtn.disabled = false;
        if (onFullyDone) onFullyDone();
      });
    });
  }

  function playArticleTyping(slot, title, headkey, content, contentHtml) {
    playArticleTypingIn(slot.el, title, headkey, content, contentHtml);
  }

  function renderCategories(slot, categories) {
    const grid = q(slot.el, "category-grid");
    grid.innerHTML = "";

    const skipChip = document.createElement("div");
    skipChip.className = "category-chip selected";
    skipChip.textContent = "⏭ Không gắn chuyên mục";
    skipChip.dataset.id = "";
    grid.appendChild(skipChip);
    slot.state.categoryId = null;

    (categories || []).forEach((cat) => {
      const chip = document.createElement("div");
      chip.className = "category-chip";
      chip.textContent = cat.name;
      chip.dataset.id = cat.id;
      grid.appendChild(chip);
    });

    grid.querySelectorAll(".category-chip").forEach((chip) => {
      chip.addEventListener("click", () => {
        grid.querySelectorAll(".category-chip").forEach((c) => c.classList.remove("selected"));
        chip.classList.add("selected");
        slot.state.categoryId = chip.dataset.id || null;
      });
    });
  }

  // ---- Đọc tên website đã render sẵn ở bước 1 (checkbox chọn nhiều site) để hiện nhãn ----
  function getWebsiteLabel(slot, wid) {
    const checkbox = slot.el.querySelector('[data-role="website-check"][value="' + wid + '"]');
    if (checkbox) {
      const item = checkbox.closest(".website-check-item");
      if (item) {
        const span = item.querySelector("span");
        if (span) return span.textContent;
      }
    }
    return "Website " + wid;
  }

  // ---- Chế độ đa website: mỗi site 1 khung chuyên mục riêng (vì mỗi WordPress có
  // hệ chuyên mục khác nhau hoàn toàn) ----
  function renderCategoriesMulti(slot, categoriesByWebsite, websiteIds) {
    const wrap = q(slot.el, "category-multi-wrap");
    wrap.innerHTML = "";
    slot.state.categorySelections = {};

    websiteIds.forEach((wid) => {
      const section = document.createElement("div");
      section.className = "category-multi-section";

      const label = document.createElement("div");
      label.className = "category-multi-label";
      label.textContent = getWebsiteLabel(slot, wid);
      section.appendChild(label);

      const grid = document.createElement("div");
      grid.className = "category-grid";

      const skipChip = document.createElement("div");
      skipChip.className = "category-chip selected";
      skipChip.textContent = "⏭ Không gắn chuyên mục";
      skipChip.dataset.id = "";
      grid.appendChild(skipChip);
      slot.state.categorySelections[wid] = null;

      (categoriesByWebsite[wid] || []).forEach((cat) => {
        const chip = document.createElement("div");
        chip.className = "category-chip";
        chip.textContent = cat.name;
        chip.dataset.id = cat.id;
        grid.appendChild(chip);
      });

      grid.querySelectorAll(".category-chip").forEach((chip) => {
        chip.addEventListener("click", () => {
          grid.querySelectorAll(".category-chip").forEach((c) => c.classList.remove("selected"));
          chip.classList.add("selected");
          slot.state.categorySelections[wid] = chip.dataset.id || null;
        });
      });

      section.appendChild(grid);
      wrap.appendChild(section);
    });
  }

  function setupUploadZone(slot, zoneRole, inputRole, onFile) {
    const zone = q(slot.el, zoneRole);
    const input = q(slot.el, inputRole);
    zone.addEventListener("click", () => input.click());
    input.addEventListener("change", () => {
      const file = input.files[0];
      if (!file) return;
      onFile(file);
      const reader = new FileReader();
      reader.onload = (ev) => {
        zone.classList.add("has-image");
        zone.innerHTML = '<img src="' + ev.target.result + '" alt="preview">';
      };
      reader.readAsDataURL(file);
    });
  }

  function checkImagesReady(slot) {
    const btn = q(slot.el, "btn-publish");
    btn.disabled = !(slot.state.coverFile && slot.state.inlineFile);
  }

  function row(label, valueHtml) {
    return '<div class="result-row"><span class="muted">' + label + "</span><span>" + valueHtml + "</span></div>";
  }

  function renderResult(slot, data) {
    const rows = q(slot.el, "result-rows");
    const shareBox = q(slot.el, "result-share");

    if (data.multi) {
      // Đăng đồng thời nhiều website - mỗi site 1 dòng kết quả riêng
      let html = "";
      data.results.forEach((r) => {
        let detail;
        if (r.ok) {
          detail = '<a href="' + r.link + '" target="_blank">' + r.link + "</a> (" + r.status + ")";
          if (!r.sheet_skipped) {
            detail += r.sheet_ok
              ? ' · Sheet <a href="' + r.sheet_url + '" target="_blank">✓</a>'
              : " · Sheet lỗi: " + r.sheet_error;
          }
          if (!r.fb_skipped) {
            detail += r.fb_ok ? " · Facebook ✓" : " · Facebook lỗi: " + r.fb_error;
          }
        } else {
          detail = "⚠️ Lỗi: " + r.error;
        }
        html += row(r.website_label || "Website", detail);
      });
      rows.innerHTML = html;
      shareBox.innerHTML = '<p class="muted" style="margin:0;">Đăng nhiều website cùng lúc - mỗi bài đã tự động chia sẻ theo cấu hình Facebook Page riêng của từng website (nếu có).</p>';
      return;
    }

    let html = "";
    html += row("Trạng thái", data.status);
    html += row("Link bài viết", '<a href="' + data.link + '" target="_blank">' + data.link + "</a>");

    if (!data.sheet_skipped) {
      html += row("Google Sheet", data.sheet_ok
        ? '<a href="' + data.sheet_url + '" target="_blank">Xem Sheet</a>'
        : "⚠️ Lỗi: " + data.sheet_error);
    }
    if (!data.fb_skipped) {
      html += row("Facebook Page", data.fb_ok
        ? (data.fb_link ? '<a href="' + data.fb_link + '" target="_blank">Xem bài đăng</a>' : "Đã đăng")
        : "⚠️ Lỗi: " + data.fb_error);
    }
    rows.innerHTML = html;

    const shareUrl = "https://www.facebook.com/sharer/sharer.php?u=" + encodeURIComponent(data.link);
    shareBox.innerHTML =
      '<div class="field"><label>Nội dung soạn sẵn để chia sẻ vào Group Facebook</label>' +
      '<textarea rows="6" readonly>' + data.share_text + "</textarea></div>" +
      '<a class="btn btn-brass" href="' + shareUrl + '" target="_blank">📤 Mở Facebook để chia sẻ</a>';
  }

  // ---- Chuyển bước trong 1 slot ----
  function goToStep(slot, n) {
    slot.state.step = n;
    slot.el.querySelectorAll(".write-panel").forEach((el) => {
      el.classList.toggle("active", Number(el.dataset.panel) === n);
    });
    slot.el.querySelectorAll(".step").forEach((el) => {
      const s = Number(el.dataset.step);
      el.classList.toggle("active", s === n);
      el.classList.toggle("done", s < n);
    });
    slot.el.querySelectorAll(".step-line").forEach((el, i) => {
      el.classList.toggle("filled", i + 1 < n);
    });
    renderTabs();
    if (n === 4) {
      if (slot.state.mode === "separate") autoFetchImagesBatch(slot);
      else autoFetchImages(slot);
    }
  }

  // ---- Lô "mỗi web 1 bài riêng" - xếp dọc từng website, xác nhận/viết lại độc lập ----
  function renderBatchPreview(slot, items) {
    const list = q(slot.el, "preview-batch-list");
    list.innerHTML = "";
    slot.state.batchConfirmed = {};

    const continueBtn = q(slot.el, "btn-batch-continue");
    continueBtn.disabled = true;

    function checkAllConfirmed() {
      const allDone = items.every((it) => slot.state.batchConfirmed[it.website_id]);
      continueBtn.disabled = !allDone;
    }

    items.forEach((item) => {
      const frag = batchPreviewTemplate.content.cloneNode(true);
      const itemEl = frag.querySelector(".batch-item");
      q(itemEl, "batch-item-label").textContent = "🌐 " + item.website_label;
      list.appendChild(frag);

      const confirmBtn = q(itemEl, "btn-confirm");
      const rewriteBtn = q(itemEl, "btn-rewrite");

      confirmBtn.addEventListener("click", () => {
        slot.state.batchConfirmed[item.website_id] = true;
        confirmBtn.disabled = true;
        confirmBtn.textContent = "✓ Đã xác nhận";
        rewriteBtn.disabled = true;
        checkAllConfirmed();
      });

      rewriteBtn.addEventListener("click", async () => {
        const originalText = rewriteBtn.textContent;
        rewriteBtn.disabled = true;
        rewriteBtn.innerHTML = '<span class="spinner"></span> Đang viết lại...';
        try {
          const data = await postJSON("/api/write/rewrite_batch_item", { slot_id: slot.id, website_id: item.website_id });
          slot.state.batchConfirmed[item.website_id] = false;
          checkAllConfirmed();
          playArticleTypingIn(itemEl, data.title, data.headkey, data.preview, data.content_html);
        } catch (e) {
          alert(e.message);
        } finally {
          rewriteBtn.disabled = false;
          rewriteBtn.textContent = originalText;
        }
      });

      playArticleTypingIn(itemEl, item.title, item.headkey, item.preview, item.content_html);
    });

    continueBtn.onclick = () => goToStep(slot, 3);
  }

  // ---- Tự động tìm & điền sẵn ảnh Unsplash theo chủ đề - chạy 1 lần khi vào bước 4 ----
  function base64ToBlob(base64Str, mime) {
    const byteChars = atob(base64Str);
    const byteNumbers = new Array(byteChars.length);
    for (let i = 0; i < byteChars.length; i++) byteNumbers[i] = byteChars.charCodeAt(i);
    return new Blob([new Uint8Array(byteNumbers)], { type: mime });
  }

  function fillAutoImage(slot, zoneRole, base64Str, kind) {
    const zone = q(slot.el, zoneRole);
    const blob = base64ToBlob(base64Str, "image/jpeg");
    if (kind === "cover") slot.state.coverFile = blob;
    else slot.state.inlineFile = blob;
    zone.classList.add("has-image");
    zone.innerHTML = '<img src="data:image/jpeg;base64,' + base64Str + '" alt="preview">';
  }

  async function autoFetchImages(slot) {
    if (slot.state.imagesAutoFetched) return;
    slot.state.imagesAutoFetched = true;

    const coverZone = q(slot.el, "zone-cover");
    const inlineZone = q(slot.el, "zone-inline");
    coverZone.innerHTML = '<div class="upload-label">⏳ Đang tự tìm ảnh phù hợp...</div>';
    inlineZone.innerHTML = '<div class="upload-label">⏳ Đang tự tìm ảnh phù hợp...</div>';

    try {
      const data = await postJSON("/api/write/auto_image", { slot_id: slot.id });
      fillAutoImage(slot, "zone-cover", data.cover_base64, "cover");
      fillAutoImage(slot, "zone-inline", data.inline_base64, "inline");
      checkImagesReady(slot);
    } catch (e) {
      coverZone.innerHTML = '<div class="upload-label">📸 Tự tìm ảnh thất bại (' + e.message + ') - bấm để tự chọn ảnh bìa</div>';
      inlineZone.innerHTML = '<div class="upload-label">📸 Bấm để tự chọn ảnh trong bài</div>';
    }
  }

  // ---- Lô "mỗi web 1 bài riêng" - mỗi website 1 khối ảnh riêng, xếp dọc ----
  function checkBatchImagesReady(slot) {
    const btn = q(slot.el, "btn-publish");
    const ids = slot.state.websiteIdsBatch || [];
    const allReady = ids.length > 0 && ids.every((wid) => {
      const imgs = (slot.state.batchImages || {})[wid];
      return imgs && imgs.coverFile && imgs.inlineFile;
    });
    btn.disabled = !allReady;
  }

  async function autoFetchImagesBatch(slot) {
    if (slot.state.imagesAutoFetched) return;
    slot.state.imagesAutoFetched = true;

    const wrap = q(slot.el, "image-batch-wrap");
    wrap.innerHTML = "";
    slot.state.batchImages = {};

    const ids = slot.state.websiteIdsBatch || [];
    ids.forEach((wid) => {
      const frag = batchImageTemplate.content.cloneNode(true);
      const itemEl = frag.querySelector(".batch-item");
      itemEl.dataset.websiteId = wid;
      q(itemEl, "batch-item-label").textContent = "🌐 " + (slot.state.websiteLabelsBatch[wid] || "Website " + wid);
      wrap.appendChild(frag);
      slot.state.batchImages[wid] = { coverFile: null, inlineFile: null };

      const zoneCover = q(itemEl, "zone-cover");
      const inputCover = q(itemEl, "input-cover");
      const zoneInline = q(itemEl, "zone-inline");
      const inputInline = q(itemEl, "input-inline");

      zoneCover.addEventListener("click", () => inputCover.click());
      inputCover.addEventListener("change", () => {
        const file = inputCover.files[0];
        if (!file) return;
        slot.state.batchImages[wid].coverFile = file;
        const reader = new FileReader();
        reader.onload = (ev) => {
          zoneCover.classList.add("has-image");
          zoneCover.innerHTML = '<img src="' + ev.target.result + '" alt="preview">';
        };
        reader.readAsDataURL(file);
        checkBatchImagesReady(slot);
      });

      zoneInline.addEventListener("click", () => inputInline.click());
      inputInline.addEventListener("change", () => {
        const file = inputInline.files[0];
        if (!file) return;
        slot.state.batchImages[wid].inlineFile = file;
        const reader = new FileReader();
        reader.onload = (ev) => {
          zoneInline.classList.add("has-image");
          zoneInline.innerHTML = '<img src="' + ev.target.result + '" alt="preview">';
        };
        reader.readAsDataURL(file);
        checkBatchImagesReady(slot);
      });

      function setupBatchReroll(btnRole, zoneRole, which) {
        const btn = q(itemEl, btnRole);
        btn.addEventListener("click", async () => {
          const originalText = btn.textContent;
          btn.disabled = true;
          btn.innerHTML = '<span class="spinner"></span> Đang đổi ảnh...';
          try {
            const data = await postJSON("/api/write/reroll_image", { slot_id: slot.id, which, website_id: wid });
            const zone = q(itemEl, zoneRole);
            const blob = base64ToBlob(data.image_base64, "image/jpeg");
            slot.state.batchImages[wid][which === "cover" ? "coverFile" : "inlineFile"] = blob;
            zone.classList.add("has-image");
            zone.innerHTML = '<img src="data:image/jpeg;base64,' + data.image_base64 + '" alt="preview">';
            checkBatchImagesReady(slot);
          } catch (e) {
            alert(e.message);
          } finally {
            btn.disabled = false;
            btn.textContent = originalText;
          }
        });
      }
      setupBatchReroll("btn-reroll-cover", "zone-cover", "cover");
      setupBatchReroll("btn-reroll-inline", "zone-inline", "inline");

      (async () => {
        try {
          const data = await postJSON("/api/write/auto_image", { slot_id: slot.id, website_id: wid });
          const blobCover = base64ToBlob(data.cover_base64, "image/jpeg");
          const blobInline = base64ToBlob(data.inline_base64, "image/jpeg");
          slot.state.batchImages[wid].coverFile = blobCover;
          slot.state.batchImages[wid].inlineFile = blobInline;
          zoneCover.classList.add("has-image");
          zoneCover.innerHTML = '<img src="data:image/jpeg;base64,' + data.cover_base64 + '" alt="preview">';
          zoneInline.classList.add("has-image");
          zoneInline.innerHTML = '<img src="data:image/jpeg;base64,' + data.inline_base64 + '" alt="preview">';
          checkBatchImagesReady(slot);
        } catch (e) {
          zoneCover.innerHTML = '<div class="upload-label">📸 Tự tìm ảnh thất bại (' + e.message + ') - bấm để tự chọn</div>';
          zoneInline.innerHTML = '<div class="upload-label">📸 Bấm để tự chọn ảnh</div>';
        }
      })();
    });
  }

  // ---- Nút "Đổi ảnh khác" - lấy 1 ảnh khác từ danh sách đã tìm sẵn, không cần tìm lại từ đầu ----
  function setupRerollButton(slot, btnRole, zoneRole, which) {
    const btn = q(slot.el, btnRole);
    if (!btn) return;
    btn.addEventListener("click", async () => {
      const originalText = btn.textContent;
      btn.disabled = true;
      btn.innerHTML = '<span class="spinner"></span> Đang đổi ảnh...';
      try {
        const data = await postJSON("/api/write/reroll_image", { slot_id: slot.id, which });
        fillAutoImage(slot, zoneRole, data.image_base64, which);
        checkImagesReady(slot);
      } catch (e) {
        alert(e.message);
      } finally {
        btn.disabled = false;
        btn.textContent = originalText;
      }
    });
  }

  // ---- Thanh tab các bài đang soạn ----
  function renderTabs() {
    tabsEl.innerHTML = "";
    Object.values(slots).forEach((slot) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "slot-tab" + (slot.active ? " active" : "");
      const stepLabel = slot.state.step >= 5 ? "✓ Hoàn tất" : "Bước " + slot.state.step + "/5";
      btn.innerHTML = "Bài " + slot.num + ' <span class="slot-tab-step">' + stepLabel + "</span>";
      btn.addEventListener("click", () => activateSlot(slot.id));
      tabsEl.appendChild(btn);
    });

    const atMax = Object.keys(slots).length >= MAX_SLOTS;
    const addBtn = document.createElement("button");
    addBtn.type = "button";
    addBtn.className = "slot-tab slot-tab-add" + (atMax ? " disabled" : "");
    addBtn.textContent = atMax ? "Đã đạt tối đa " + MAX_SLOTS + " bài cùng lúc" : "+ Viết bài mới";
    if (!atMax) addBtn.addEventListener("click", createSlot);
    tabsEl.appendChild(addBtn);
  }

  function activateSlot(slotId) {
    Object.values(slots).forEach((s) => {
      s.active = s.id === slotId;
      s.el.style.display = s.active ? "block" : "none";
    });
    renderTabs();
  }

  // ---- Gắn toàn bộ sự kiện cho 1 slot mới tạo ----
  // ---- Sinh bài cho 1 slot cụ thể - dùng chung cho bấm tay và tự tạo nhiều slot ở chế độ "mỗi web 1 bài riêng" ----
  async function runGenerateForSlot(slot, topic, body) {
    slot.state.topic = topic;
    const el = slot.el;
    const btnGenerate = q(el, "btn-generate");
    const label = q(el, "btn-generate-label");
    setBusy(btnGenerate, label, "Đang viết bài...");
    try {
      const data = await postJSON("/api/write/generate", body);
      slot.state.isMulti = !!data.multi;
      if (data.multi) {
        q(el, "category-single-wrap").style.display = "none";
        q(el, "category-multi-wrap").style.display = "block";
        renderCategoriesMulti(slot, data.categories_by_website || {}, data.website_ids || []);
      } else {
        q(el, "category-single-wrap").style.display = "";
        q(el, "category-multi-wrap").style.display = "none";
        renderCategories(slot, data.categories);
      }
      goToStep(slot, 2);
      playArticleTyping(slot, data.title, data.headkey, data.preview, data.content_html);
    } catch (e) {
      alert('Bài "' + topic + '": ' + e.message);
    } finally {
      unsetBusy(btnGenerate, label, "✎ Viết bài");
    }
  }

  // ---- Đọc danh sách {website_id, topic} đã tick + nhập ở chế độ "mỗi web 1 bài riêng" ----
  function collectSeparatePairs(el) {
    const pairs = [];
    let checkedButEmpty = 0;
    el.querySelectorAll('[data-role="website-check-diff"]:checked').forEach((cb) => {
      const wid = cb.value;
      const topicEl = q(el, "website-topic-" + wid);
      const topic = topicEl ? topicEl.value.trim() : "";
      if (topic) {
        pairs.push({ website_id: wid, topic });
      } else {
        checkedButEmpty++;
      }
    });
    return { pairs, checkedButEmpty };
  }

  // ---- Bấm "Viết bài" - phân theo 3 chế độ ----
  function handleGenerateClick(slot) {
    const el = slot.el;
    const mode = slot.state.publishMode;

    if (mode === "separate") {
      const { pairs, checkedButEmpty } = collectSeparatePairs(el);
      if (checkedButEmpty > 0) {
        alert("Có website đã chọn nhưng chưa nhập chủ đề. Vui lòng nhập đủ chủ đề hoặc bỏ chọn website đó.");
        return;
      }
      if (pairs.length === 0) {
        alert("Vui lòng chọn ít nhất 1 website và nhập chủ đề riêng cho nó.");
        return;
      }

      slot.state.mode = "separate";
      const btnGenerate = q(el, "btn-generate");
      const label = q(el, "btn-generate-label");
      setBusy(btnGenerate, label, "Đang viết " + pairs.length + " bài...");

      postJSON("/api/write/generate_batch", { slot_id: slot.id, pairs })
        .then((data) => {
          slot.state.isMulti = true;
          slot.state.websiteIdsBatch = data.website_ids;
          slot.state.websiteLabelsBatch = {};
          data.items.forEach((it) => { slot.state.websiteLabelsBatch[it.website_id] = it.website_label; });

          if (data.warnings && data.warnings.length) {
            alert("Một số bài không tạo được:\n" + data.warnings.join("\n"));
          }

          q(el, "preview-single-wrap").style.display = "none";
          q(el, "preview-batch-wrap").style.display = "block";
          renderBatchPreview(slot, data.items);

          q(el, "category-single-wrap").style.display = "none";
          q(el, "category-multi-wrap").style.display = "block";
          renderCategoriesMulti(slot, data.categories_by_website || {}, data.website_ids || []);

          q(el, "image-single-wrap").style.display = "none";
          q(el, "image-batch-wrap").style.display = "block";

          goToStep(slot, 2);
        })
        .catch((e) => alert(e.message))
        .finally(() => unsetBusy(btnGenerate, label, "✎ Viết bài"));
      return;
    }

    const topicInput = q(el, "topic-input");
    const topic = topicInput.value.trim();
    if (!topic) {
      alert("Vui lòng nhập chủ đề bài viết.");
      return;
    }

    const body = { topic, slot_id: slot.id };
    if (mode === "multi") {
      const checked = Array.from(el.querySelectorAll('[data-role="website-check"]:checked')).map((c) => c.value);
      if (checked.length === 0) {
        alert("Vui lòng chọn ít nhất 1 website để đăng đồng thời.");
        return;
      }
      body.website_ids = checked;
    } else {
      const websiteSelect = q(el, "website-select");
      body.website_id = websiteSelect ? websiteSelect.value : null;
    }

    runGenerateForSlot(slot, topic, body);
  }

  function wireSlot(slot) {
    const el = slot.el;
    slot.state.publishMode = "single";

    // Chuyển đổi 3 chế độ: 1 website / nhiều web cùng nội dung / nhiều web mỗi web 1 bài riêng
    const modeSingleBtn = q(el, "mode-single-btn");
    const modeMultiBtn = q(el, "mode-multi-btn");
    const modeSeparateBtn = q(el, "mode-separate-btn");
    const singleWrap = q(el, "website-single-wrap");
    const multiWrap = q(el, "website-multi-wrap");
    const separateWrap = q(el, "website-separate-wrap");
    const sharedTopicWrap = q(el, "shared-topic-wrap");

    function setMode(mode) {
      slot.state.publishMode = mode;
      [modeSingleBtn, modeMultiBtn, modeSeparateBtn].forEach((b) => b && b.classList.remove("active"));
      if (mode === "single") { modeSingleBtn.classList.add("active"); }
      if (mode === "multi") { modeMultiBtn.classList.add("active"); }
      if (mode === "separate") { modeSeparateBtn.classList.add("active"); }

      if (singleWrap) singleWrap.style.display = mode === "single" ? "" : "none";
      if (multiWrap) multiWrap.style.display = mode === "multi" ? "" : "none";
      if (separateWrap) separateWrap.style.display = mode === "separate" ? "" : "none";
      // Chế độ "mỗi web 1 bài riêng" dùng ô chủ đề RIÊNG cho từng site, không dùng ô chủ đề chung
      if (sharedTopicWrap) sharedTopicWrap.style.display = mode === "separate" ? "none" : "";
    }

    if (modeSingleBtn && modeMultiBtn && modeSeparateBtn) {
      modeSingleBtn.addEventListener("click", () => setMode("single"));
      modeMultiBtn.addEventListener("click", () => setMode("multi"));
      modeSeparateBtn.addEventListener("click", () => setMode("separate"));
    }

    // Ở chế độ "mỗi web 1 bài riêng" - tick chọn website nào thì hiện ô chủ đề riêng của website đó
    el.querySelectorAll('[data-role="website-check-diff"]').forEach((cb) => {
      cb.addEventListener("change", () => {
        const topicEl = q(el, "website-topic-" + cb.value);
        if (topicEl) topicEl.style.display = cb.checked ? "block" : "none";
      });
    });

    const btnGenerate = q(el, "btn-generate");
    btnGenerate.addEventListener("click", () => handleGenerateClick(slot));

    q(el, "btn-confirm").addEventListener("click", () => goToStep(slot, 3));

    q(el, "btn-rewrite").addEventListener("click", async (e) => {
      const btn = e.currentTarget;
      const originalText = btn.textContent;
      btn.disabled = true;
      btn.innerHTML = '<span class="spinner"></span> Đang viết lại...';
      try {
        const data = await postJSON("/api/write/rewrite", { slot_id: slot.id });
        playArticleTyping(slot, data.title, data.headkey, data.preview, data.content_html);
      } catch (e2) {
        alert(e2.message);
      } finally {
        btn.disabled = false;
        btn.textContent = originalText;
      }
    });

    q(el, "btn-cancel-2").addEventListener("click", async () => {
      await postJSON("/api/write/cancel", { slot_id: slot.id });
      closeSlot(slot.id);
    });

    q(el, "btn-category-next").addEventListener("click", () => goToStep(slot, 4));

    setupUploadZone(slot, "zone-cover", "input-cover", (file) => {
      slot.state.coverFile = file;
      checkImagesReady(slot);
    });
    setupUploadZone(slot, "zone-inline", "input-inline", (file) => {
      slot.state.inlineFile = file;
      checkImagesReady(slot);
    });
    setupRerollButton(slot, "btn-reroll-cover", "zone-cover", "cover");
    setupRerollButton(slot, "btn-reroll-inline", "zone-inline", "inline");

    q(el, "btn-publish").addEventListener("click", async () => {
      const btn = q(el, "btn-publish");
      const label = q(el, "btn-publish-label");
      setBusy(btn, label, "Đang đăng bài...");

      const formData = new FormData();
      formData.append("slot_id", slot.id);

      if (slot.state.mode === "separate") {
        (slot.state.websiteIdsBatch || []).forEach((wid) => {
          const imgs = slot.state.batchImages[wid];
          formData.append("cover_" + wid, imgs.coverFile);
          formData.append("inline_" + wid, imgs.inlineFile);
        });
        formData.append("category_map", JSON.stringify(slot.state.categorySelections || {}));
      } else {
        formData.append("cover", slot.state.coverFile);
        formData.append("inline", slot.state.inlineFile);
        if (slot.state.isMulti) {
          formData.append("category_map", JSON.stringify(slot.state.categorySelections || {}));
        } else {
          formData.append("category_id", slot.state.categoryId || "skip");
        }
      }

      try {
        const res = await fetch("/api/write/publish", { method: "POST", body: formData });
        const data = await res.json();
        if (!res.ok) throw new Error(data.error || "Có lỗi khi đăng bài");
        renderResult(slot, data);
        goToStep(slot, 5);
      } catch (e) {
        alert(e.message);
        unsetBusy(btn, label, "🚀 Đăng bài");
      }
    });

    q(el, "btn-write-another").addEventListener("click", () => {
      closeSlot(slot.id);
      createSlot();
    });
  }

  function createSlot() {
    if (Object.keys(slots).length >= MAX_SLOTS) return null;

    const slotId = "s" + Date.now() + Math.floor(Math.random() * 1000);
    const num = nextSlotNum++;
    const frag = template.content.cloneNode(true);
    const el = frag.querySelector(".write-slot");
    containerEl.appendChild(frag);

    const slot = {
      id: slotId, num, el, active: false,
      state: { step: 1, topic: "", categoryId: null, coverFile: null, inlineFile: null },
    };
    slots[slotId] = slot;
    wireSlot(slot);

    Object.values(slots).forEach((s) => {
      s.active = s.id === slotId;
      s.el.style.display = s.active ? "block" : "none";
    });
    renderTabs();
    return slot;
  }

  // ---- Đóng 1 slot (huỷ hoặc viết bài khác) - tự chuyển sang slot còn lại, hoặc mở slot trống mới ----
  function closeSlot(slotId) {
    const slot = slots[slotId];
    if (!slot) return;
    slot.el.remove();
    delete slots[slotId];

    const remaining = Object.values(slots);
    if (remaining.length === 0) {
      createSlot();
    } else if (!remaining.some((s) => s.active)) {
      activateSlot(remaining[0].id);
    } else {
      renderTabs();
    }
  }

  // Luôn mở sẵn 1 slot đầu tiên khi vào trang
  createSlot();
})();
