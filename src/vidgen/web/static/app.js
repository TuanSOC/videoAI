// vidgen studio — vanilla SPA: hash router, three views, polling while jobs run.
const view = document.getElementById("view");
const WPS = { vi: 3.3, en: 2.5 }; // spoken words/second, mirrors script/writer.py
const STATUS = {
  review: "Chờ duyệt", queued: "Đang chờ", running: "Đang xử lý", rendered: "Thiếu metadata",
  done: "Hoàn tất", error: "Lỗi", empty: "Trống",
};
const STEPS = [
  ["script", "Kịch bản"], ["voice", "Giọng đọc"], ["visuals", "Hình ảnh"],
  ["render", "Dựng video"], ["metadata", "Metadata"],
];
const VTYPES = { stock: "Stock", ai_image: "Ảnh AI", ai_video: "Video AI" };
let pollTimer = null;

// ---------- helpers ----------
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" }, ...opts,
    body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
  });
  const data = res.headers.get("content-type")?.includes("json") ? await res.json() : null;
  if (!res.ok) {
    const d = data?.detail;
    const msg = Array.isArray(d) ? d.map((e) => `${e.loc?.slice(-2).join(".")}: ${e.msg}`).join("; ") : d;
    throw new Error(msg || `HTTP ${res.status}`);
  }
  return data;
}

function toast(msg, kind = "") {
  const el = document.createElement("div");
  el.className = `toast ${kind}`;
  el.textContent = msg;
  document.getElementById("toasts").append(el);
  setTimeout(() => el.remove(), kind === "error" ? 7000 : 3500);
}

function ago(ts) {
  const s = Math.max(1, Math.round(Date.now() / 1000 - ts));
  if (s < 60) return "vừa xong";
  if (s < 3600) return `${Math.round(s / 60)} phút trước`;
  if (s < 86400) return `${Math.round(s / 3600)} giờ trước`;
  return `${Math.round(s / 86400)} ngày trước`;
}

const fmtSec = (s) => `${Math.floor(s / 60)}:${String(Math.round(s % 60)).padStart(2, "0")}`;
const words = (t) => (t.trim() ? t.trim().split(/\s+/).length : 0);
const pill = (status) => `<span class="pill st-${status}"><span class="dot"></span>${STATUS[status] || status}</span>`;

function stopPolling() { clearTimeout(pollTimer); pollTimer = null; }
function poll(fn, ms) { stopPolling(); pollTimer = setTimeout(fn, ms); }

