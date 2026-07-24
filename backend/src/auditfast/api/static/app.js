"use strict";

// AuditFAST web UI. Drives the same engine the MCP server exposes, one step per card.
// Every network call goes to /api/*, which adapts services.py; no logic lives here
// beyond sequencing and rendering.

const $ = (id) => document.getElementById(id);
const state = { engagementId: null, proposal: null };

async function api(method, path, body) {
  const res = await fetch(path, {
    method,
    headers: body ? { "Content-Type": "application/json" } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  let data = {};
  try { data = await res.json(); } catch (_) { /* empty body */ }
  if (!res.ok) {
    // Service errors arrive as { detail: { error, next_step } }.
    const detail = data.detail || data;
    const err = new Error(detail.error || `Request failed (${res.status})`);
    err.nextStep = detail.next_step;
    throw err;
  }
  return data;
}

function showMsg(id, text, kind = "info", nextStep) {
  const el = $(id);
  el.className = `msg show ${kind}`;
  el.innerHTML = text + (nextStep ? `<br><span class="muted">Next: ${escapeHtml(nextStep)}</span>` : "");
}
function hideMsg(id) { $(id).className = "msg"; }
function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function enableCard(id) { $(id).classList.remove("dim"); }
function markDone(id) { $(id).classList.add("done"); }

function setAccount(name) {
  $("account").innerHTML = name ? `signed in as <b>${escapeHtml(name)}</b>` : "not signed in";
}

// -- Step 1: auth ------------------------------------------------------------

async function refreshStatus() {
  try {
    const s = await api("GET", "/api/status");
    if (s.configuration_error) {
      showMsg("auth-msg", escapeHtml(s.configuration_error), "err");
      return;
    }
    if (s.signed_in_as) {
      setAccount(s.signed_in_as);
      onSignedIn();
    }
    if (!s.definition_reads_enabled) {
      // Non-fatal, just informative — pipeline/notebook checks will be skipped.
      console.info("Definition reads are OFF; pipeline & notebook checks will report evidence_unavailable.");
    }
  } catch (e) { /* status is best-effort */ }
}

let pollTimer = null;
async function beginSignIn() {
  hideMsg("auth-msg");
  $("btn-signin").disabled = true;
  try {
    const r = await api("POST", "/api/auth/sign-in");
    if (r.already_signed_in) {
      setAccount(r.account);
      onSignedIn();
      return;
    }
    const link = $("verify-link");
    link.href = r.verification_uri;
    link.textContent = r.verification_uri;
    $("user-code").textContent = r.user_code;
    $("code-box").style.display = "block";
    $("auth-poll").innerHTML = '<span class="spinner"></span> waiting for you to sign in...';
    pollComplete();
  } catch (e) {
    showMsg("auth-msg", escapeHtml(e.message), "err", e.nextStep);
    $("btn-signin").disabled = false;
  }
}

async function pollComplete() {
  try {
    const r = await api("POST", "/api/auth/complete", { poll_seconds: 15 });
    if (r.signed_in) {
      setAccount(r.account);
      $("auth-poll").textContent = "";
      $("code-box").style.display = "none";
      onSignedIn();
      return;
    }
    if (r.error) {
      showMsg("auth-msg", escapeHtml(r.error), "err", r.next_step);
      $("btn-signin").disabled = false;
      return;
    }
    pollTimer = setTimeout(pollComplete, 1500); // still pending
  } catch (e) {
    showMsg("auth-msg", escapeHtml(e.message), "err", e.nextStep);
    $("btn-signin").disabled = false;
  }
}

function onSignedIn() {
  if (pollTimer) clearTimeout(pollTimer);
  markDone("card-auth");
  $("btn-signin").textContent = "Signed in";
  $("btn-signin").disabled = true;
  enableCard("card-eng");
}

// -- Step 2: engagement ------------------------------------------------------

async function startEngagement() {
  const project = $("project").value.trim();
  const ws = $("ws").value.trim();
  if (!project || !ws) {
    showMsg("eng-msg", "Enter both a project name and a workspace reference.", "err");
    return;
  }
  hideMsg("eng-msg");
  const btn = $("btn-start");
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span> discovering...';
  try {
    const eng = await api("POST", "/api/engagements", {
      workspace_url: ws, project_name: project,
    });
    state.engagementId = eng.engagement_id;
    const proposal = await api("POST", `/api/engagements/${eng.engagement_id}/discover`, {});
    state.proposal = proposal;
    markDone("card-eng");
    renderScope(proposal);
    enableCard("card-scope");
    $("card-scope").scrollIntoView({ behavior: "smooth" });
  } catch (e) {
    showMsg("eng-msg", escapeHtml(e.message), "err", e.nextStep);
  } finally {
    btn.disabled = false;
    btn.textContent = "Create & discover";
  }
}

// -- Step 3: scope proposal --------------------------------------------------

function renderScope(p) {
  const counts = Object.entries(p.inventory_counts || {})
    .map(([t, n]) => `${escapeHtml(t)}: ${n}`).join(" &middot; ") || "no items";

  const relevantRows = (p.artifacts_relevant || []).map((a) => `
    <tr>
      <td><input type="checkbox" class="art" data-id="${escapeHtml(a.id)}" checked></td>
      <td>${escapeHtml(a.name)}</td><td><code>${escapeHtml(a.type)}</code></td>
      <td class="muted">${escapeHtml(a.why)}</td>
    </tr>`).join("");

  const excludedRows = (p.artifacts_excluded || []).map((a) => `
    <tr>
      <td><input type="checkbox" class="art-add" data-id="${escapeHtml(a.id)}"></td>
      <td>${escapeHtml(a.name)}</td><td><code>${escapeHtml(a.type)}</code></td>
      <td class="muted">${escapeHtml(a.why_not)}</td>
    </tr>`).join("");

  const warnHtml = (p.warnings || []).length
    ? `<ul class="warnlist">${p.warnings.map((w) => `<li>${escapeHtml(w)}</li>`).join("")}</ul>` : "";

  $("scope-body").innerHTML = `
    <div class="banner">Workspace <b style="margin:0 4px">${escapeHtml(p.workspace_name)}</b>
      &mdash; ${counts}. <b style="margin-left:4px">${p.checks_in_scope.length}</b> checks would run.</div>
    ${warnHtml}
    <h3 style="font-size:14px;margin:16px 0 0">Relevant artifacts (${(p.artifacts_relevant || []).length})</h3>
    <table><thead><tr><th></th><th>Name</th><th>Type</th><th>Why</th></tr></thead>
      <tbody>${relevantRows || '<tr><td colspan="4" class="muted">None auto-selected.</td></tr>'}</tbody></table>
    ${excludedRows ? `
      <details><summary>Excluded artifacts (${(p.artifacts_excluded || []).length}) — tick to include</summary>
        <table><thead><tr><th></th><th>Name</th><th>Type</th><th>Why not</th></tr></thead>
          <tbody>${excludedRows}</tbody></table></details>` : ""}
    <details><summary>Checks in scope (${p.checks_in_scope.length})</summary>
      <table><thead><tr><th>Item</th><th>Area</th><th>Check</th><th>Def read?</th></tr></thead>
        <tbody>${p.checks_in_scope.map((c) => `<tr><td><code>${escapeHtml(c.item_id)}</code></td>
          <td>${c.area}</td><td>${escapeHtml(c.title)}</td>
          <td>${c.needs_definition_read ? "yes" : "no"}</td></tr>`).join("")}</tbody></table></details>
    <div class="row">
      <button id="btn-confirm">Confirm scope &amp; run audit</button>
      <span class="msg" id="scope-msg"></span>
    </div>`;

  $("btn-confirm").addEventListener("click", confirmAndRun);
}

async function confirmAndRun() {
  const excludeArt = [...document.querySelectorAll(".art")]
    .filter((c) => !c.checked).map((c) => c.dataset.id);
  const includeArt = [...document.querySelectorAll(".art-add")]
    .filter((c) => c.checked).map((c) => c.dataset.id);

  const btn = $("btn-confirm");
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span> auditing...';
  try {
    await api("POST", `/api/engagements/${state.engagementId}/confirm`, {
      confirm: true, exclude_artifact_ids: excludeArt, include_artifact_ids: includeArt,
    });
    const run = await api("POST", `/api/engagements/${state.engagementId}/run`, {});
    const report = await api("GET", `/api/engagements/${state.engagementId}/report`);
    markDone("card-scope");
    renderResult(run, report);
    enableCard("card-result");
    $("card-result").scrollIntoView({ behavior: "smooth" });
  } catch (e) {
    showMsg("scope-msg", escapeHtml(e.message), "err", e.nextStep);
    $("scope-msg").className = "msg show err";
  } finally {
    btn.disabled = false;
    btn.textContent = "Confirm scope & run audit";
  }
}

// -- Step 4: result ----------------------------------------------------------

function bandOf(pct) {
  if (pct <= 40) return "Critical";
  if (pct <= 60) return "High";
  if (pct <= 75) return "Medium";
  if (pct <= 90) return "Good";
  return "Excellent";
}
function barColor(pct) {
  return { Critical: "#f85149", High: "#db6d28", Medium: "#d29922", Good: "#3fb950", Excellent: "#58a6ff" }[bandOf(pct)];
}
function bars(obj) {
  return Object.entries(obj || {}).sort((a, b) => a[1] - b[1]).map(([name, pct]) => `
    <div class="bar-row"><div class="name" title="${escapeHtml(name)}">${escapeHtml(name)}</div>
      <div class="bar"><span style="width:${pct}%;background:${barColor(pct)}"></span></div>
      <div class="pct">${pct}%</div></div>`).join("");
}

function renderResult(run, report) {
  const s = run.summary;
  const findings = (report.results || [])
    .filter((r) => r.status === "scored" && r.score !== null && r.score <= 1)
    .sort((a, b) => ({ critical: 0, high: 1, medium: 2, low: 3 }[a.severity] ?? 9)
                   - ({ critical: 0, high: 1, medium: 2, low: 3 }[b.severity] ?? 9));

  const findingHtml = findings.length ? findings.map((f) => `
    <div class="finding ${f.severity || "low"}">
      <div class="head">${escapeHtml(f.item_id)} &mdash; ${escapeHtml(f.title)}</div>
      <div class="meta">${(f.severity || "medium").toUpperCase()} &middot; score ${f.score}/3
        &middot; Area ${f.area} &middot; ${escapeHtml(f.pillar)}</div>
      <div>${escapeHtml(f.detail)}</div>
      ${f.remediation ? `<div class="fix"><b>Fix:</b> ${escapeHtml(f.remediation)}</div>` : ""}
    </div>`).join("")
    : '<div class="banner">No check scored 0 or 1. Nothing qualifies as a finding.</div>';

  const gaps = (report.results || []).filter((r) => r.status === "evidence_unavailable");
  const gapHtml = gaps.length ? `
    <details><summary>Coverage gaps — ${gaps.length} check(s) had no evidence</summary>
      <ul class="warnlist" style="color:var(--muted)">${gaps.map((g) =>
        `<li><code>${escapeHtml(g.item_id)}</code> ${escapeHtml(g.title)} — ${escapeHtml(g.detail)}</li>`).join("")}</ul>
    </details>` : "";

  $("result-body").innerHTML = `
    <div class="score-badge"><div class="num">${s.overall_score}%</div>
      <div class="band ${s.risk_band}">${s.risk_band}</div></div>
    <div class="muted" style="font-size:13px">${s.items_scored} scored &middot;
      ${s.items_not_applicable} N/A &middot; ${s.items_unavailable} no evidence &middot;
      ${s.findings} finding(s) &middot; ${s.artifacts_inspected} artifact(s) inspected</div>

    <h3 style="font-size:14px;margin:18px 0 4px">Pillars</h3><div class="bars">${bars(s.pillars)}</div>
    <h3 style="font-size:14px;margin:18px 0 4px">Areas</h3><div class="bars">${bars(
      Object.fromEntries(Object.entries(s.areas).map(([a, p]) => [`Area ${a}`, p])))}</div>

    <h3 style="font-size:14px;margin:22px 0 4px">Findings (${findings.length})</h3>
    ${findingHtml}
    ${gapHtml}

    <div class="row">
      <button class="secondary" id="btn-md">View full Markdown report</button>
      <button class="secondary" id="btn-log">Verify read-only audit log</button>
    </div>
    <div id="extra"></div>`;

  $("btn-md").addEventListener("click", () => {
    $("extra").innerHTML = `<details open><summary>Report Markdown${
      report.saved_to ? ` (saved to <code>${escapeHtml(report.saved_to)}</code>)` : ""}</summary>
      <pre style="white-space:pre-wrap;font-size:12px;background:var(--panel-2);padding:14px;border-radius:8px;overflow:auto">${
        escapeHtml(report.report_markdown)}</pre></details>`;
  });
  $("btn-log").addEventListener("click", showAuditLog);
}

async function showAuditLog() {
  const log = await api("GET", `/api/engagements/${state.engagementId}/audit-log?limit=200`);
  const rows = (log.entries || []).map((e) => `<tr><td>${e.seq}</td><td><code>${escapeHtml(e.method)}</code></td>
    <td class="muted">${escapeHtml(e.url || "")}</td><td>${escapeHtml(e.decision || "")}</td></tr>`).join("");
  $("extra").innerHTML = `
    <div class="banner ${log.chain_intact ? "" : "bad"}">${log.chain_intact ? "✓" : "✗"}
      ${escapeHtml(log.chain_status)}</div>
    <table><thead><tr><th>#</th><th>Method</th><th>URL</th><th>Decision</th></tr></thead>
      <tbody>${rows || '<tr><td colspan="4" class="muted">No calls logged.</td></tr>'}</tbody></table>
    <p class="muted" style="font-size:12px">Every call is a GET, or a POST to <code>/getDefinition</code> — reads only.</p>`;
}

// -- wire up -----------------------------------------------------------------

$("btn-signin").addEventListener("click", beginSignIn);
$("btn-start").addEventListener("click", startEngagement);
refreshStatus();
