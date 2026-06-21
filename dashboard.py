"""
Live dashboard server for SelfAudit.
Serves the UI and a Server-Sent Events stream. Also exposes a REST API
for the developer to interact with agents from the browser.
"""

import json
import time
import threading
from flask import Flask, Response, render_template_string, request, jsonify

app = Flask(__name__)
_watcher = None

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

    /* thresholds bar */
    .thresholds {
      background: var(--surface); border-bottom: 1px solid var(--border);
      padding: 10px 24px; display: flex; align-items: center; gap: 20px; flex-wrap: wrap;
    }
    .thresholds span { font-size:10px; color:var(--muted); letter-spacing:.08em; text-transform:uppercase; }
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
    .note-item { font-size:10px; color:var(--muted); padding:3px 0; border-bottom:1px solid #1a1a1a; }

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
      border-radius:6px; padding:12px 14px; margin-bottom:8px; position:relative;
    }
    .alert-entry.peer   { border-color:#2b1d0d; border-left-color:var(--orange); }
    .alert-entry.dismissed { opacity:.35; }
    .alert-meta { display:flex; justify-content:space-between; align-items:center; margin-bottom:4px; }
    .alert-agent { font-weight:700; color:var(--red); font-size:12px; }
    .alert-entry.peer .alert-agent { color:var(--orange); }
    .alert-time  { font-size:10px; color:var(--muted); }
    .alert-reason { color:#ccc; margin-bottom:4px; font-size:12px; }
    .alert-rec    { color:var(--muted); font-size:11px; line-height:1.6; }
    .btn-dismiss {
      position:absolute; top:10px; right:12px;
      background:transparent; border:1px solid var(--border); color:var(--muted);
      font-family:inherit; font-size:9px; padding:2px 7px; border-radius:3px; cursor:pointer;
    }
    .btn-dismiss:hover { color:var(--text); border-color:var(--muted); }
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
  <div class="live" id="live-dot"></div>
</div>

<div class="thresholds">
  <span>thresholds</span>
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
  <div style="width:1px;background:var(--border);margin:0 8px;height:20px"></div>
  <span style="font-size:10px;color:var(--muted);letter-spacing:.08em;text-transform:uppercase">per-agent budget</span>
  <div class="threshold-group">
    <input type="text" id="b-agent" placeholder="agent-id" style="width:120px">
  </div>
  <div class="threshold-group">
    <label>cap ($)</label>
    <input type="number" id="b-cap" value="0.10" min="0.01" step="0.01">
  </div>
  <button class="btn-apply" style="background:var(--orange)" onclick="applyBudget()">Set</button>
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

    const actionsHTML = (a.recent_actions||[]).map(r =>
      `<div class="action-row ${r.success?'success':'fail'}">
        <span>${r.success?'✓':'✗'}</span><span>${r.action}</span>
        <span style="margin-left:auto;font-size:10px">$${r.cost}</span>
      </div>`).join('') || '<div class="action-row">no actions yet</div>';

    const notesHTML = (a.notes||[]).map(n =>
      `<div class="note-item">${n}</div>`).join('');

    const alertCallout  = a.alert_reason  ? `<div class="callout red">${a.alert_reason}</div>` : '';
    const flagCallout   = a.flagged && !a.alert_reason ? `<div class="callout orange">⚠ flagged for human review</div>` : '';
    const pauseCallout  = a.paused ? `<div class="callout blue">⏸ paused — not accumulating cost</div>` : '';
    const projCallout   = (!a.paused && !a.completed && a.proj_1h > 0.01)
      ? `<div class="callout" style="border-color:#555;background:rgba(255,255,255,.03);color:#888;margin-top:8px">
           📈 at current rate: <strong style="color:#ccc">$${a.proj_1h}/hr</strong>${a.budget ? ` · budget $${a.budget}` : ''}
         </div>` : '';

    const pauseBtn  = a.paused
      ? `<button class="btn btn-resume" onclick="api('resume','${a.agent_id}')">▶ Resume</button>`
      : `<button class="btn btn-pause"  onclick="api('pause','${a.agent_id}')">⏸ Pause</button>`;
    const flagBtn = '';

    card.innerHTML = `
      <div class="card-top">
        <span class="agent-name"><span class="pulse ${pulse}"></span>${a.agent_id}</span>
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
      ${alertCallout}${flagCallout}${pauseCallout}${projCallout}
      ${notesHTML ? `<div class="notes-list">${notesHTML}</div>` : ''}
      <div class="controls">
        ${pauseBtn}
        ${flagBtn}
        <button class="btn btn-note" onclick="toggleNote('${a.agent_id}', this)">✎ Note</button>
      </div>
      <div class="note-row" id="note-${a.agent_id.replace(/[^a-z0-9]/gi,'_')}">
        <input class="note-input" type="text" placeholder="add a note..." onkeydown="if(event.key==='Enter')sendNote('${a.agent_id}',this)">
        <button class="btn-send" onclick="sendNote('${a.agent_id}',this.previousElementSibling)">Send</button>
      </div>
    `;
    return card;
  }

  function renderAlertLog(alerts) {
    const log = document.getElementById('alert-log');
    if (!alerts||!alerts.length) { log.innerHTML='<div class="empty-log">no alerts yet</div>'; return; }
    log.innerHTML = [...alerts].reverse().map(a => `
      <div class="alert-entry ${a.dismissed?'dismissed':''} ${a.type==='peer'?'peer':''}">
        <div class="alert-meta">
          <span class="alert-agent">${a.type==='peer'?'⚠ PEER — ':'⛔ ALERT — '}${a.agent_id}</span>
          <span class="alert-time">${a.time}</span>
        </div>
        <div class="alert-reason">${a.reason}</div>
        <div class="alert-rec">${a.recommendation.replace(/\\n/g,'<br>')}</div>
        ${!a.dismissed && a.id ? `<button class="btn-dismiss" onclick="dismissAlert('${a.id}')">Dismiss</button>` : ''}
      </div>`).join('');
  }

  function api(action, agentId) {
    fetch('/api/' + action, {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({agent_id: agentId})
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
    });
    input.value = '';
    const key = agentId.replace(/[^a-z0-9]/gi,'_');
    document.getElementById('note-' + key).classList.remove('open');
  }

  function dismissAlert(alertId) {
    fetch('/api/dismiss', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({alert_id: alertId})
    });
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
    if (!agent) { alert('Enter an agent ID'); return; }
    fetch('/api/set_budget', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({agent_id: agent, budget_usd: cap})
    });
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
    });
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
            flagged_list.append({
                "agent_id": agent_id,
                "reason":   reason,
                "time":     dt.datetime.now().strftime("%H:%M:%S"),
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
            "notes":          state.notes,
            "alert_reason":   matched.reason if matched else None,
            "proj_1h":        f"{state.projected_cost_1h:.4f}",
            "budget":         f"{state.budget_usd:.2f}" if state.budget_usd else None,
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
    for pj in getattr(watcher, "peer_alerts", []):
        all_alerts.append({**pj, "type": "peer", "dismissed": False, "id": None})

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
        if _watcher and _watcher.states:
            yield f"data: {json.dumps(_snapshot(_watcher))}\n\n"
        while True:
            changed = _watcher._dirty.wait(timeout=2.0) if _watcher else False
            if changed and _watcher and _watcher.states:
                _watcher._dirty.clear()
                yield f"data: {json.dumps(_snapshot(_watcher))}\n\n"
    return Response(generate(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.route("/api/pause",      methods=["POST"])
def api_pause():
    _watcher.pause(request.json["agent_id"]); return jsonify(ok=True)

@app.route("/api/resume",     methods=["POST"])
def api_resume():
    _watcher.resume(request.json["agent_id"]); return jsonify(ok=True)


@app.route("/api/note",       methods=["POST"])
def api_note():
    _watcher.add_note(request.json["agent_id"], request.json["note"]); return jsonify(ok=True)

@app.route("/api/dismiss",    methods=["POST"])
def api_dismiss():
    _watcher.dismiss_alert(request.json["alert_id"]); return jsonify(ok=True)

@app.route("/api/set_budget", methods=["POST"])
def api_set_budget():
    d = request.json
    _watcher.set_budget(d["agent_id"], d["budget_usd"])
    return jsonify(ok=True)

@app.route("/api/escalate", methods=["POST"])
def api_escalate():
    """Human reviewed a flag and decided it's actually broken — fire a full alert."""
    agent_id = request.json["agent_id"]
    _watcher.escalate_flag(agent_id)
    return jsonify(ok=True)

@app.route("/api/clear_flag", methods=["POST"])
def api_clear_flag():
    """Human reviewed a flag and decided the agent is fine — clear it."""
    _watcher.unflag(agent_id=request.json["agent_id"])
    return jsonify(ok=True)

@app.route("/api/thresholds", methods=["POST"])
def api_thresholds():
    d = request.json
    _watcher.set_thresholds(retry=d.get("retry"), cost=d.get("cost"), time=d.get("time"))
    return jsonify(ok=True)


def start(watcher, port=5050):
    global _watcher
    _watcher = watcher
    threading.Thread(
        target=lambda: app.run(port=port, debug=False, use_reloader=False),
        daemon=True,
    ).start()
    print(f"  Dashboard → http://localhost:{port}\n")