// ---------- router ----------
async function route() {
  stopPolling();
  const hash = location.hash || "#/";
  document.querySelectorAll("[data-nav]").forEach((a) =>
    a.classList.toggle("active", (a.dataset.nav === "settings") === hash.startsWith("#/settings")));
  const m = hash.match(/^#\/v\/([a-z0-9-]+)/);
  try {
    if (m) await renderDetail(m[1]);
    else if (hash.startsWith("#/settings")) await renderSettings();
    else await renderLibrary();
  } catch (e) {
    view.innerHTML = `<div class="banner error"><div><b>Không tải được trang.</b><pre>${esc(e.message)}</pre></div></div>`;
  }
  view.focus({ preventScroll: true });
}
window.addEventListener("hashchange", route);

// ---------- health badge ----------
async function refreshHealth() {
  const el = document.getElementById("health");
  try {
    const checks = await api("/api/doctor");
    const missing = checks.filter((c) => c.required && !c.ok);
    const warns = checks.filter((c) => !c.required && !c.ok);
    el.className = `health ${missing.length ? "bad" : warns.length ? "warn" : "ok"}`;
    el.querySelector(".health-label").textContent = missing.length
      ? `Thiếu ${missing.length} cấu hình` : warns.length ? "Sẵn sàng (có cảnh báo)" : "Sẵn sàng";
  } catch {
    el.className = "health bad";
    el.querySelector(".health-label").textContent = "Mất kết nối";
  }
}

// ---------- library ----------
async function renderLibrary() {
  const videos = await api("/api/videos");
  if (!["", "#/", "#"].includes(location.hash)) return;
  view.innerHTML = `
    <div class="page-head">
      <div><h1>Thư viện video</h1><p>Nhập chủ đề → duyệt kịch bản → render → đăng.</p></div>
    </div>
    <form class="card new-video" id="new-form">
      <label class="field"><span>Chủ đề video</span>
        <textarea class="input" name="topic" rows="2" required minlength="3" maxlength="200"
          placeholder="VD: Bí ẩn tam giác Bermuda, Vì sao bạch tuộc có 3 trái tim…"></textarea></label>
      <div class="row">
        <div class="field"><span>Định dạng</span>
          <div class="segmented" role="radiogroup">
            <input type="radio" id="f-short" name="format" value="short" checked><label for="f-short">Short 9:16</label>
            <input type="radio" id="f-long" name="format" value="long"><label for="f-long">Long 16:9</label>
          </div></div>
        <div class="field"><span>Ngôn ngữ</span>
          <div class="segmented" role="radiogroup">
            <input type="radio" id="l-vi" name="lang" value="vi" checked><label for="l-vi">Tiếng Việt</label>
            <input type="radio" id="l-en" name="lang" value="en"><label for="l-en">English</label>
          </div></div>
        <label class="check grow"><input type="checkbox" name="auto_render"> Bỏ qua bước duyệt, render luôn</label>
        <button class="btn primary" type="submit">Tạo kịch bản</button>
      </div>
    </form>
    <h2 class="section-title">${videos.length} video</h2>
    ${videos.length ? `<div class="grid">${videos.map(videoCard).join("")}</div>` : `
      <div class="empty"><strong>Chưa có video nào</strong>Nhập chủ đề ở trên để tạo video đầu tiên.</div>`}`;

  document.getElementById("new-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const f = new FormData(ev.target);
    const btn = ev.target.querySelector("button[type=submit]");
    btn.disabled = true;
    try {
      const v = await api("/api/videos", { method: "POST", body: {
        topic: f.get("topic"), format: f.get("format"), lang: f.get("lang"), auto_render: f.has("auto_render"),
      } });
      location.hash = `#/v/${v.slug}`;
    } catch (e) {
      toast(`Không tạo được: ${e.message}`, "error");
      btn.disabled = false;
    }
  });

  if (videos.some((v) => v.status === "running" || v.status === "queued")) {
    poll(() => renderLibrary().catch(() => {}), 3000);
  }
}

function videoCard(v) {
  const thumb = v.has_video
    ? `<img src="/media/${v.slug}/thumb.jpg?t=${Math.round(v.updated)}" alt="" loading="lazy">`
    : `<span class="fmt-icon ${v.format}"></span>`;
  const stage = v.stage ? ` · ${STEPS.find((s) => s[0] === v.stage)?.[1] || v.stage}` : "";
  return `
    <a class="card vcard" href="#/v/${v.slug}">
      <div class="thumb">${thumb}${pill(v.status)}</div>
      <div class="body">
        <h3>${esc(v.title)}</h3>
        <div class="meta">${v.format === "short" ? "Short" : "Long"} · ${v.lang.toUpperCase()}${v.scenes ? ` · ${v.scenes} cảnh` : ""}${stage} · ${ago(v.updated)}</div>
      </div>
    </a>`;
}

// ---------- detail ----------
let draft = null;   // working copy of script being edited
let dirty = false;

