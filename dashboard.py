"""
Live dashboard server for SelfAudit.
Serves the UI and a Server-Sent Events stream that pushes state every 500ms.
"""

import json
import time
import threading
from flask import Flask, Response, render_template_string

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
      --bg: #0a0a0a;
      --surface: #141414;
      --border: #222;
      --text: #e0e0e0;
      --muted: #444;
      --green: #30d158;
      --red: #ff3b30;
      --orange: #ff9f0a;
      --blue: #0a84ff;
    }

    body {
      background: var(--bg);
      color: var(--text);
      font-family: 'SF Mono', 'Menlo', 'Consolas', monospace;
      font-size: 13px;
      min-height: 100vh;
    }

    /* ── header ── */
    .header {
      position: sticky;
      top: 0;
      z-index: 10;
      background: rgba(10,10,10,0.92);
      backdrop-filter: blur(12px);
      border-bottom: 1px solid var(--border);
      padding: 14px 28px;
      display: flex;
      align-items: center;
      gap: 32px;
    }

    .logo {
      font-size: 15px;
      font-weight: 700;
      letter-spacing: 0.06em;
      color: #fff;
      white-space: nowrap;
    }

    .logo span { color: var(--green); }

    .stats {
      display: flex;
      gap: 24px;
      flex: 1;
    }

    .stat {
      display: flex;
      flex-direction: column;
      gap: 2px;
    }

    .stat label {
      font-size: 9px;
      letter-spacing: 0.1em;
      text-transform: uppercase;
      color: var(--muted);
    }

    .stat .val {
      font-size: 14px;
      font-weight: 600;
      color: var(--text);
    }

    .stat .val.danger { color: var(--red); }
    .stat .val.good   { color: var(--green); }

    .live-dot {
      width: 7px; height: 7px;
      border-radius: 50%;
      background: var(--green);
      animation: blink 1.4s infinite;
      margin-left: auto;
    }
    .live-dot.off { background: var(--muted); animation: none; }

    @keyframes blink {
      0%,100% { opacity:1; }
      50%      { opacity:0.2; }
    }

    /* ── main layout ── */
    .main { padding: 28px; }

    .section-title {
      font-size: 10px;
      letter-spacing: 0.12em;
      text-transform: uppercase;
      color: var(--muted);
      margin-bottom: 14px;
    }

    /* ── agent grid ── */
    .grid {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
      gap: 14px;
      margin-bottom: 40px;
    }

    .card {
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 18px;
      transition: border-color 0.25s, box-shadow 0.25s;
    }
    .card.alerted {
      border-color: var(--red);
      box-shadow: 0 0 24px rgba(255,59,48,0.12);
    }
    .card.done {
      border-color: #1e3a26;
    }
    .card.peer-flagged {
      border-color: var(--orange);
      box-shadow: 0 0 24px rgba(255,159,10,0.1);
    }

    .card-top {
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 14px;
    }

    .agent-name {
      font-size: 13px;
      font-weight: 600;
      color: #fff;
      display: flex;
      align-items: center;
      gap: 6px;
    }

    .pulse {
      width: 6px; height: 6px;
      border-radius: 50%;
      background: var(--green);
      animation: blink 1.4s infinite;
      flex-shrink: 0;
    }
    .pulse.red { background: var(--red); }
    .pulse.orange { background: var(--orange); }
    .pulse.off { background: #2a2a2a; animation: none; }

    .badge {
      font-size: 9px;
      font-weight: 700;
      letter-spacing: 0.1em;
      padding: 3px 7px;
      border-radius: 4px;
      text-transform: uppercase;
    }
    .badge.running  { background: #0d2b18; color: var(--green); }
    .badge.alerted  { background: #2b0d0d; color: var(--red); }
    .badge.done     { background: #0d2b18; color: #34c759; }
    .badge.flagged  { background: #2b1d0d; color: var(--orange); }
    .badge.waiting  { background: #1a1a1a; color: var(--muted); }

    .metrics {
      display: grid;
      grid-template-columns: 1fr 1fr 1fr 1fr;
      gap: 10px;
      margin-bottom: 12px;
    }

    .metric label {
      display: block;
      font-size: 9px;
      color: var(--muted);
      letter-spacing: 0.08em;
      text-transform: uppercase;
      margin-bottom: 2px;
    }

    .metric .val {
      font-size: 16px;
      font-weight: 600;
    }
    .metric .val.danger { color: var(--red); }
    .metric .val.good   { color: var(--green); }
    .metric .val.warn   { color: var(--orange); }

    .bar-wrap {
      background: #1a1a1a;
      border-radius: 3px;
      height: 3px;
      margin-bottom: 12px;
      overflow: hidden;
    }
    .bar {
      height: 100%;
      border-radius: 3px;
      background: var(--green);
      transition: width 0.4s ease;
    }
    .bar.danger { background: var(--red); }
    .bar.warn   { background: var(--orange); }

    /* action history */
    .actions {
      display: flex;
      flex-direction: column;
      gap: 4px;
      margin-bottom: 10px;
    }
    .action-row {
      display: flex;
      align-items: center;
      gap: 6px;
      font-size: 11px;
      color: var(--muted);
    }
    .action-row.success { color: #3a8a50; }
    .action-row.fail    { color: #7a2a2a; }
    .action-icon { font-size: 10px; width: 12px; }

    .callout {
      margin-top: 10px;
      padding: 8px 10px;
      border-radius: 4px;
      font-size: 11px;
      line-height: 1.5;
      border-left: 3px solid;
    }
    .callout.red    { border-color: var(--red);    background: rgba(255,59,48,0.07);  color: #ff7a72; }
    .callout.orange { border-color: var(--orange); background: rgba(255,159,10,0.07); color: #ffc050; }

    /* ── alert log ── */
    .alert-log {
      border-top: 1px solid var(--border);
      padding-top: 28px;
      margin-top: 10px;
    }

    .alert-entry {
      background: var(--surface);
      border: 1px solid #2b0d0d;
      border-left: 3px solid var(--red);
      border-radius: 6px;
      padding: 14px 16px;
      margin-bottom: 10px;
    }
    .alert-entry.peer {
      border-color: #2b1d0d;
      border-left-color: var(--orange);
    }

    .alert-meta {
      display: flex;
      justify-content: space-between;
      margin-bottom: 6px;
    }
    .alert-agent { font-weight: 700; color: var(--red); font-size: 12px; }
    .alert-entry.peer .alert-agent { color: var(--orange); }
    .alert-time  { font-size: 10px; color: var(--muted); }

    .alert-reason { color: #ccc; margin-bottom: 6px; font-size: 12px; }
    .alert-rec    { color: var(--muted); font-size: 11px; line-height: 1.6; }

    .empty-log {
      color: var(--muted);
      font-size: 12px;
      padding: 16px 0;
    }
  </style>
</head>
<body>

<div class="header">
  <div class="logo">Self<span>Audit</span></div>
  <div class="stats">
    <div class="stat">
      <label>total cost</label>
      <div class="val" id="h-cost">$0.0000</div>
    </div>
    <div class="stat">
      <label>alerts</label>
      <div class="val danger" id="h-alerts">0</div>
    </div>
    <div class="stat">
      <label>agents done</label>
      <div class="val good" id="h-done">0</div>
    </div>
    <div class="stat">
      <label>agents running</label>
      <div class="val" id="h-running">0</div>
    </div>
    <div class="stat">
      <label>last update</label>
      <div class="val" id="h-time">—</div>
    </div>
  </div>
  <div class="live-dot" id="live-dot"></div>
</div>

<div class="main">
  <div class="section-title">agents</div>
  <div class="grid" id="grid">
    <div class="card">
      <div class="card-top">
        <span class="agent-name">waiting for agents...</span>
        <span class="badge waiting">—</span>
      </div>
    </div>
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
    renderAlertLog(data.alerts);
    document.getElementById('live-dot').className = 'live-dot';
  };

  source.onerror = () => {
    document.getElementById('live-dot').className = 'live-dot off';
  };

  function updateHeader(data) {
    document.getElementById('h-cost').textContent    = '$' + data.total_cost;
    document.getElementById('h-alerts').textContent  = data.alert_count;
    document.getElementById('h-done').textContent    = data.done_count;
    document.getElementById('h-running').textContent = data.running_count;
    document.getElementById('h-time').textContent    = new Date().toLocaleTimeString();
  }

  function renderGrid(agents) {
    const grid = document.getElementById('grid');
    grid.innerHTML = '';
    agents.forEach(a => grid.appendChild(makeCard(a)));
  }

  function makeCard(a) {
    const card = document.createElement('div');
    const isPeerFlagged = a.peer_flagged && !a.alerted;
    const cls = a.status === 'ALERTED' ? 'alerted'
              : isPeerFlagged           ? 'peer-flagged'
              : a.status === 'DONE'     ? 'done' : '';
    card.className = 'card ' + cls;

    const pulseClass = a.status === 'ALERTED' ? 'red'
                     : isPeerFlagged           ? 'orange'
                     : a.status === 'DONE'     ? 'off' : '';

    const badgeClass = a.status === 'ALERTED' ? 'alerted'
                     : isPeerFlagged           ? 'flagged'
                     : a.status === 'DONE'     ? 'done' : 'running';

    const badgeText  = isPeerFlagged && a.status !== 'ALERTED' ? 'DRIFTING' : a.status;

    const costPct  = Math.min(100, (parseFloat(a.cost) / 0.05) * 100);
    const barClass = a.status === 'ALERTED' ? 'danger'
                   : isPeerFlagged           ? 'warn' : '';

    const costClass    = a.status === 'ALERTED' ? ' danger' : '';
    const progressClass = a.progress > 0 ? ' good' : '';
    const retriesClass  = a.retries >= 3 ? ' danger' : a.retries >= 2 ? ' warn' : '';

    const actionsHTML = (a.recent_actions || []).map(act => `
      <div class="action-row ${act.success ? 'success' : 'fail'}">
        <span class="action-icon">${act.success ? '✓' : '✗'}</span>
        <span>${act.action}</span>
        <span style="margin-left:auto;font-size:10px">$${act.cost}</span>
      </div>`).join('');

    const alertCallout = a.alert_reason
      ? `<div class="callout red">${a.alert_reason}</div>` : '';

    const peerCallout = isPeerFlagged && a.peer_reason
      ? `<div class="callout orange">⚠ peer judge: ${a.peer_reason}</div>` : '';

    card.innerHTML = `
      <div class="card-top">
        <span class="agent-name">
          <span class="pulse ${pulseClass}"></span>${a.agent_id}
        </span>
        <span class="badge ${badgeClass}">${badgeText}</span>
      </div>
      <div class="metrics">
        <div class="metric">
          <label>cost</label>
          <div class="val${costClass}">$${a.cost}</div>
        </div>
        <div class="metric">
          <label>steps</label>
          <div class="val${progressClass}">${a.progress}</div>
        </div>
        <div class="metric">
          <label>retries</label>
          <div class="val${retriesClass}">${a.retries}</div>
        </div>
        <div class="metric">
          <label>elapsed</label>
          <div class="val">${a.elapsed}s</div>
        </div>
      </div>
      <div class="bar-wrap"><div class="bar ${barClass}" style="width:${costPct}%"></div></div>
      <div class="actions">${actionsHTML || '<div class="action-row">no actions yet</div>'}</div>
      ${alertCallout}${peerCallout}
    `;
    return card;
  }

  function renderAlertLog(alerts) {
    const log = document.getElementById('alert-log');
    if (!alerts || alerts.length === 0) {
      log.innerHTML = '<div class="empty-log">no alerts yet</div>';
      return;
    }
    log.innerHTML = [...alerts].reverse().map(a => `
      <div class="alert-entry ${a.type === 'peer' ? 'peer' : ''}">
        <div class="alert-meta">
          <span class="alert-agent">${a.type === 'peer' ? '⚠ PEER JUDGE — ' : '⛔ ALERT — '}${a.agent_id}</span>
          <span class="alert-time">${a.time}</span>
        </div>
        <div class="alert-reason">${a.reason}</div>
        <div class="alert-rec">${a.recommendation.replace(/\\n/g, '<br>')}</div>
      </div>`).join('');
  }
</script>
</body>
</html>
"""


def _snapshot(watcher):
    agents = []
    all_alerts = []
    total_cost = 0.0
    alert_count = done_count = running_count = 0

    for agent_id, state in sorted(watcher.states.items()):
        status = "DONE" if state.completed else ("ALERTED" if state.alerted else "RUNNING")
        if state.alerted:   alert_count += 1
        if state.completed: done_count += 1
        else:               running_count += 1
        total_cost += state.cumulative_cost

        matched_alert = next((a for a in watcher.alerts if a.agent_id == agent_id), None)

        recent = [
            {
                "action": ev.action,
                "success": ev.success,
                "cost": f"{ev.cost_usd:.4f}",
            }
            for ev in state.events[-4:]
        ]

        peer_info = getattr(state, "peer_verdict", None)

        agents.append({
            "agent_id":      agent_id,
            "status":        status,
            "cost":          f"{state.cumulative_cost:.4f}",
            "progress":      state.progress_score,
            "retries":       state.max_retry_count,
            "elapsed":       f"{state.elapsed:.1f}",
            "recent_actions": recent,
            "alerted":       state.alerted,
            "alert_reason":  matched_alert.reason if matched_alert else None,
            "peer_flagged":  peer_info and not peer_info.get("on_task", True),
            "peer_reason":   peer_info.get("reason") if peer_info else None,
        })

    for a in watcher.alerts:
        import datetime
        all_alerts.append({
            "agent_id":       a.agent_id,
            "reason":         a.reason,
            "recommendation": a.recommendation,
            "time":           datetime.datetime.fromtimestamp(a.timestamp).strftime("%H:%M:%S"),
            "type":           "watcher",
        })

    # peer judge alerts stored separately on watcher if present
    for pj in getattr(watcher, "peer_alerts", []):
        import datetime
        all_alerts.append({
            "agent_id":       pj["agent_id"],
            "reason":         pj["reason"],
            "recommendation": pj["recommendation"],
            "time":           pj["time"],
            "type":           "peer",
        })

    return {
        "agents":        agents,
        "alerts":        all_alerts,
        "total_cost":    f"{total_cost:.4f}",
        "alert_count":   alert_count,
        "done_count":    done_count,
        "running_count": running_count,
    }


@app.route("/")
def index():
    return render_template_string(HTML)


@app.route("/stream")
def stream():
    def generate():
        while True:
            if _watcher and _watcher.states:
                yield f"data: {json.dumps(_snapshot(_watcher))}\n\n"
            time.sleep(0.5)

    return Response(generate(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def start(watcher, port=5050):
    global _watcher
    _watcher = watcher
    threading.Thread(
        target=lambda: app.run(port=port, debug=False, use_reloader=False),
        daemon=True,
    ).start()
    print(f"  Dashboard → http://localhost:{port}\n")
