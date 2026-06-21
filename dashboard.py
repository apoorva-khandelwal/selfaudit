"""
Live dashboard server for SelfAudit.
Serves the UI and a Server-Sent Events stream. Also exposes a REST API
for the developer to interact with agents from the browser.
"""

import json
import time
import threading
from flask import Flask, Response, render_template_string, request, jsonify
from models import MODELS, get_cheaper_alternatives, format_tradeoffs


def _flag_rec(state):
    from sdk import _build_recommendation
    # pick situation from the watcher note
    notes = [n for n in state.notes if "[watcher]" in n]
    last = notes[-1] if notes else ""
    if "cost/progress ratio" in last:
        situation = "high_cost_ratio"
    else:
        situation = "stuck_subtask"
    return _build_recommendation(
        state.agent_id, state.model,
        state.cumulative_cost, state.max_retry_count,
        state.progress_score, situation,
    )

app = Flask(__name__)
_watcher = None
_undo_stack = []  # list of (label, callable)

HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>SelfAudit</title>
  <style>
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
    :root {
      --bg: #0a0a0a; --surface: #141414; --surface2: #1a1a1a;
      --border: #222; --text: #e0e0e0; --muted: #555;
      --green: #30d158; --red: #ff3b30; --orange: #ff9f0a;
      --blue: #0a84ff; --purple: #bf5af2;
    }
    body { background: var(--bg); color: var(--text); font-family: 'SF Mono','Menlo','Consolas',monospace; font-size: 13px; }

    /* header */
    .header {
      position: sticky; top: 0; z-index: 20;
      background: rgba(10,10,10,0.95); backdrop-filter: blur(12px);
      border-bottom: 1px solid var(--border);
      padding: 12px 24px; display: flex; align-items: center; gap: 28px;
    }
    .logo { font-size: 15px; font-weight: 700; color: #fff; letter-spacing:.05em; }
    .logo span { color: var(--green); }
    .stats { display: flex; gap: 20px; flex: 1; }
    .stat label { display:block; font-size:9px; letter-spacing:.1em; text-transform:uppercase; color:var(--muted); margin-bottom:2px; }
    .stat .val { font-size:14px; font-weight:600; }
    .stat .val.danger { color: var(--red); }
    .stat .val.good   { color: var(--green); }
    .live { width:7px; height:7px; border-radius:50%; background:var(--green); animation:blink 1.4s infinite; margin-left:auto; }
    .live.off { background:var(--muted); animation:none; }
    @keyframes blink { 0%,100%{opacity:1} 50%{opacity:.2} }

    /* settings panel */
    .settings-toggle, .undo-btn {
      background: transparent; border: 1px solid var(--border); color: var(--muted);
      font-family: inherit; font-size: 10px; padding: 3px 10px; border-radius: 4px;
      cursor: pointer; letter-spacing:.06em;
    }
    .settings-toggle:hover, .undo-btn:hover { color: var(--text); border-color: var(--muted); }
    .undo-btn:disabled { opacity: 0.3; cursor: default; }
    .undo-btn:disabled:hover { color: var(--muted); border-color: var(--border); }
    .settings-panel {
      display: none; background: var(--surface); border-bottom: 1px solid var(--border);
      padding: 12px 24px; gap: 20px; flex-wrap: wrap; align-items: center;
    }
    .settings-panel.open { display: flex; }
    .settings-panel span { font-size:10px; color:var(--muted); letter-spacing:.08em; text-transform:uppercase; }
    .threshold-group { display:flex; align-items:center; gap:6px; }
    .threshold-group label { font-size:10px; color:var(--muted); }
    .threshold-group input {
      background: var(--surface2); border: 1px solid var(--border); color: var(--text);
      font-family: inherit; font-size: 11px; padding: 3px 6px; border-radius: 4px; width: 60px;
    }
    .btn-apply {
      background: var(--blue); color: #fff; border: none; border-radius: 4px;
      font-family: inherit; font-size: 10px; font-weight:600; padding: 4px 10px;
      cursor: pointer; letter-spacing:.06em;
    }
    .btn-apply:hover { opacity:.85; }

    /* main */
    .main { padding: 24px; }
    .section-title { font-size:10px; letter-spacing:.12em; text-transform:uppercase; color:var(--muted); margin-bottom:12px; }

    /* grid */
    .grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(300px,1fr)); gap:14px; margin-bottom:36px; }

    .card {
      background: var(--surface); border: 1px solid var(--border);
      border-radius: 10px; padding: 16px;
      transition: border-color .25s, box-shadow .25s;
    }
    .card.alerted  { border-color:var(--red);    box-shadow:0 0 20px rgba(255,59,48,.1); }
    .card.done     { border-color:#1e3a26; }
    .card.paused   { border-color:var(--blue);   box-shadow:0 0 20px rgba(10,132,255,.08); opacity:.7; }
    .card.flagged  { border-color:var(--orange); box-shadow:0 0 20px rgba(255,159,10,.1); }

    .card-top { display:flex; justify-content:space-between; align-items:center; margin-bottom:12px; }
    .agent-name { font-size:13px; font-weight:600; color:#fff; display:flex; align-items:center; gap:6px; }
    .pulse { width:6px; height:6px; border-radius:50%; background:var(--green); animation:blink 1.4s infinite; }
    .pulse.red    { background:var(--red); }
    .pulse.orange { background:var(--orange); }
    .pulse.blue   { background:var(--blue); animation:none; }
    .pulse.off    { background:#2a2a2a; animation:none; }

    .badge { font-size:9px; font-weight:700; letter-spacing:.1em; padding:3px 7px; border-radius:4px; text-transform:uppercase; }
    .badge.running { background:#0d2b18; color:var(--green); }
    .badge.alerted { background:#2b0d0d; color:var(--red); }
    .badge.done    { background:#0d2b18; color:#34c759; }
    .badge.paused  { background:#0a1f2e; color:var(--blue); }
    .badge.flagged { background:#2b1d0d; color:var(--orange); }

    .metrics { display:grid; grid-template-columns:1fr 1fr 1fr 1fr; gap:8px; margin-bottom:10px; }
    .metric label { display:block; font-size:9px; color:var(--muted); letter-spacing:.08em; text-transform:uppercase; margin-bottom:2px; }
    .metric .val { font-size:15px; font-weight:600; }
    .metric .val.danger { color:var(--red); }
    .metric .val.good   { color:var(--green); }
    .metric .val.warn   { color:var(--orange); }

    .bar-wrap { background:#1a1a1a; border-radius:3px; height:3px; margin-bottom:10px; overflow:hidden; }
    .bar { height:100%; border-radius:3px; background:var(--green); transition:width .4s ease; }
    .bar.danger { background:var(--red); }
    .bar.warn   { background:var(--orange); }
    .bar.blue   { background:var(--blue); }

    .actions { display:flex; flex-direction:column; gap:3px; margin-bottom:10px; }
    .action-row { display:flex; align-items:center; gap:6px; font-size:11px; color:var(--muted); }
    .action-row.success { color:#3a7a50; }
    .action-row.fail    { color:#7a3030; }

    /* controls */
    .controls { display:flex; gap:6px; flex-wrap:wrap; margin-top:10px; padding-top:10px; border-top:1px solid var(--border); }
    .btn {
      font-family:inherit; font-size:10px; font-weight:600; letter-spacing:.06em;
      padding:4px 10px; border-radius:4px; border:1px solid; cursor:pointer;
      background:transparent; transition:background .15s;
    }
    .btn-pause  { border-color:#1a3a5a; color:var(--blue); }
    .btn-pause:hover  { background:#0a1f2e; }
    .btn-resume { border-color:#1a3a5a; color:var(--blue); }
    .btn-resume:hover { background:#0a1f2e; }
    .btn-flag   { border-color:#3a2a0a; color:var(--orange); }
    .btn-flag:hover   { background:#2b1d0d; }
    .btn-unflag { border-color:#3a2a0a; color:var(--orange); }
    .btn-unflag:hover { background:#2b1d0d; }
    .btn-note   { border-color:var(--border); color:var(--muted); }
    .btn-note:hover   { background:var(--surface2); color:var(--text); }

    /* note input */
    .note-row { display:none; margin-top:8px; gap:6px; }
    .note-row.open { display:flex; }
    .note-input {
      flex:1; background:var(--surface2); border:1px solid var(--border);
      color:var(--text); font-family:inherit; font-size:11px;
      padding:4px 8px; border-radius:4px;
    }
    .btn-send { background:var(--blue); color:#fff; border:none; border-radius:4px; padding:4px 10px; font-family:inherit; font-size:10px; cursor:pointer; }

    /* notes list */
    .notes-list { margin-top:8px; display:flex; flex-direction:column; gap:3px; }
    .note-item { font-size:10px; color:var(--muted); padding:3px 0; border-bottom:1px solid #1a1a1a; display:flex; align-items:center; gap:6px; }
    .note-text { flex:1; }
    .note-edit-input { flex:1; background:var(--surface2); border:1px solid var(--border); color:var(--text); font-family:inherit; font-size:10px; padding:2px 6px; border-radius:3px; }
    .note-btn { background:none; border:none; cursor:pointer; font-size:9px; color:var(--muted); padding:0 2px; }
    .note-btn:hover { color:var(--text); }

    .callout { margin-top:8px; padding:7px 10px; border-radius:4px; font-size:11px; line-height:1.5; border-left:3px solid; }
    .callout.red    { border-color:var(--red);    background:rgba(255,59,48,.07);  color:#ff7a72; }
    .callout.orange { border-color:var(--orange); background:rgba(255,159,10,.07); color:#ffc050; }
    .callout.blue   { border-color:var(--blue);   background:rgba(10,132,255,.07); color:#5ab0ff; }

    /* alert log */
    /* review queue */
    .review-queue { border-top:1px solid var(--border); padding-top:24px; margin-bottom:32px; }
    .review-entry {
      background:var(--surface); border:1px solid #3a2a0a; border-left:3px solid var(--orange);
      border-radius:6px; padding:12px 14px; margin-bottom:8px;
    }
    .review-meta { display:flex; justify-content:space-between; align-items:center; margin-bottom:6px; }
    .review-agent { font-weight:700; color:var(--orange); font-size:12px; }
    .review-time  { font-size:10px; color:var(--muted); }
    .review-reason { color:#ccc; font-size:12px; margin-bottom:8px; }
    .review-actions { display:flex; gap:8px; }
    .btn-escalate {
      background:transparent; border:1px solid #5a1a1a; color:var(--red);
      font-family:inherit; font-size:10px; font-weight:600; padding:4px 10px;
      border-radius:4px; cursor:pointer; letter-spacing:.06em;
    }
    .btn-escalate:hover { background:#2b0d0d; }
    .btn-clear {
      background:transparent; border:1px solid var(--border); color:var(--muted);
      font-family:inherit; font-size:10px; padding:4px 10px;
      border-radius:4px; cursor:pointer;
    }
    .btn-clear:hover { color:var(--text); border-color:var(--muted); }

    /* alert log */
    .alert-log { border-top:1px solid var(--border); padding-top:24px; }
    .alert-entry {
      background:var(--surface); border:1px solid #2b0d0d; border-left:3px solid var(--red);
      border-radius:6px; padding:12px 14px; margin-bottom:8px;
    }
    .alert-entry.dismissed { opacity:.35; }
    .alert-meta { display:flex; justify-content:space-between; align-items:center; margin-bottom:6px; }
    .alert-agent { font-weight:700; color:var(--red); font-size:12px; }
    .alert-time  { font-size:10px; color:var(--muted); }
    .alert-reason { color:#ccc; margin-bottom:8px; font-size:12px; }
    .alert-actions { display:flex; gap:6px; align-items:center; margin-top:8px; }
    .btn-rec {
      background:transparent; border:1px solid #3a2a0a; color:var(--orange);
      font-family:inherit; font-size:10px; font-weight:600; padding:4px 10px;
      border-radius:4px; cursor:pointer; letter-spacing:.04em;
    }
    .btn-rec:hover { background:#2b1d0d; }
    .btn-rec.open  { background:#2b1d0d; }
    .btn-dismiss-sm {
      background:transparent; border:1px solid var(--border); color:var(--muted);
      font-family:inherit; font-size:10px; padding:4px 10px; border-radius:4px; cursor:pointer;
    }
    .btn-dismiss-sm:hover { color:var(--text); border-color:var(--muted); }
    .btn-delete-sm {
      background:transparent; border:1px solid #3a1a1a; color:var(--red);
      font-family:inherit; font-size:10px; padding:4px 10px; border-radius:4px; cursor:pointer;
    }
    .btn-delete-sm:hover { background:#2b0d0d; }
    .rec-panel { display:none; margin-top:10px; padding:10px 12px; background:var(--surface2); border-radius:6px; border:1px solid var(--border); }
    .rec-panel.open { display:block; }
    .alert-rec { color:var(--muted); font-size:11px; line-height:1.6; }
    .empty-log { color:var(--muted); font-size:12px; padding:12px 0; }
  </style>
</head>
<body>

<div class="header">
  <div class="logo">Self<span>Audit</span></div>
  <div class="stats">
    <div class="stat"><label>total cost</label><div class="val" id="h-cost">$0.0000</div></div>
    <div class="stat"><label>alerts</label><div class="val danger" id="h-alerts">0</div></div>
    <div class="stat"><label>done</label><div class="val good" id="h-done">0</div></div>
    <div class="stat"><label>running</label><div class="val" id="h-running">0</div></div>
    <div class="stat"><label>paused</label><div class="val" id="h-paused" style="color:var(--blue)">0</div></div>
    <div class="stat"><label>est. saved</label><div class="val good" id="h-saved">$0.00</div></div>
  </div>
  <button class="undo-btn" id="undo-btn" onclick="undo()" disabled title="">↩ Undo</button>
  <button class="settings-toggle" onclick="document.getElementById('settings-panel').classList.toggle('open')">⚙ Settings</button>
  <div class="live" id="live-dot"></div>
</div>

<div class="settings-panel" id="settings-panel">
  <span>global defaults</span>
  <div class="threshold-group">
    <label>retries</label>
    <input type="number" id="t-retry" value="3" min="1">
  </div>
  <div class="threshold-group">
    <label>cost ($)</label>
    <input type="number" id="t-cost" value="0.05" min="0.001" step="0.01">
  </div>
  <div class="threshold-group">
    <label>time (s)</label>
    <input type="number" id="t-time" value="30" min="5">
  </div>
  <button class="btn-apply" onclick="applyThresholds()">Apply</button>
  <div style="width:1px;background:var(--border);margin:0 4px;height:20px"></div>
  <span>per-agent budget</span>
  <div class="threshold-group">
    <input type="text" id="b-agent" placeholder="agent-id" style="width:120px">
  </div>
  <div class="threshold-group">
    <label>cap ($)</label>
    <input type="number" id="b-cap" value="0.10" min="0.01" step="0.01">
  </div>
  <button class="btn-apply" style="background:var(--orange)" onclick="applyBudget()">Set</button>
  <span id="budget-msg" style="font-size:10px;transition:opacity .5s"></span>
</div>

<div class="main">
  <div class="section-title">agents</div>
  <div class="grid" id="grid">
    <div class="card"><div class="card-top"><span class="agent-name">waiting for agents...</span></div></div>
  </div>
  <div class="review-queue">
    <div class="section-title">flagged for review</div>
    <div id="review-queue"><div class="empty-log">no flags yet — watcher will flag agents in the ambiguous zone</div></div>
  </div>

  <div class="alert-log">
    <div class="section-title">alert log</div>
    <div id="alert-log"><div class="empty-log">no alerts yet</div></div>
  </div>
</div>

<script>
  const source = new EventSource('/stream');

  source.onmessage = (e) => {
    const data = JSON.parse(e.data);
    updateHeader(data);
    renderGrid(data.agents);
    renderReviewQueue(data.flagged);
    renderAlertLog(data.alerts);
    document.getElementById('live-dot').className = 'live';
  };
  source.onerror = () => { document.getElementById('live-dot').className = 'live off'; };

  function updateHeader(d) {
    document.getElementById('h-cost').textContent    = '$' + d.total_cost;
    document.getElementById('h-alerts').textContent  = d.alert_count;
    document.getElementById('h-done').textContent    = d.done_count;
    document.getElementById('h-running').textContent = d.running_count;
    document.getElementById('h-paused').textContent  = d.paused_count;
    document.getElementById('h-saved').textContent   = '$' + d.cost_saved;
  }

  function renderGrid(agents) {
    const grid = document.getElementById('grid');
    // preserve existing note inputs so they don't reset while typing
    const noteStates = {};
    grid.querySelectorAll('.card').forEach(c => {
      const id = c.dataset.agentId;
      if (id) noteStates[id] = c.querySelector('.note-input')?.value || '';
    });
    grid.innerHTML = '';
    agents.forEach(a => {
      const card = makeCard(a);
      if (noteStates[a.agent_id]) {
        const ni = card.querySelector('.note-input');
        if (ni) ni.value = noteStates[a.agent_id];
      }
      grid.appendChild(card);
    });
  }

  function makeCard(a) {
    const card = document.createElement('div');
    const cls = a.paused ? 'paused' : a.status === 'ALERTED' ? 'alerted' : a.flagged ? 'flagged' : a.status === 'DONE' ? 'done' : '';
    card.className = 'card ' + cls;
    card.dataset.agentId = a.agent_id;

    const pulse = a.paused ? 'blue' : a.status === 'ALERTED' ? 'red' : a.flagged ? 'orange' : a.status === 'DONE' ? 'off' : '';
    const badge = a.paused ? 'paused' : a.status === 'ALERTED' ? 'alerted' : a.flagged ? 'flagged' : a.status === 'DONE' ? 'done' : 'running';
    const label = a.paused ? 'PAUSED' : a.flagged && a.status !== 'ALERTED' ? 'FLAGGED' : a.status;

    const costPct  = Math.min(100, (parseFloat(a.cost)/0.05)*100);
    const barCls   = a.status==='ALERTED' ? 'danger' : a.flagged ? 'warn' : a.paused ? 'blue' : '';
    const costCls  = a.status==='ALERTED' ? ' danger' : '';
    const progCls  = a.progress > 0 ? ' good' : '';
    const retryCls = a.retries >= 3 ? ' danger' : a.retries >= 2 ? ' warn' : '';

    // deduplicate repeated actions — show "call_api ×5" instead of 5 rows
    const actionMap = [];
    (a.recent_actions||[]).forEach(r => {
      const last = actionMap[actionMap.length - 1];
      if (last && last.action === r.action && last.success === r.success) {
        last.count++; last.cost = (parseFloat(last.cost) + parseFloat(r.cost)).toFixed(4);
      } else {
        actionMap.push({...r, count: 1, cost: r.cost});
      }
    });
    const actionsHTML = actionMap.map(r =>
      `<div class="action-row ${r.success?'success':'fail'}">
        <span>${r.success?'✓':'✗'}</span>
        <span>${r.action}${r.count>1?` <span style="color:var(--muted)">×${r.count}</span>`:''}</span>
        <span style="margin-left:auto;font-size:10px">$${r.cost}</span>
      </div>`).join('') || '<div class="action-row">no actions yet</div>';

    // only show user-added notes in the card (watcher notes are in the review queue)
    const userNotes = (a.notes||[]).filter(n => !n.text.includes('[watcher]'));
    const notesHTML = userNotes.map(n =>
      `<div class="note-item" id="note-item-${a.agent_id.replace(/[^a-z0-9]/gi,'_')}-${n.i}">
        <span class="note-text" style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1">${n.text}</span>
        <button class="note-btn" title="Edit" onclick="startEditNote('${a.agent_id}',${n.i},this)">✎</button>
        <button class="note-btn" title="Delete" onclick="deleteNote('${a.agent_id}',${n.i})">✕</button>
      </div>`).join('');

    const modelTag     = a.model ? `<div style="font-size:9px;color:var(--muted);margin-top:2px">${a.model}</div>` : '';
    const alertCallout = a.alert_reason ? `<div class="callout red">${a.alert_reason}</div>` : '';
    const flagCallout  = a.flagged && !a.alert_reason ? `<div class="callout orange">⚠ flagged for human review</div>` : '';
    const pauseCallout = a.paused ? `<div class="callout blue">⏸ paused</div>` : '';
    const projLine     = (!a.paused && !a.completed && parseFloat(a.proj_1h) > 0.01)
      ? `<div style="font-size:10px;color:#555;margin-top:6px">at current rate: <span style="color:#888">$${a.proj_1h}/hr</span>${a.budget?` · cap <span style="color:#aaa">$${a.budget}</span>`:''}</div>` : '';

    const pauseBtn = a.paused
      ? `<button class="btn btn-resume" onclick="api('resume','${a.agent_id}')">▶ Resume</button>`
      : `<button class="btn btn-pause"  onclick="api('pause','${a.agent_id}')">⏸ Pause</button>`;

    card.innerHTML = `
      <div class="card-top">
        <div><span class="agent-name"><span class="pulse ${pulse}"></span>${a.agent_id}</span>${modelTag}</div>
        <span class="badge ${badge}">${label}</span>
      </div>
      <div class="metrics">
        <div class="metric"><label>cost</label><div class="val${costCls}">$${a.cost}</div></div>
        <div class="metric"><label>steps</label><div class="val${progCls}">${a.progress}</div></div>
        <div class="metric"><label>retries</label><div class="val${retryCls}">${a.retries}</div></div>
        <div class="metric"><label>elapsed</label><div class="val">${a.elapsed}s</div></div>
      </div>
      <div class="bar-wrap"><div class="bar ${barCls}" style="width:${costPct}%"></div></div>
      <div class="actions">${actionsHTML}</div>
      ${alertCallout}${flagCallout}${pauseCallout}${projLine}
      ${notesHTML ? `<div class="notes-list" style="margin-top:8px">${notesHTML}</div>` : ''}
      <div class="controls">
        ${pauseBtn}
        <button class="btn btn-note" onclick="toggleNote('${a.agent_id}', this)">✎ Note</button>
        <button class="btn btn-note" onclick="toggleThresh('${a.agent_id}')">⚙</button>
      </div>
      <div class="note-row" id="note-${a.agent_id.replace(/[^a-z0-9]/gi,'_')}">
        <input class="note-input" type="text" placeholder="add a note..." onkeydown="if(event.key==='Enter')sendNote('${a.agent_id}',this)">
        <button class="btn-send" onclick="sendNote('${a.agent_id}',this.previousElementSibling)">Send</button>
      </div>
      <div class="note-row" id="thresh-${a.agent_id.replace(/[^a-z0-9]/gi,'_')}" style="flex-direction:column;gap:6px">
        <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
          <span style="font-size:10px;color:var(--muted)">retries</span>
          <input class="note-input" type="number" id="tr-r-${a.agent_id.replace(/[^a-z0-9]/gi,'_')}" placeholder="global" value="${a.t_retry ?? ''}" style="width:60px">
          <span style="font-size:10px;color:var(--muted)">cost ($)</span>
          <input class="note-input" type="number" id="tr-c-${a.agent_id.replace(/[^a-z0-9]/gi,'_')}" placeholder="global" value="${a.t_cost ?? ''}" style="width:60px" step="0.01">
          <span style="font-size:10px;color:var(--muted)">time (s)</span>
          <input class="note-input" type="number" id="tr-t-${a.agent_id.replace(/[^a-z0-9]/gi,'_')}" placeholder="global" value="${a.t_time ?? ''}" style="width:60px">
          <button class="btn-send" onclick="applyAgentThresh('${a.agent_id}')">Apply</button>
        </div>
        <span style="font-size:9px;color:var(--muted)">leave blank to use global defaults</span>
      </div>
    `;
    return card;
  }

  function renderAlertLog(alerts) {
    const log = document.getElementById('alert-log');
    if (!alerts||!alerts.length) { log.innerHTML='<div class="empty-log">no alerts yet</div>'; return; }
    log.innerHTML = [...alerts].reverse().map(a => `
      <div class="alert-entry ${a.dismissed?'dismissed':''}">
        <div class="alert-meta">
          <span class="alert-agent">⛔ ${a.agent_id}</span>
          <span class="alert-time">${a.time}</span>
        </div>
        <div class="alert-reason">${a.reason}</div>
        <div class="alert-actions">
          ${a.recommendation ? `<button class="btn-rec" id="rec-btn-${a.id}" onclick="toggleRec('${a.id}')">💡 Recommendation</button>` : ''}
          ${a.id && !a.dismissed ? `<button class="btn-dismiss-sm" onclick="dismissAlert('${a.id}')">Dismiss</button>` : ''}
          ${a.id ? `<button class="btn-delete-sm" onclick="deleteAlert('${a.id}')">Delete</button>` : ''}
        </div>
        ${a.recommendation ? `<div class="rec-panel" id="rec-${a.id}"><div class="alert-rec">${renderRec(a.recommendation)}</div></div>` : ''}
      </div>`).join('');
  }

  function toggleRec(alertId) {
    const panel = document.getElementById('rec-' + alertId);
    const btn   = document.getElementById('rec-btn-' + alertId);
    if (!panel) return;
    const open = panel.classList.toggle('open');
    btn.classList.toggle('open', open);
    btn.textContent = open ? '💡 Hide' : '💡 Recommendation';
  }

  function renderRec(rec) {
    if (!rec || typeof rec === 'string') return rec || '';
    let html = '';
    if (rec.headline) html += `<div style="color:#ccc;font-weight:600;margin-bottom:6px">${rec.headline}</div>`;
    if (rec.steps && rec.steps.length) {
      html += '<ol style="margin:0 0 10px 16px;padding:0;display:flex;flex-direction:column;gap:4px">';
      rec.steps.forEach(s => { html += `<li style="color:#aaa">${s}</li>`; });
      html += '</ol>';
    }
    if (rec.alternatives && rec.alternatives.length) {
      html += '<div style="font-size:10px;color:var(--muted);margin-bottom:4px;letter-spacing:.06em;text-transform:uppercase;margin-top:10px">cheaper alternatives</div>';
      html += '<div style="display:flex;flex-direction:column;gap:4px">';
      rec.alternatives.forEach(a => {
        html += `<div style="display:flex;align-items:baseline;gap:8px;font-size:11px">
          <span style="color:#ccc;font-weight:600;min-width:160px">${a.id}</span>
          <span style="color:var(--green)">$${a.input_cost_per_1m.toFixed(2)}/$1M in</span>
          <span style="color:var(--muted)">· $${a.output_cost_per_1m.toFixed(2)}/$1M out</span>
          <span style="color:var(--muted)">· ${a.context_window_k}K ctx</span>
          <span style="color:#555">— ${a.notes}</span>
        </div>`;
      });
      html += '</div>';
    }
    if (rec.past_alerts && rec.past_alerts.length) {
      html += '<div style="font-size:10px;color:var(--muted);margin:10px 0 4px;letter-spacing:.06em;text-transform:uppercase">similar past alerts</div>';
      html += '<div style="display:flex;flex-direction:column;gap:4px">';
      rec.past_alerts.forEach(p => {
        html += `<div style="font-size:11px;padding:5px 8px;background:rgba(255,255,255,.03);border-radius:4px;border-left:2px solid var(--border)">
          <span style="color:#aaa;font-weight:600">${p.agent_id}</span>
          <span style="color:var(--muted);margin:0 6px">·</span>
          <span style="color:#888">${p.reason}</span>
          <span style="color:var(--muted);margin:0 6px">·</span>
          <span style="color:#555">$${p.cost} · ${p.time}</span>
        </div>`;
      });
      html += '</div>';
    }
    return html;
  }

  function api(action, agentId) {
    fetch('/api/' + action, {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({agent_id: agentId})
    }).then(() => refreshUndoBtn());
  }

  function refreshUndoBtn() {
    fetch('/api/undo_state').then(r => r.json()).then(d => {
      const btn = document.getElementById('undo-btn');
      btn.disabled = !d.available;
      btn.title = d.label ? 'Undo: ' + d.label : '';
    });
  }

  function undo() {
    fetch('/api/undo', {method:'POST', headers:{'Content-Type':'application/json'}, body:'{}'})
      .then(r => r.json()).then(d => {
        if (d.ok) refreshUndoBtn();
      });
  }

  function toggleNote(agentId, btn) {
    const key = agentId.replace(/[^a-z0-9]/gi,'_');
    const row = document.getElementById('note-' + key);
    row.classList.toggle('open');
    if (row.classList.contains('open')) row.querySelector('input').focus();
  }

  function sendNote(agentId, input) {
    const note = input.value.trim();
    if (!note) return;
    fetch('/api/note', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({agent_id: agentId, note})
    }).then(() => refreshUndoBtn());
    input.value = '';
    const key = agentId.replace(/[^a-z0-9]/gi,'_');
    document.getElementById('note-' + key).classList.remove('open');
  }

  function dismissAlert(alertId) {
    fetch('/api/dismiss', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({alert_id: alertId})
    }).then(() => refreshUndoBtn());
  }

  function deleteAlert(alertId) {
    fetch('/api/delete_alert', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({alert_id: alertId})
    }).then(() => refreshUndoBtn());
  }

  function startEditNote(agentId, index, btn) {
    const key = agentId.replace(/[^a-z0-9]/gi,'_');
    const item = document.getElementById(`note-item-${key}-${index}`);
    const span = item.querySelector('.note-text');
    const current = span.textContent;
    item.innerHTML = `
      <input class="note-edit-input" value="${current.replace(/"/g,'&quot;')}" id="edit-input-${key}-${index}">
      <button class="note-btn" onclick="saveEditNote('${agentId}',${index})">✓</button>
      <button class="note-btn" onclick="cancelEditNote('${agentId}',${index},'${current.replace(/'/g,"\\'")}')">✕</button>
    `;
    const input = document.getElementById(`edit-input-${key}-${index}`);
    input.focus();
    input.addEventListener('keydown', e => { if(e.key==='Enter') saveEditNote(agentId,index); if(e.key==='Escape') cancelEditNote(agentId,index,current); });
  }

  function saveEditNote(agentId, index) {
    const key = agentId.replace(/[^a-z0-9]/gi,'_');
    const input = document.getElementById(`edit-input-${key}-${index}`);
    if (!input) return;
    fetch('/api/edit_note', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({agent_id: agentId, index, text: input.value})
    }).then(() => refreshUndoBtn());
  }

  function cancelEditNote(agentId, index, original) {
    const key = agentId.replace(/[^a-z0-9]/gi,'_');
    const item = document.getElementById(`note-item-${key}-${index}`);
    item.innerHTML = `
      <span class="note-text">${original}</span>
      <button class="note-btn" title="Edit" onclick="startEditNote('${agentId}',${index},this)">✎</button>
      <button class="note-btn" title="Delete" onclick="deleteNote('${agentId}',${index})">✕</button>
    `;
  }

  function deleteNote(agentId, index) {
    fetch('/api/delete_note', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({agent_id: agentId, index})
    }).then(() => refreshUndoBtn());
  }

  function renderReviewQueue(flags) {
    const el = document.getElementById('review-queue');
    if (!flags || !flags.length) {
      el.innerHTML = '<div class="empty-log">no flags yet — watcher will flag agents in the ambiguous zone</div>';
      return;
    }
    el.innerHTML = flags.map(f => `
      <div class="review-entry">
        <div class="review-meta">
          <span class="review-agent">⚑ ${f.agent_id}</span>
          <span class="review-time">${f.time}</span>
        </div>
        <div class="review-reason">${f.reason}</div>
        <div style="display:flex;gap:16px;margin:6px 0 8px;font-size:11px;color:var(--muted)">
          <span>cost <strong style="color:#ccc">$${f.cost}</strong></span>
          <span>steps <strong style="color:#ccc">${f.progress}</strong></span>
          <span>retries <strong style="color:#ccc">${f.retries}</strong></span>
          <span>proj/hr <strong style="color:#ccc">$${f.proj_1h}</strong></span>
        </div>
        <div class="review-actions">
          <button class="btn-escalate" onclick="escalate('${f.agent_id}')">⛔ Escalate to Alert</button>
          <button class="btn-clear"    onclick="clearFlag('${f.agent_id}')">✓ Looks fine — clear</button>
        </div>
      </div>`).join('');
  }

  function escalate(agentId) {
    fetch('/api/escalate', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({agent_id: agentId})
    });
  }

  function clearFlag(agentId) {
    fetch('/api/clear_flag', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({agent_id: agentId})
    });
  }

  function applyBudget() {
    const agent = document.getElementById('b-agent').value.trim();
    const cap   = parseFloat(document.getElementById('b-cap').value);
    if (!agent) { showBudgetMsg('Enter an agent ID', 'var(--orange)'); return; }
    fetch('/api/set_budget', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({agent_id: agent, budget_usd: cap})
    }).then(r => r.json()).then(d => {
      if (d.ok) {
        showBudgetMsg(`Budget $${cap.toFixed(2)} set for ${agent}`, 'var(--green)');
        document.getElementById('b-agent').value = '';
        refreshUndoBtn();
      } else {
        showBudgetMsg(d.error, 'var(--red)');
      }
    });
  }

  function showBudgetMsg(msg, color) {
    const el = document.getElementById('budget-msg');
    el.style.color = color;
    el.style.opacity = '1';
    el.textContent = msg;
    setTimeout(() => el.style.opacity = '0', 2500);
  }

  function toggleThresh(agentId) {
    const key = agentId.replace(/[^a-z0-9]/gi,'_');
    const row = document.getElementById('thresh-' + key);
    row.classList.toggle('open');
  }

  function applyAgentThresh(agentId) {
    const key = agentId.replace(/[^a-z0-9]/gi,'_');
    const r = document.getElementById('tr-r-' + key).value;
    const c = document.getElementById('tr-c-' + key).value;
    const t = document.getElementById('tr-t-' + key).value;
    const body = {agent_id: agentId};
    if (r) body.retry = parseInt(r);
    if (c) body.cost  = parseFloat(c);
    if (t) body.time  = parseFloat(t);
    fetch('/api/agent_thresholds', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify(body)
    }).then(() => refreshUndoBtn());
  }

  function applyThresholds() {
    fetch('/api/thresholds', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({
        retry: parseInt(document.getElementById('t-retry').value),
        cost:  parseFloat(document.getElementById('t-cost').value),
        time:  parseFloat(document.getElementById('t-time').value),
      })
    }).then(() => refreshUndoBtn());
  }
</script>
</body>
</html>
"""


def _snapshot(watcher):
    import datetime as dt
    agents = []
    all_alerts = []
    flagged_list = []
    total_cost = alert_count = done_count = running_count = paused_count = 0

    for agent_id, state in sorted(watcher.states.items()):
        status = "DONE" if state.completed else "ALERTED" if state.alerted else "RUNNING"
        if state.alerted:   alert_count += 1
        if state.completed: done_count += 1
        if state.paused:    paused_count += 1
        else:               running_count += 1
        total_cost += state.cumulative_cost

        matched = next((a for a in watcher.alerts if a.agent_id == agent_id and not a.dismissed), None)

        # collect watcher-generated flag notes for the review queue
        if state.flagged and not state.alerted:
            watcher_notes = [n for n in state.notes if "[watcher]" in n]
            reason = watcher_notes[-1].split("[watcher] ")[-1] if watcher_notes else "ambiguous signals detected"
            rec = ""  # recommendation shown only after escalation to alert
            flagged_list.append({
                "agent_id":   agent_id,
                "reason":     reason,
                "retries":    state.max_retry_count,
                "cost":       f"{state.cumulative_cost:.4f}",
                "progress":   state.progress_score,
                "proj_1h":    f"{state.projected_cost_1h:.4f}",
                "rec":        rec,
                "time":       dt.datetime.now().strftime("%H:%M:%S"),
            })

        agents.append({
            "agent_id":       agent_id,
            "status":         status,
            "cost":           f"{state.cumulative_cost:.4f}",
            "progress":       state.progress_score,
            "retries":        state.max_retry_count,
            "elapsed":        f"{state.elapsed:.1f}",
            "recent_actions": [{"action": e.action, "success": e.success, "cost": f"{e.cost_usd:.4f}"} for e in state.events[-4:]],
            "alerted":        state.alerted,
            "paused":         state.paused,
            "flagged":        state.flagged,
            "notes":          [{"i": i, "text": n} for i, n in enumerate(state.notes)],
            "alert_reason":   matched.reason if matched else None,
            "proj_1h":        f"{state.projected_cost_1h:.4f}",
            "budget":         f"{state.budget_usd:.2f}" if state.budget_usd else None,
            "model":          state.model,
            "progress_mode":  state.progress_mode,
            "t_retry":        state.retry_threshold,
            "t_cost":         state.cost_threshold,
            "t_time":         state.time_threshold,
        })

    import datetime
    for a in watcher.alerts:
        all_alerts.append({
            "id":             a.id,
            "agent_id":       a.agent_id,
            "reason":         a.reason,
            "recommendation": a.recommendation,
            "time":           datetime.datetime.fromtimestamp(a.timestamp).strftime("%H:%M:%S"),
            "dismissed":      a.dismissed,
            "type":           "watcher",
        })

    return {
        "agents":        agents,
        "alerts":        all_alerts,
        "flagged":       flagged_list,
        "total_cost":    f"{total_cost:.4f}",
        "alert_count":   alert_count,
        "done_count":    done_count,
        "running_count": running_count,
        "paused_count":  paused_count,
        "cost_saved":    f"{getattr(watcher, 'cost_saved_usd', 0.0):.2f}",
    }


@app.route("/")
def index():
    return render_template_string(HTML)


@app.route("/stream")
def stream():
    def generate():
        # send initial snapshot from local watcher or Redis
        if _watcher and _watcher.states:
            yield f"data: {json.dumps(_snapshot(_watcher))}\n\n"
        else:
            try:
                import redis_store as _rs
                snap = _rs.get_snapshot()
                if snap:
                    yield f"data: {json.dumps(snap)}\n\n"
            except Exception:
                pass

        if _watcher:
            # local mode: use threading.Event
            while True:
                changed = _watcher._dirty.wait(timeout=2.0)
                if changed and _watcher.states:
                    _watcher._dirty.clear()
                    yield f"data: {json.dumps(_snapshot(_watcher))}\n\n"
        else:
            # viewer mode (friend's machine): subscribe to Redis pub/sub
            try:
                import redis_store as _rs
                for _ in _rs.subscribe():
                    snap = _rs.get_snapshot()
                    if snap:
                        yield f"data: {json.dumps(snap)}\n\n"
            except Exception:
                # fall back to polling every 2s if pub/sub fails
                while True:
                    time.sleep(2)
                    try:
                        import redis_store as _rs
                        snap = _rs.get_snapshot()
                        if snap:
                            yield f"data: {json.dumps(snap)}\n\n"
                    except Exception:
                        pass

    return Response(generate(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def _push_undo(label, fn):
    _undo_stack.append((label, fn))
    if len(_undo_stack) > 20:
        _undo_stack.pop(0)

@app.route("/api/undo", methods=["POST"])
def api_undo():
    if not _undo_stack:
        return jsonify(ok=False, error="Nothing to undo"), 400
    label, fn = _undo_stack.pop()
    try:
        fn()
        return jsonify(ok=True, label=label, remaining=len(_undo_stack),
                       next_label=_undo_stack[-1][0] if _undo_stack else None)
    except Exception as e:
        return jsonify(ok=False, error=str(e)), 500

@app.route("/api/undo_state", methods=["GET"])
def api_undo_state():
    return jsonify(
        available=len(_undo_stack) > 0,
        label=_undo_stack[-1][0] if _undo_stack else None,
    )

@app.route("/api/debug", methods=["GET"])
def api_debug():
    if not _watcher:
        return jsonify(error="no watcher")
    return jsonify(
        alerts_count=len(_watcher.alerts),
        alerts=[{"id": a.id, "agent_id": a.agent_id, "reason": a.reason, "dismissed": a.dismissed} for a in _watcher.alerts],
        alerted_states=[k for k, v in _watcher.states.items() if v.alerted],
    )

@app.route("/api/pause", methods=["POST"])
def api_pause():
    aid = request.json["agent_id"]
    _push_undo(f"pause {aid}", lambda: _watcher.resume(aid))
    _watcher.pause(aid)
    return jsonify(ok=True)

@app.route("/api/resume", methods=["POST"])
def api_resume():
    aid = request.json["agent_id"]
    _push_undo(f"resume {aid}", lambda: _watcher.pause(aid))
    _watcher.resume(aid)
    return jsonify(ok=True)

@app.route("/api/note", methods=["POST"])
def api_note():
    aid = request.json["agent_id"]
    _watcher.add_note(aid, request.json["note"])
    idx = len(_watcher.states[aid].notes) - 1
    _push_undo(f"add note on {aid}", lambda i=idx: _watcher.delete_note(aid, i))
    return jsonify(ok=True)

@app.route("/api/dismiss", methods=["POST"])
def api_dismiss():
    alert_id = request.json["alert_id"]
    _push_undo(f"dismiss alert", lambda: _watcher.undismiss_alert(alert_id))
    _watcher.dismiss_alert(alert_id)
    return jsonify(ok=True)

@app.route("/api/delete_alert", methods=["POST"])
def api_delete_alert():
    alert_id = request.json["alert_id"]
    alert = next((a for a in _watcher.alerts if a.id == alert_id), None)
    _watcher.delete_alert(alert_id)
    if alert:
        _push_undo(f"delete alert on {alert.agent_id}", lambda a=alert: _watcher.restore_alert(a))
    return jsonify(ok=True)

@app.route("/api/edit_note", methods=["POST"])
def api_edit_note():
    d = request.json
    aid, idx = d["agent_id"], d["index"]
    old_text = _watcher.states[aid].notes[idx] if aid in _watcher.states else None
    ok = _watcher.edit_note(aid, idx, d["text"])
    if ok and old_text is not None:
        _push_undo(f"edit note on {aid}", lambda t=old_text: _watcher.edit_note(aid, idx, t))
    return jsonify(ok=ok)

@app.route("/api/delete_note", methods=["POST"])
def api_delete_note():
    d = request.json
    aid, idx = d["agent_id"], d["index"]
    old_text = _watcher.states[aid].notes[idx] if aid in _watcher.states else None
    ok = _watcher.delete_note(aid, idx)
    if ok and old_text is not None:
        _push_undo(f"delete note on {aid}", lambda t=old_text: _watcher.add_note(aid, t))
    return jsonify(ok=ok)

@app.route("/api/set_budget", methods=["POST"])
def api_set_budget():
    d = request.json
    aid = d["agent_id"]
    if aid not in _watcher.states:
        return jsonify(ok=False, error=f"Agent '{aid}' not found"), 404
    old = _watcher.states[aid].budget_usd
    ok = _watcher.set_budget(aid, d["budget_usd"])
    if ok:
        _push_undo(f"set budget on {aid}", lambda b=old: _watcher.set_budget(aid, b))
    return jsonify(ok=ok)

@app.route("/api/escalate", methods=["POST"])
def api_escalate():
    aid = request.json["agent_id"]
    _watcher.escalate_flag(aid)
    return jsonify(ok=True)

@app.route("/api/clear_flag", methods=["POST"])
def api_clear_flag():
    _watcher.unflag(agent_id=request.json["agent_id"])
    return jsonify(ok=True)

@app.route("/api/thresholds", methods=["POST"])
def api_thresholds():
    d = request.json
    old = (_watcher.retry_threshold, _watcher.cost_threshold, _watcher.time_threshold)
    _watcher.set_thresholds(retry=d.get("retry"), cost=d.get("cost"), time=d.get("time"))
    _push_undo("set global thresholds",
               lambda o=old: _watcher.set_thresholds(retry=o[0], cost=o[1], time=o[2]))
    return jsonify(ok=True)

@app.route("/api/agent_thresholds", methods=["POST"])
def api_agent_thresholds():
    d = request.json
    aid = d["agent_id"]
    if aid not in _watcher.states:
        return jsonify(ok=False, error=f"Agent '{aid}' not found"), 404
    s = _watcher.states[aid]
    old = (s.retry_threshold, s.cost_threshold, s.time_threshold)
    ok = _watcher.set_agent_thresholds(aid, retry=d.get("retry"), cost=d.get("cost"), time=d.get("time"))
    if ok:
        _push_undo(f"set thresholds on {aid}",
                   lambda o=old: _watcher.set_agent_thresholds(aid, retry=o[0], cost=o[1], time=o[2]))
    return jsonify(ok=ok)


def start(watcher, port=5050):
    global _watcher
    _watcher = watcher
    threading.Thread(
        target=lambda: app.run(port=port, debug=False, use_reloader=False),
        daemon=True,
    ).start()
    print(f"  Dashboard → http://localhost:{port}\n")


def start_viewer(port=5050):
    """Start dashboard in viewer-only mode — reads live data from Redis (no local watcher needed)."""
    global _watcher
    _watcher = None
    app.run(port=port, debug=False, use_reloader=False)


if __name__ == "__main__":
    import sys
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 5050
    print(f"SelfAudit viewer — connecting to Redis for live data...")
    print(f"Dashboard → http://localhost:{port}")
    start_viewer(port=port)