async function renderDetail(slug, { keepDraft = false } = {}) {
  const v = await api(`/api/videos/${slug}`);
  if (location.hash !== `#/v/${slug}`) return; // user navigated away while this was loading
  const active = v.status === "running" || v.status === "queued";
  if (!keepDraft || !draft || draft._slug !== slug) {
    draft = v.script ? { ...structuredClone(v.script), _slug: slug } : null;
    dirty = false;
  }
  const lang = v.script?.lang || v.lang;
  const job = v.job;

  view.innerHTML = `
    <a class="crumb" href="#/">← Thư viện</a>
    <div class="detail-head">
      <div>
        <h1>${esc(v.title)}</h1>
        <div class="pills">${pill(v.status)}
          <span class="pill">${v.format === "short" ? "Short 9:16" : "Long 16:9"}</span>
          <span class="pill">${lang === "vi" ? "Tiếng Việt" : "English"}</span></div>
      </div>
      <div class="actions">
        <button class="btn ghost sm" id="regen" ${active ? "disabled" : ""}>Viết lại kịch bản</button>
        <button class="btn danger sm" id="delete" ${active ? "disabled" : ""}>Xóa</button>
        <button class="btn" id="save" ${active || !draft ? "disabled" : ""}>Lưu kịch bản</button>
        <button class="btn primary" id="render" ${active || !draft ? "disabled" : ""}>
          ${v.has_video ? "Render lại" : "Render video"}</button>
      </div>
    </div>
    ${job?.status === "error" ? `<div class="banner error"><div><b>${job.kind === "script" ? "Không viết được kịch bản" : "Render thất bại"}.</b>
        Sửa nguyên nhân (thường là thiếu API key — xem <a href="#/settings"><u>Cài đặt</u></a>) rồi thử lại.
        <pre>${esc(job.error)}</pre></div></div>` : ""}
    ${v.status === "review" && !active ? `<div class="banner info">Kịch bản sẵn sàng. Sửa lời đọc và từ khóa hình nếu cần, rồi bấm <b>Render video</b>.</div>` : ""}
    <div class="layout">
      <section id="scenes"></section>
      <aside class="side">
        <div class="card panel"><h2 class="section-title">Tiến trình</h2>${stepsHtml(v)}</div>
        <div class="card panel"><h2 class="section-title">Video</h2>${playerHtml(v)}</div>
        ${v.metadata ? `<div class="card panel"><h2 class="section-title">Metadata để đăng</h2>${metadataHtml(v.metadata)}</div>` : ""}
      </aside>
    </div>`;

  renderScenes(lang, active, job);
  wireDetail(slug, v);
  if (active) poll(() => renderDetail(slug, { keepDraft: true }).catch(() => {}), 1500);
  else if (job?.status === "done" && job.finished_at > Date.now() / 1000 - 3) refreshHealth();
}

function stepsHtml(v) {
  const job = v.job;
  const failedAt = job?.status === "error" ? (job.current || (job.kind === "script" ? "script" : "")) : "";
  return `<div class="steps">${STEPS.map(([key, label]) => {
    const js = job?.stages?.[key] || "";
    const has = key === "script" ? !!v.script : v.artifacts?.[key];
    let cls = "", ic = "";
    if (job && (job.status === "running" || job.status === "queued") && job.current === key) cls = "run";
    else if (failedAt === key) { cls = "fail"; ic = "!"; }
    else if (has) { cls = "done"; ic = "✓"; }
    const t = cls === "done" && v.timings?.[key] != null ? `${v.timings[key]}s` : js === "skip" ? "có sẵn" : "";
    return `<div class="step ${cls}"><span class="ic">${ic}</span>${label}<span class="t">${t}</span></div>`;
  }).join("")}</div>`;
}

function playerHtml(v) {
  if (!v.has_video) return `<div class="player-empty">Chưa có video — render để xem</div>`;
  const src = `/media/${v.slug}/final.mp4?t=${Math.round(v.updated)}`;
  return `<div class="player ${v.format}"><video controls preload="metadata" src="${src}"></video></div>
    <div class="hint">File: output/${esc(v.slug)}/final.mp4</div>`;
}

function metadataHtml(m) {
  const block = (label, text, id) => `
    <div class="meta-block"><div class="meta-label">${label}<button class="btn ghost sm" data-copy="${id}">Copy</button></div>
    <div class="meta-value" id="${id}">${esc(text)}</div></div>`;
  return block("Tiêu đề", m.title, "m-title")
    + block("Mô tả", m.description, "m-desc")
    + `<div class="meta-block"><div class="meta-label">Tags<button class="btn ghost sm" data-copy="m-tags">Copy</button></div>
       <div class="tags" id="m-tags" data-text="${esc(m.tags.join(", "))}">${m.tags.map((t) => `<span class="tag">${esc(t)}</span>`).join("")}</div></div>`
    + (m.ai_visuals_used ? `<div class="ai-flag">Video có hình AI → khi đăng, bật nhãn “Nội dung đã chỉnh sửa/tổng hợp” (YouTube) / “AI-generated” (TikTok).</div>` : "");
}

