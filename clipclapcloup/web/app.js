/* ClipClapCloup — app logic.
   Plain DOM and fetch: no framework, no build step, nothing to load from the
   network (the app has to work the moment it opens, offline or not). */

const state = {
  view: "create",
  settings: {},
  tiktok: { connected: false, redirect_uri: "" },
  waitingForTikTok: false,
};

/* ── tiny helpers ──────────────────────────────────────────── */

const $ = (id) => document.getElementById(id);

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
    body: options.body ? JSON.stringify(options.body) : undefined,
  });
  let data = {};
  try { data = await response.json(); } catch { /* empty body is fine */ }
  if (!response.ok) throw new Error(data.error || "Something went wrong.");
  return data;
}

const get = (path) => api(path);
const post = (path, body) => api(path, { method: "POST", body });
const del = (path) => api(path, { method: "DELETE" });

let toastTimer;
function toast(message) {
  const element = $("toast");
  element.textContent = message;
  element.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { element.hidden = true; }, 2600);
}

function openExternal(url) {
  post("/api/open-url", { url }).catch(() => window.open(url, "_blank"));
}

function relativeDate(iso) {
  if (!iso) return "";
  const date = new Date(iso);
  if (isNaN(date)) return "";
  return date.toLocaleDateString(undefined, { day: "numeric", month: "short" })
    + " · " + date.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
}

/* ── navigation ────────────────────────────────────────────── */

document.querySelectorAll(".nav-item").forEach((button) => {
  button.addEventListener("click", () => switchView(button.dataset.view));
});

function switchView(name) {
  state.view = name;
  document.querySelectorAll(".nav-item").forEach((b) => b.classList.toggle("active", b.dataset.view === name));
  document.querySelectorAll(".view").forEach((v) => v.classList.toggle("active", v.id === `view-${name}`));
  if (name === "library") loadLibrary();
  if (name === "posted") loadPosted();
  if (name === "settings") renderSettings();
}

/* ── boot ──────────────────────────────────────────────────── */

async function boot() {
  try {
    const data = await get("/api/state");
    state.settings = data.settings;
    state.tiktok = data.tiktok;
    $("version-line").textContent = `Version ${data.version}.`;
    applySettingsToForms();
    updateCounts(data.counts);
    if (!data.settings.onboarded) showWizard();
    if (!data.ffmpeg) {
      setStatus("cut-status", "ffmpeg is missing, so clips can't be made. Reinstall the app.", "error");
    }
  } catch (error) {
    setStatus("cut-status", "The app's local service isn't answering. Restart ClipClapCloup.", "error");
  }
}

function updateCounts(counts) {
  const badge = $("count-pending");
  const pending = counts?.pending || 0;
  badge.textContent = pending;
  badge.hidden = pending === 0;
}

function applySettingsToForms() {
  const s = state.settings;
  $("parts").value = String(s.parts ?? 3);
  $("part-duration").value = String(s.part_duration ?? 70);
  $("overlap").value = String(s.overlap ?? 5);
  $("mode").value = s.mode || "blur-pad";
  $("burn-part-label").checked = s.burn_part_label !== false;
  $("set-clips-dir").value = s.clips_dir_resolved || "";
  $("set-caption-language").value = s.caption_language || "en";
  $("set-cookies-browser").value = s.cookies_browser || "";
  $("anthropic-state").textContent = s.anthropic_api_key_set ? "— a key is saved" : "— not set";
}

/* ── first run wizard ──────────────────────────────────────── */

const WIZARD_STEPS = [
  {
    title: "Welcome to ClipClapCloup",
    body: `
      <p>Give it a YouTube link and it gives you back vertical clips, ready for TikTok.</p>
      <ul>
        <li>It listens to the whole video and picks the liveliest stretch.</li>
        <li>It reframes that stretch to 9:16 and splits it into numbered parts.</li>
        <li>It drafts a caption for each one, which you can rewrite.</li>
      </ul>
      <p>Everything happens on this computer. Nothing is uploaded anywhere unless you ask for it.</p>`,
  },
  {
    title: "Where your clips are saved",
    body: `
      <p>Finished clips land in this folder, one sub-folder per video:</p>
      <div class="copy-row"><code id="wizard-folder"></code></div>
      <p style="margin-top:12px">You can change it later in Settings.</p>`,
  },
  {
    title: "How you'll post them",
    body: `
      <p><strong>By hand — the simple way.</strong> Each clip has a “Send to phone” button that shows a QR code.
      Scan it, save the video, post it from the TikTok app as usual. No setup, no limits.</p>
      <p><strong>Through the TikTok API — advanced.</strong> You can connect your own TikTok developer app and
      post straight from here. Be aware that until TikTok reviews that app, everything it posts stays
      <em>private to you</em>. Settings walks you through it if you want it.</p>`,
  },
];