function renderScenes(lang, active, job) {
  const box = document.getElementById("scenes");
  if (!draft) {
    const writing = job && (job.status === "queued" || job.status === "running") && job.kind === "script";
    box.innerHTML = writing
      ? `<div class="scenes-head"><h2 class="section-title">Đang viết kịch bản…</h2></div>${'<div class="skeleton"></div>'.repeat(4)}`
      : `<div class="empty"><strong>Chưa có kịch bản</strong>Bấm “Viết lại kịch bản” để thử lại.</div>`;
    return;
  }
  const total = draft.scenes.reduce((n, s) => n + words(s.narration), 0);
  let lastChapter = null;
  box.innerHTML = `
    <div class="scenes-head">
      <div class="stats"><span><b>${draft.scenes.length}</b> cảnh</span><span><b>${total}</b> từ</span>
        <span>≈ <b>${fmtSec(total / WPS[lang])}</b></span></div>
      <span class="dirty" id="dirty" ${dirty ? "" : "hidden"}>● Chưa lưu</span>
    </div>
    ${draft.sources?.length
      ? `<div class="sources">Dữ kiện lấy từ: ${draft.sources.map((s) =>
          `<a href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.title)}</a>`).join(" · ")}
          — vẫn nên đối chiếu trước khi render.</div>`
      : `<div class="sources warn">Không tìm được nguồn tham khảo — hãy kiểm tra kỹ các dữ kiện trong kịch bản.</div>`}
    ${draft.scenes.map((s, i) => {
      const chapter = s.chapter && s.chapter !== lastChapter ? `<div class="chapter">${esc(s.chapter)}</div>` : "";
      lastChapter = s.chapter;
      return chapter + sceneHtml(s, i, lang, active);
    }).join("")}
    <button class="btn ghost" id="add-scene" ${active ? "disabled" : ""}>+ Thêm cảnh</button>`;
}

function sceneHtml(s, i, lang, active) {
  const dis = active ? "disabled" : "";
  const w = words(s.narration);
  return `
    <div class="card scene" data-i="${i}">
      <div class="scene-top">
        <span class="scene-num">#${i + 1}</span>
        <span class="scene-info">${w} từ · ≈ ${(w / WPS[lang]).toFixed(1)}s</span>
        <div class="scene-tools">
          <button class="btn ghost icon sm" data-act="up" title="Lên" ${dis || (i === 0 ? "disabled" : "")}>↑</button>
          <button class="btn ghost icon sm" data-act="down" title="Xuống" ${dis || (i === draft.scenes.length - 1 ? "disabled" : "")}>↓</button>
          <button class="btn ghost icon sm" data-act="insert" title="Chèn cảnh bên dưới" ${dis}>+</button>
          <button class="btn ghost icon sm danger" data-act="remove" title="Xóa cảnh" ${dis || (draft.scenes.length === 1 ? "disabled" : "")}>✕</button>
        </div>
      </div>
      <textarea class="input narration" data-f="narration" rows="2" aria-label="Lời đọc cảnh ${i + 1}" ${dis}>${esc(s.narration)}</textarea>
      <div class="scene-row">
        <input class="input" data-f="visual_query" value="${esc(s.visual_query)}" placeholder="Từ khóa hình (tiếng Anh)" aria-label="Từ khóa hình" ${dis}>
        <select class="input" data-f="visual_type" aria-label="Loại hình" ${dis}>
          ${Object.entries(VTYPES).map(([k, l]) => `<option value="${k}" ${s.visual_type === k ? "selected" : ""}>${l}</option>`).join("")}
        </select>
      </div>
      ${s.visual_type !== "stock" ? `<textarea class="input ai-prompt" data-f="ai_prompt" rows="2" placeholder="Prompt AI (tiếng Anh, mô tả chân thực)" aria-label="Prompt AI" ${dis}>${esc(s.ai_prompt)}</textarea>` : ""}
    </div>`;
}