let wizardStep = 0;

function showWizard() {
  wizardStep = 0;
  $("onboarding").hidden = false;
  renderWizard();
}

function renderWizard() {
  const step = WIZARD_STEPS[wizardStep];
  $("wizard-body").innerHTML = `<h2>${esc(step.title)}</h2>${step.body}`;
  const folder = $("wizard-folder");
  if (folder) folder.textContent = state.settings.clips_dir_resolved || "";
  $("wizard-dots").innerHTML = WIZARD_STEPS.map((_, i) => `<i class="${i === wizardStep ? "on" : ""}"></i>`).join("");
  $("wizard-back").style.visibility = wizardStep === 0 ? "hidden" : "visible";
  $("wizard-next").textContent = wizardStep === WIZARD_STEPS.length - 1 ? "Start" : "Next";
}

$("wizard-back").addEventListener("click", () => { wizardStep = Math.max(0, wizardStep - 1); renderWizard(); });
$("wizard-next").addEventListener("click", async () => {
  if (wizardStep < WIZARD_STEPS.length - 1) {
    wizardStep += 1;
    renderWizard();
    return;
  }
  $("onboarding").hidden = true;
  const data = await post("/api/settings", { onboarded: true });
  state.settings = data.settings;
});

/* ── making clips ──────────────────────────────────────────── */

function setStatus(id, message, kind = "") {
  const element = $(id);
  element.className = `status ${kind}`;
  element.textContent = message;
}

$("btn-cut").addEventListener("click", async () => {
  const url = $("url").value.trim();
  if (!url) { setStatus("cut-status", "Paste a YouTube link first.", "error"); return; }

  const button = $("btn-cut");
  button.disabled = true;
  setStatus("cut-status", "");
  $("cut-log").hidden = true;
  $("cut-log-text").textContent = "";
  $("cut-progress").hidden = false;
  $("cut-bar").style.width = "0%";
  $("cut-percent").textContent = "0%";
  $("cut-phase").textContent = "Starting…";

  const options = {
    url,
    parts: Number($("parts").value),
    part_duration: Number($("part-duration").value),
    overlap: Number($("overlap").value),
    mode: $("mode").value,
    hook_text: $("hook-text").value.trim(),
    burn_part_label: $("burn-part-label").checked,
  };

  // Remember the choices so the next video starts from the same place.
  post("/api/settings", {
    parts: options.parts,
    part_duration: options.part_duration,
    overlap: options.overlap,
    mode: options.mode,
    burn_part_label: options.burn_part_label,
  }).catch(() => {});

  try {
    const { job_id } = await post("/api/cut", options);
    const result = await followJob(job_id, (snapshot) => {
      $("cut-phase").textContent = snapshot.detail || snapshot.phase || "Working…";
      $("cut-percent").textContent = `${Math.round(snapshot.percent)}%`;
      $("cut-bar").style.width = `${snapshot.percent}%`;
      if (snapshot.log?.length) {
        $("cut-log-text").textContent += snapshot.log.join("\n") + "\n";
        $("cut-log").hidden = false;
      }
    });

    $("cut-progress").hidden = true;
    const count = result?.clips?.length || 0;
    setStatus("cut-status", `${count} clip${count === 1 ? "" : "s"} ready in “Ready to post”.`, "ok");
    $("url").value = "";
    await refreshCounts();
    switchView("library");
  } catch (error) {
    $("cut-progress").hidden = true;
    setStatus("cut-status", error.message, "error");
  } finally {
    button.disabled = false;
  }
});