function markDirty() {
  dirty = true;
  document.getElementById("dirty")?.removeAttribute("hidden");
}

async function saveDraft(slug) {
  const { _slug, ...script } = draft;
  await api(`/api/videos/${slug}/script`, { method: "PUT", body: script });
  dirty = false;
}

function wireDetail(slug, v) {
  const box = document.getElementById("scenes");
  const lang = v.script?.lang || v.lang;

  box.addEventListener("input", (ev) => {
    const card = ev.target.closest(".scene");
    const f = ev.target.dataset.f;
    if (!card || !f) return;
    draft.scenes[+card.dataset.i][f] = ev.target.value;
    markDirty();
    if (f === "narration") {
      const w = words(ev.target.value);
      card.querySelector(".scene-info").textContent = `${w} từ · ≈ ${(w / WPS[lang]).toFixed(1)}s`;
    }
  });
  box.addEventListener("change", (ev) => {
    if (ev.target.dataset.f === "visual_type") renderScenes(lang, false, v.job);
  });
  box.addEventListener("click", (ev) => {
    const btn = ev.target.closest("button");
    if (!btn) return;
    if (btn.id === "add-scene") {
      draft.scenes.push({ id: 0, narration: "", visual_query: "", visual_type: "stock", ai_prompt: "", chapter: draft.scenes.at(-1)?.chapter ?? null });
    } else if (btn.dataset.act) {
      const i = +btn.closest(".scene").dataset.i;
      const sc = draft.scenes;
      if (btn.dataset.act === "remove") sc.splice(i, 1);
      if (btn.dataset.act === "insert") sc.splice(i + 1, 0, { id: 0, narration: "", visual_query: "", visual_type: "stock", ai_prompt: "", chapter: sc[i].chapter ?? null });
      if (btn.dataset.act === "up") [sc[i - 1], sc[i]] = [sc[i], sc[i - 1]];
      if (btn.dataset.act === "down") [sc[i + 1], sc[i]] = [sc[i], sc[i + 1]];
    } else return;
    markDirty();
    renderScenes(lang, false, v.job);
    document.getElementById("dirty")?.removeAttribute("hidden");
  });

  document.getElementById("save").addEventListener("click", async () => {
    try { await saveDraft(slug); toast("Đã lưu kịch bản"); renderDetail(slug); }
    catch (e) { toast(`Lưu thất bại: ${e.message}`, "error"); }
  });

  document.getElementById("render").addEventListener("click", async (ev) => {
    ev.target.disabled = true;
    try {
      if (dirty) await saveDraft(slug);
      await api(`/api/videos/${slug}/render`, { method: "POST", body: {} });
      toast("Đã bắt đầu render");
      renderDetail(slug);
    } catch (e) { toast(`Không render được: ${e.message}`, "error"); ev.target.disabled = false; }
  });

  document.getElementById("regen").addEventListener("click", async (ev) => {
    const b = ev.currentTarget;
    if (!b.classList.contains("confirm")) {
      b.classList.add("confirm", "danger"); b.textContent = "Ghi đè kịch bản hiện tại?";
      setTimeout(() => { b.classList.remove("confirm", "danger"); b.textContent = "Viết lại kịch bản"; }, 4000);
      return;
    }
    try { await api(`/api/videos/${slug}/script/regenerate`, { method: "POST" }); draft = null; renderDetail(slug); }
    catch (e) { toast(e.message, "error"); }
  });

  document.getElementById("delete").addEventListener("click", async (ev) => {
    const b = ev.currentTarget;
    if (!b.classList.contains("confirm")) {
      b.classList.add("confirm"); b.textContent = "Xác nhận xóa?";
      setTimeout(() => { b.classList.remove("confirm"); b.textContent = "Xóa"; }, 4000);
      return;
    }
    try { await api(`/api/videos/${slug}`, { method: "DELETE" }); toast("Đã xóa video"); location.hash = "#/"; }
    catch (e) { toast(e.message, "error"); }
  });

  document.querySelectorAll("[data-copy]").forEach((b) => b.addEventListener("click", async () => {
    const el = document.getElementById(b.dataset.copy);
    const text = el.dataset.text ?? el.textContent;
    try { await navigator.clipboard.writeText(text); b.textContent = "Đã copy"; setTimeout(() => (b.textContent = "Copy"), 1500); }
    catch { toast("Trình duyệt chặn clipboard — hãy bôi đen và copy tay", "error"); }
  }));
}

window.addEventListener("beforeunload", (ev) => { if (dirty) { ev.preventDefault(); } });

// ---------- settings ----------
const KEY_INFO = {
  GEMINI_API_KEY: ["Gemini API key", "Viết kịch bản + metadata (free)", "https://aistudio.google.com/apikey"],
  PEXELS_API_KEY: ["Pexels API key", "Stock video/ảnh (free, 200 lượt/giờ)", "https://www.pexels.com/api/"],
  PIXABAY_API_KEY: ["Pixabay API key", "Stock dự phòng (free)", "https://pixabay.com/api/docs/"],
  COMFYUI_URL: ["ComfyUI URL", "Ảnh/video AI local (tùy chọn)", ""],
  OLLAMA_URL: ["Ollama URL", "LLM local dự phòng (tùy chọn)", ""],
};

async function loadChecks() {
  const box = document.getElementById("checks");
  if (!box) return;
  box.innerHTML = '<div class="skeleton" style="height:220px"></div>';
  try {
    const checks = await api("/api/doctor");
    box.innerHTML = `<table class="checks">${checks.map((c) => `
      <tr><td><div class="d ${c.ok ? "ok" : c.required ? "bad" : "warn"}"></div></td>
      <td>${esc(c.name)}<div class="detail">${esc(c.detail)}</div></td></tr>`).join("")}</table>`;
  } catch (e) {
    box.innerHTML = `<div class="banner error">${esc(e.message)}</div>`;
  }
}

async function renderSettings() {
  const keys = await api("/api/settings");
  if (!location.hash.startsWith("#/settings")) return;
  view.innerHTML = `
    <div class="page-head"><div><h1>Cài đặt</h1><p>Key lưu trong file <code>.env</code> trên máy bạn, không gửi đi đâu khác.</p></div></div>
    <div class="settings">
      <div class="card panel"><h2 class="section-title">Kiểm tra hệ thống</h2>
        <div id="checks"></div>
        <button class="btn sm" id="recheck" style="margin-top:12px">Kiểm tra lại</button>
      </div>
      <form class="card panel keys" id="keys-form"><h2 class="section-title">API key & dịch vụ</h2>
        ${Object.entries(KEY_INFO).map(([k, [label, desc, url]]) => {
          const cur = keys[k] || {};
          const isUrl = k.endsWith("_URL");
          return `<label class="field"><span>${label}</span>
            <input class="input" name="${k}" type="${isUrl ? "url" : "password"}" autocomplete="off"
              value="${isUrl ? esc(cur.value || "") : ""}"
              placeholder="${!isUrl && cur.set ? `Đã lưu ${esc(cur.hint)} — để trống nếu không đổi` : isUrl ? "" : "Chưa có"}">
            <div class="hint">${desc}${url ? ` · <a href="${url}" target="_blank" rel="noopener">Lấy key</a>` : ""}
              ${!isUrl && cur.set ? ` · <span class="saved">✓ đã lưu</span>` : ""}</div></label>`;
        }).join("")}
        <div><button class="btn primary" type="submit">Lưu cài đặt</button></div>
      </form>
    </div>`;

  loadChecks();
  document.getElementById("recheck").addEventListener("click", () => { loadChecks(); refreshHealth(); });
  document.getElementById("keys-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const body = {};
    for (const [k, val] of new FormData(ev.target)) if (String(val).trim()) body[k] = String(val).trim();
    if (!Object.keys(body).length) return toast("Không có gì thay đổi");
    try { await api("/api/settings", { method: "PUT", body }); toast("Đã lưu cài đặt"); await renderSettings(); refreshHealth(); }
    catch (e) { toast(`Lưu thất bại: ${e.message}`, "error"); }
  });
}

refreshHealth();
route();