async function followJob(jobId, onUpdate) {
  let since = 0;
  for (;;) {
    const snapshot = await get(`/api/jobs/${jobId}?since=${since}`);
    since = snapshot.log_total ?? since;
    onUpdate(snapshot);
    if (snapshot.status === "error") throw new Error(snapshot.error || "The job failed.");
    if (snapshot.status === "done") return snapshot.result;
    await new Promise((resolve) => setTimeout(resolve, 600));
  }
}

async function refreshCounts() {
  try {
    const data = await get("/api/state");
    state.tiktok = data.tiktok;
    updateCounts(data.counts);
  } catch { /* the next poll will catch up */ }
}

/* ── clip lists ────────────────────────────────────────────── */

async function loadLibrary() {
  await renderGroups("/api/groups?status=pending", "library-list", {
    emptyTitle: "Nothing waiting",
    emptyBody: "Clips you make will show up here, ready to check and post.",
  });
}

async function loadPosted() {
  await renderGroups("/api/groups?status=posted", "posted-list", {
    emptyTitle: "Nothing posted yet",
    emptyBody: "Once you post a clip — by hand or through the API — it moves here.",
  });
}

async function renderGroups(endpoint, containerId, empty) {
  const container = $(containerId);
  container.innerHTML = `<p class="muted">Loading…</p>`;
  try {
    const { groups } = await get(endpoint);
    if (!groups.length) {
      container.innerHTML = `<div class="empty"><strong>${esc(empty.emptyTitle)}</strong>${esc(empty.emptyBody)}</div>`;
      return;
    }
    container.innerHTML = groups.map(groupMarkup).join("");
    wireClipActions(container);
  } catch (error) {
    container.innerHTML = `<p class="status error">${esc(error.message)}</p>`;
  }
}

function groupMarkup(group) {
  return `
    <section class="group">
      <div class="group-head">
        <span class="group-title">${esc(group.video_title)}</span>
        <span class="group-meta">${esc(relativeDate(group.created_at))}</span>
      </div>
      ${group.clips.map(clipMarkup).join("")}
    </section>`;
}

function clipMarkup(clip) {
  const parts = clip.parts_total || 1;
  const heading = parts > 1 ? `Part ${clip.part_index} of ${parts}` : "Clip";
  const posted = clip.status !== "pending";
  const pill = clip.status === "published_manual"
    ? `<span class="pill ok">posted by hand</span>`
    : clip.status === "published_api"
      ? `<span class="pill ok">posted via API${clip.privacy === "SELF_ONLY" ? " · private" : ""}</span>`
      : "";

  const actions = posted
    ? `<button class="ghost" data-action="requeue">Put back in queue</button>
       <button class="ghost danger" data-action="forget">Remove from list</button>`
    : `<button class="ghost" data-action="share">Send to phone</button>
       <button class="ghost" data-action="folder">Open folder</button>
       <button class="ghost" data-action="manual">Mark as posted</button>
       ${state.tiktok.connected ? `<button class="ghost" data-action="publish">Post via API</button>` : ""}
       <button class="ghost danger" data-action="delete">Delete</button>`;

  return `
    <article class="clip" data-id="${esc(clip.id)}" data-path="${esc(clip.path || "")}">
      <video src="/media/${esc(clip.id)}" controls preload="metadata" playsinline></video>
      <div class="clip-body">
        <div class="clip-head">
          <span class="clip-part">${esc(heading)}</span>
          <span class="clip-range">${esc(clip.start || "")} → ${esc(clip.end || "")}</span>
          ${pill}
        </div>
        <textarea data-role="caption" ${posted ? "readonly" : ""}
          placeholder="Caption for this clip…">${esc(clip.caption || "")}</textarea>
        <div class="clip-actions">${actions}</div>
        <p class="status" data-role="clip-status"></p>
      </div>
    </article>`;
}

function wireClipActions(container) {
  container.querySelectorAll(".clip").forEach((card) => {
    const id = card.dataset.id;
    const caption = card.querySelector("[data-role=caption]");
    const status = card.querySelector("[data-role=clip-status]");

    if (!caption.readOnly) {
      caption.addEventListener("change", async () => {
        try {
          await post(`/api/clips/${id}/caption`, { caption: caption.value.trim() });
          toast("Caption saved");
        } catch (error) {
          status.className = "status error";
          status.textContent = error.message;
        }
      });
    }

    card.querySelectorAll("[data-action]").forEach((button) => {
      button.addEventListener("click", () => clipAction(button.dataset.action, { id, card, caption, status, button }));
    });
  });
}

async function clipAction(action, context) {
  const { id, card, caption, status, button } = context;
  const setError = (message) => { status.className = "status error"; status.textContent = message; };

  try {
    if (action === "share") {
      const data = await get(`/api/clips/${id}/share`);
      $("share-qr").src = data.qr;
      $("share-url").textContent = data.url;
      $("share-modal").hidden = false;
      return;
    }

    if (action === "folder") {
      await post("/api/open-folder", { path: card.dataset.path });
      return;
    }

    if (action === "manual") {
      if (!caption.value.trim()) { setError("Write a caption before marking it as posted."); return; }
      await post(`/api/clips/${id}/manual`, { caption: caption.value.trim() });
      toast("Moved to Posted");
      await Promise.all([loadLibrary(), refreshCounts()]);
      return;
    }

    if (action === "requeue") {
      await post(`/api/clips/${id}/pending`, {});
      toast("Back in the queue");
      await Promise.all([loadPosted(), refreshCounts()]);
      return;
    }

    if (action === "forget") {
      if (!confirm("Remove this clip from the list? The video file stays on disk.")) return;
      await del(`/api/clips/${id}`);
      await Promise.all([loadPosted(), refreshCounts()]);
      return;
    }

    if (action === "delete") {
      if (!confirm("Delete this clip and its video file? This cannot be undone.")) return;
      await del(`/api/clips/${id}?file=1`);
      await Promise.all([loadLibrary(), refreshCounts()]);
      return;
    }

    if (action === "publish") {
      if (!caption.value.trim()) { setError("Write a caption before posting."); return; }
      button.disabled = true;
      status.className = "status";
      status.textContent = "Starting…";
      const { job_id } = await post(`/api/clips/${id}/publish`, { caption: caption.value.trim() });
      await followJob(job_id, (snapshot) => {
        status.textContent = `${snapshot.detail || "Posting…"} ${Math.round(snapshot.percent)}%`;
      });
      toast("Posted to TikTok");
      await Promise.all([loadLibrary(), refreshCounts()]);
    }
  } catch (error) {
    setError(error.message);
    if (button) button.disabled = false;
  }
}

$("share-close").addEventListener("click", () => { $("share-modal").hidden = true; });
$("share-modal").addEventListener("click", (event) => {
  if (event.target === $("share-modal")) $("share-modal").hidden = true;
});

/* ── settings ──────────────────────────────────────────────── */

function renderSettings() {
  applySettingsToForms();
  renderTikTokSteps();
}

$("set-clips-dir").addEventListener("change", async (event) => {
  const data = await post("/api/settings", { clips_dir: event.target.value.trim() });
  state.settings = data.settings;
  applySettingsToForms();
  toast("Folder saved");
});

$("set-caption-language").addEventListener("change", async (event) => {
  const data = await post("/api/settings", { caption_language: event.target.value });
  state.settings = data.settings;
  toast("Caption language saved");
});

$("set-cookies-browser").addEventListener("change", async (event) => {
  const data = await post("/api/settings", { cookies_browser: event.target.value });
  state.settings = data.settings;
  toast(event.target.value ? `Will borrow cookies from ${event.target.value}` : "Browser cookies turned off");
});

$("btn-open-clips").addEventListener("click", () => post("/api/open-folder", {}).catch(() => {}));

$("btn-save-anthropic").addEventListener("click", async () => {
  const key = $("set-anthropic-key").value.trim();
  if (!key) { toast("Paste a key first"); return; }
  const data = await post("/api/settings", { anthropic_api_key: key });
  state.settings = data.settings;
  $("set-anthropic-key").value = "";
  applySettingsToForms();
  toast("Key saved");
});

function renderTikTokSteps() {
  const pill = $("tiktok-pill");
  pill.textContent = state.tiktok.connected ? "Connected" : "Not connected";
  pill.className = `pill ${state.tiktok.connected ? "ok" : ""}`;

  if (state.tiktok.connected) {
    $("tiktok-steps").innerHTML = `
      <p class="muted" style="margin-top:12px">
        This app is signed in to your TikTok account. Clips in “Ready to post” now have a
        <strong>Post via API</strong> button.
      </p>
      <div class="row">
        <button class="ghost danger" id="btn-tiktok-disconnect">Disconnect</button>
      </div>`;
    $("btn-tiktok-disconnect").addEventListener("click", async () => {
      await post("/api/tiktok/disconnect", {});
      state.tiktok.connected = false;
      renderTikTokSteps();
      toast("Disconnected");
    });
    return;
  }

  $("tiktok-steps").innerHTML = `
    <div class="steps">
      <div class="step">
        <h3>Create a developer app</h3>
        <p>Sign in with the TikTok account you post from, then create an app. Any name works.</p>
        <div class="row"><button class="ghost" data-open="https://developers.tiktok.com/apps/">Open TikTok for Developers</button></div>
      </div>
      <div class="step">
        <h3>Add two products to it</h3>
        <p>In your app's page, add <strong>Login Kit</strong> and <strong>Content Posting API</strong>.
        In the Content Posting API settings, turn on <strong>Direct Post</strong>.</p>
      </div>
      <div class="step">
        <h3>Add a Desktop platform with this redirect address</h3>
        <p>TikTok sends you back here after you approve. Paste this exactly:</p>
        <div class="copy-row">
          <code id="redirect-uri">${esc(state.tiktok.redirect_uri)}</code>
          <button class="ghost" id="btn-copy-redirect">Copy</button>
        </div>
      </div>
      <div class="step">
        <h3>Paste your app's keys here</h3>
        <p>Both are on your app's page, under Basic Information.</p>
        <label class="field" style="margin-top:10px">
          <span class="field-label">Client key</span>
          <input type="text" id="set-client-key" autocomplete="off" spellcheck="false"
            value="${esc(state.settings.tiktok_client_key || "")}">
        </label>
        <label class="field">
          <span class="field-label">Client secret ${state.settings.tiktok_client_secret_set ? '<span class="field-hint">— saved, leave blank to keep</span>' : ""}</span>
          <input type="password" id="set-client-secret" autocomplete="off" placeholder="${state.settings.tiktok_client_secret_set ? "••••••••" : ""}">
        </label>
        <div class="row"><button class="ghost" id="btn-save-tiktok-keys">Save keys</button></div>
      </div>
      <div class="step">
        <h3>Connect</h3>
        <p>This opens TikTok in your browser. Approve the app and you'll be sent straight back.</p>
        <div class="row">
          <button class="primary" id="btn-tiktok-connect">Connect TikTok account</button>
          <span class="status" id="tiktok-connect-status"></span>
        </div>
      </div>
    </div>`;

  $("tiktok-steps").querySelectorAll("[data-open]").forEach((button) => {
    button.addEventListener("click", () => openExternal(button.dataset.open));
  });

  $("btn-copy-redirect").addEventListener("click", () => {
    navigator.clipboard.writeText(state.tiktok.redirect_uri).then(() => toast("Copied"));
  });

  $("btn-save-tiktok-keys").addEventListener("click", async () => {
    const patch = { tiktok_client_key: $("set-client-key").value.trim() };
    const secret = $("set-client-secret").value.trim();
    if (secret) patch.tiktok_client_secret = secret;
    const data = await post("/api/settings", patch);
    state.settings = data.settings;
    toast("Keys saved");
    renderTikTokSteps();
  });

  $("btn-tiktok-connect").addEventListener("click", connectTikTok);
}

async function connectTikTok() {
  const status = $("tiktok-connect-status");
  status.className = "status";
  status.textContent = "Opening TikTok…";
  try {
    const { url } = await get("/api/tiktok/login-url");
    openExternal(url);
    status.textContent = "Waiting for you to approve in the browser…";
    const connected = await waitForTikTok();
    if (connected) {
      state.tiktok.connected = true;
      renderTikTokSteps();
      toast("TikTok connected");
    } else {
      status.className = "status error";
      status.textContent = "Still not connected. Check the keys and try again.";
    }
  } catch (error) {
    status.className = "status error";
    status.textContent = error.message;
  }
}

async function waitForTikTok() {
  for (let attempt = 0; attempt < 90; attempt += 1) {
    await new Promise((resolve) => setTimeout(resolve, 2000));
    try {
      const data = await get("/api/state");
      if (data.tiktok.connected) { state.tiktok = data.tiktok; return true; }
    } catch { /* keep waiting */ }
  }
  return false;
}

boot();
