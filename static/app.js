/* ============================================================
 * Critical Path Monitor — front end
 *
 * The question this UI exists to answer:
 *   when a task is blocked, is that block FREE, or is it costing
 *   delivery time right now?
 *
 * buffer_min is static (from the DAG). buffer_remaining_min is live:
 * it is ls - es, and for a pending/blocked task es is floored at the
 * job's elapsed time, so it ticks DOWN while the task sits there.
 * Once it goes negative the block is burning delivery time, and that
 * shows up as job.delay_incurred_min.
 * ============================================================ */

'use strict';

const POLL_MS = 500;
const MAX_EVENTS = 400;

const WORKFLOWS = {
  compute_rack: 'Compute rack',
  storage_rack: 'Storage rack',
};

/* ── State ─────────────────────────────────────────────────── */

const state = {
  config: null,
  jobs: new Map(),        // job_id -> JobSnapshot
  jobOrder: [],           // job_ids, server order
  events: [],             // ascending by seq
  seenSeq: new Set(),
  lastSeq: 0,
  selectedId: null,
  edgesByWorkflow: new Map(),
  failures: 0,
};

/* ── DOM ───────────────────────────────────────────────────── */

const $ = (id) => document.getElementById(id);
const el = {
  cfgMaxJobs: $('cfg-max-jobs'),
  cfgConcJobs: $('cfg-max-conc-jobs'),
  cfgConcTasks: $('cfg-max-conc-tasks'),
  cfgTick: $('cfg-tick'),
  linkState: $('link-state'),
  linkLabel: $('link-label'),
  jobList: $('job-list'),
  jobCount: $('job-count'),
  btnCompute: $('btn-new-compute'),
  btnStorage: $('btn-new-storage'),
  btnDemo: $('btn-demo'),
  rerunBanner: $('rerun-banner'),
  btnRerun: $('btn-rerun'),
  dagName: $('dag-name'),
  dagWorkflow: $('dag-workflow'),
  dagState: $('dag-state'),
  dagReadouts: $('dag-readouts'),
  dagScroll: $('dag-scroll'),
  dagEmpty: $('dag-empty'),
  dagSvg: $('dag-svg'),
  dagHint: $('dag-hint'),
  eventLog: $('event-log'),
  followToggle: $('follow-toggle'),
  toasts: $('toasts'),
};

/* ── Formatting ────────────────────────────────────────────── */

/** 400 -> "6h 40m", 45 -> "45m", 0 -> "0m". Never raw decimals. */
function fmtMin(min) {
  if (min === null || min === undefined || !isFinite(min)) return '—';
  const neg = min < 0;
  const total = Math.round(Math.abs(min));
  const h = Math.floor(total / 60);
  const m = total % 60;
  const body = h > 0 ? `${h}h ${m}m` : `${m}m`;
  return neg ? `-${body}` : body;
}

/** Signed, for deltas: "+15m", "on time". */
function fmtDelta(min) {
  if (min === null || min === undefined || !isFinite(min)) return '—';
  if (Math.round(min) === 0) return 'on time';
  return (min > 0 ? '+' : '') + fmtMin(min);
}

function isLate(job) {
  return job && job.delay_incurred_min > 0.5;
}

/* ── HTTP ──────────────────────────────────────────────────── */

class ApiError extends Error {
  constructor(status, detail) {
    super(detail);
    this.status = status;
    this.detail = detail;
  }
}

async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  });
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const body = await res.json();
      if (body && body.detail) {
        detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail);
      }
    } catch (_) { /* non-JSON error body */ }
    throw new ApiError(res.status, detail);
  }
  return res.json();
}

/* ── Toasts ────────────────────────────────────────────────── */

function toast(message, kind = 'err', ms = 4200) {
  const node = document.createElement('div');
  node.className = `toast toast-${kind}`;
  node.textContent = message;
  el.toasts.appendChild(node);
  setTimeout(() => {
    node.classList.add('out');
    setTimeout(() => node.remove(), 260);
  }, ms);
}

/* ── Polling ───────────────────────────────────────────────── */

function setLink(ok) {
  if (ok) {
    state.failures = 0;
    el.linkState.dataset.state = 'live';
    el.linkLabel.textContent = 'live';
  } else {
    state.failures += 1;
    // One dropped poll is noise; two in a row is worth showing.
    if (state.failures >= 2) {
      el.linkState.dataset.state = 'down';
      el.linkLabel.textContent = 'reconnecting';
    }
  }
}

function ingestState(data) {
  if (data.config) applyConfig(data.config);

  if (Array.isArray(data.jobs)) {
    const next = new Map();
    const order = [];
    for (const job of data.jobs) {
      next.set(job.job_id, job);
      order.push(job.job_id);
    }
    state.jobs = next;
    state.jobOrder = order;
    if (state.selectedId && !next.has(state.selectedId)) state.selectedId = null;
    if (!state.selectedId && order.length) state.selectedId = order[0];
  }

  if (Array.isArray(data.events) && data.events.length) {
    let added = false;
    for (const ev of data.events) {
      if (state.seenSeq.has(ev.seq)) continue;
      state.seenSeq.add(ev.seq);
      state.events.push(ev);
      added = true;
    }
    if (added) {
      state.events.sort((a, b) => a.seq - b.seq);
      if (state.events.length > MAX_EVENTS) {
        const dropped = state.events.splice(0, state.events.length - MAX_EVENTS);
        for (const ev of dropped) state.seenSeq.delete(ev.seq);
      }
    }
  }

  if (typeof data.seq === 'number') state.lastSeq = data.seq;
}

function ingestJobDetail(detail) {
  if (!detail || !detail.job_id) return;
  if (Array.isArray(detail.edges)) {
    state.edgesByWorkflow.set(detail.workflow_id, detail.edges);
  }
  // The detail response is at least as fresh as the list; prefer it.
  const existing = state.jobs.get(detail.job_id);
  if (!existing || detail.snapshot_seq >= existing.snapshot_seq) {
    state.jobs.set(detail.job_id, detail);
  }
}

async function poll() {
  const wanted = state.selectedId;
  const calls = [api(`/api/state?since=${state.lastSeq}`)];
  if (wanted) calls.push(api(`/api/jobs/${encodeURIComponent(wanted)}`));

  const results = await Promise.allSettled(calls);

  const stateRes = results[0];
  if (stateRes.status === 'fulfilled') {
    ingestState(stateRes.value);
    setLink(true);
  } else {
    setLink(false);
  }

  // A 404 here just means the job aged out; the list poll will correct us.
  if (results[1] && results[1].status === 'fulfilled') {
    ingestJobDetail(results[1].value);
  }

  render();
}

async function pollLoop() {
  try {
    await poll();
  } catch (err) {
    // Never let a render or parse error kill the loop.
    console.error('poll cycle failed', err);
    setLink(false);
  }
  setTimeout(pollLoop, POLL_MS);
}

/* ── Config strip ──────────────────────────────────────────── */

function applyConfig(cfg) {
  const changed = !state.config
    || state.config.max_jobs !== cfg.max_jobs
    || state.config.max_concurrent_jobs !== cfg.max_concurrent_jobs
    || state.config.max_concurrent_tasks !== cfg.max_concurrent_tasks
    || state.config.tick_ms !== cfg.tick_ms;
  state.config = cfg;
  if (!changed) return;
  el.cfgMaxJobs.textContent = cfg.max_jobs;
  el.cfgConcJobs.textContent = cfg.max_concurrent_jobs;
  el.cfgConcTasks.textContent = cfg.max_concurrent_tasks;
  el.cfgTick.textContent = `${cfg.tick_ms}ms/min`;
}

/* ── Job list (keyed reconciliation) ───────────────────────── */

const jobCards = new Map(); // job_id -> { root, refs }

function buildJobCard(job) {
  const root = document.createElement('button');
  root.type = 'button';
  root.className = 'job';
  root.innerHTML = `
    <div class="job-top">
      <span class="job-name"></span>
      <span class="pill" style="margin-left:auto"></span>
    </div>
    <div class="job-wf"></div>
    <div class="job-grid">
      <div class="jg"><span class="jg-k">elapsed</span><span class="jg-v" data-k="elapsed"></span></div>
      <div class="jg"><span class="jg-k">eta</span><span class="jg-v" data-k="eta"></span></div>
      <div class="jg"><span class="jg-k">baseline</span><span class="jg-v" data-k="baseline"></span></div>
    </div>
    <span class="delay-chip"></span>`;

  const refs = {
    name: root.querySelector('.job-name'),
    pill: root.querySelector('.pill'),
    wf: root.querySelector('.job-wf'),
    elapsed: root.querySelector('[data-k="elapsed"]'),
    eta: root.querySelector('[data-k="eta"]'),
    baseline: root.querySelector('[data-k="baseline"]'),
    chip: root.querySelector('.delay-chip'),
  };

  root.addEventListener('click', () => selectJob(job.job_id));
  return { root, refs };
}

function updateJobCard(card, job) {
  const { refs, root } = card;
  const late = isLate(job);

  root.classList.toggle('selected', job.job_id === state.selectedId);
  root.classList.toggle('late', late);

  refs.name.textContent = job.name || job.job_id;
  refs.pill.textContent = job.state;
  refs.pill.className = `pill pill-${job.state}`;
  refs.pill.style.marginLeft = 'auto';
  refs.wf.textContent = job.workflow_name || WORKFLOWS[job.workflow_id] || job.workflow_id;
  refs.elapsed.textContent = fmtMin(job.elapsed_min);
  refs.eta.textContent = fmtMin(job.projected_finish_min);
  refs.baseline.textContent = fmtMin(job.baseline_finish_min);

  refs.chip.textContent = late
    ? `${fmtDelta(job.delay_incurred_min)} late`
    : 'on time';
  refs.chip.className = late ? 'delay-chip' : 'delay-chip on-time';
}

function renderJobs() {
  const jobs = state.jobOrder.map((id) => state.jobs.get(id)).filter(Boolean);

  el.jobCount.textContent = state.config
    ? `${jobs.length}/${state.config.max_jobs}`
    : String(jobs.length);

  const atCapacity = !!state.config && jobs.length >= state.config.max_jobs;
  el.btnCompute.disabled = atCapacity;
  el.btnStorage.disabled = atCapacity;
  el.btnCompute.title = atCapacity ? 'At max_jobs capacity' : '';
  el.btnStorage.title = atCapacity ? 'At max_jobs capacity' : '';

  if (!jobs.length) {
    if (jobCards.size) {
      jobCards.clear();
      el.jobList.innerHTML = '';
    }
    if (!el.jobList.querySelector('.empty')) {
      el.jobList.innerHTML = '<p class="empty">No jobs yet. Create one, or run the demo.</p>';
    }
    return;
  }

  const stale = el.jobList.querySelector('.empty');
  if (stale) stale.remove();

  const seen = new Set();
  jobs.forEach((job, index) => {
    seen.add(job.job_id);
    let card = jobCards.get(job.job_id);
    if (!card) {
      card = buildJobCard(job);
      jobCards.set(job.job_id, card);
    }
    updateJobCard(card, job);
    // Keep DOM order in sync with server order without churning nodes.
    const current = el.jobList.children[index];
    if (current !== card.root) el.jobList.insertBefore(card.root, current || null);
  });

  for (const [id, card] of jobCards) {
    if (!seen.has(id)) {
      card.root.remove();
      jobCards.delete(id);
    }
  }
}

function selectJob(jobId) {
  if (state.selectedId === jobId) return;
  state.selectedId = jobId;
  render();
}

/* ── DAG layout ────────────────────────────────────────────── */

const NODE_W = 160;
const NODE_H = 70;
const COL_PITCH = 220;
const ROW_PITCH = 98;
const PAD = 16;

/**
 * Depth = longest path from any root, over the edge set.
 * X column = depth, Y row = index within that depth, taking rows in the
 * task array's own (human-authored, stable) order.
 */
function layoutNodes(tasks, edges) {
  const ids = tasks.map((t) => t.id);
  const idSet = new Set(ids);
  const indegree = new Map(ids.map((id) => [id, 0]));
  const children = new Map(ids.map((id) => [id, []]));

  for (const e of edges) {
    if (!idSet.has(e.source) || !idSet.has(e.target)) continue;
    children.get(e.source).push(e.target);
    indegree.set(e.target, indegree.get(e.target) + 1);
  }

  const depth = new Map(ids.map((id) => [id, 0]));
  const queue = ids.filter((id) => indegree.get(id) === 0);
  const order = [];
  while (queue.length) {
    const id = queue.shift();
    order.push(id);
    for (const child of children.get(id)) {
      depth.set(child, Math.max(depth.get(child), depth.get(id) + 1));
      indegree.set(child, indegree.get(child) - 1);
      if (indegree.get(child) === 0) queue.push(child);
    }
  }
  // A cycle would strand nodes; place them after everything rather than drop them.
  if (order.length !== ids.length) {
    for (const id of ids) if (!order.includes(id)) depth.set(id, 0);
  }

  const rows = new Map(); // depth -> count so far
  const pos = new Map();
  let maxRow = 0;
  for (const t of tasks) {
    const d = depth.get(t.id) || 0;
    const r = rows.get(d) || 0;
    rows.set(d, r + 1);
    maxRow = Math.max(maxRow, r + 1);
    pos.set(t.id, { depth: d, row: r });
  }

  const maxDepth = Math.max(0, ...ids.map((id) => depth.get(id) || 0));

  // Vertically centre each column so the graph reads as a flow.
  for (const t of tasks) {
    const p = pos.get(t.id);
    const inCol = rows.get(p.depth);
    const offset = ((maxRow - inCol) * ROW_PITCH) / 2;
    p.x = PAD + p.depth * COL_PITCH;
    p.y = PAD + offset + p.row * ROW_PITCH;
  }

  return {
    pos,
    width: PAD * 2 + maxDepth * COL_PITCH + NODE_W,
    height: PAD * 2 + (maxRow - 1) * ROW_PITCH + NODE_H,
  };
}

/* ── DAG rendering ─────────────────────────────────────────── */

const SVG_NS = 'http://www.w3.org/2000/svg';
const svgEl = (tag, attrs = {}) => {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  return node;
};

const dagView = {
  key: null,          // `${job_id}:${workflow_id}` — rebuild only when this changes
  nodes: new Map(),   // task_id -> refs
  edges: [],
};

function truncate(text, max) {
  return text.length > max ? `${text.slice(0, max - 1)}…` : text;
}

function buildDag(job, edges, geo) {
  el.dagSvg.innerHTML = '';
  dagView.nodes.clear();
  dagView.edges = [];

  el.dagSvg.setAttribute('width', geo.width);
  el.dagSvg.setAttribute('height', geo.height);
  el.dagSvg.setAttribute('viewBox', `0 0 ${geo.width} ${geo.height}`);

  const edgeLayer = svgEl('g');
  const nodeLayer = svgEl('g');
  el.dagSvg.appendChild(edgeLayer);
  el.dagSvg.appendChild(nodeLayer);

  // Edges behind nodes.
  for (const e of edges) {
    const a = geo.pos.get(e.source);
    const b = geo.pos.get(e.target);
    if (!a || !b) continue;
    const x1 = a.x + NODE_W;
    const y1 = a.y + NODE_H / 2;
    const x2 = b.x;
    const y2 = b.y + NODE_H / 2;
    const dx = Math.max(28, (x2 - x1) * 0.45);
    const path = svgEl('path', {
      class: 'edge',
      d: `M ${x1} ${y1} C ${x1 + dx} ${y1}, ${x2 - dx} ${y2}, ${x2} ${y2}`,
    });
    edgeLayer.appendChild(path);
    dagView.edges.push({ path, source: e.source, target: e.target });
  }

  for (const task of job.tasks) {
    const p = geo.pos.get(task.id);
    if (!p) continue;

    const g = svgEl('g', { class: 'node', transform: `translate(${p.x},${p.y})` });
    const title = svgEl('title');
    const box = svgEl('rect', { class: 'node-box', width: NODE_W, height: NODE_H, rx: 7 });
    const label = svgEl('text', { class: 'node-label', x: 11, y: 20 });
    const stateText = svgEl('text', { class: 'node-state', x: 11, y: 38 });
    const duration = svgEl('text', {
      class: 'node-sub ok', x: NODE_W - 11, y: 38, 'text-anchor': 'end',
    });
    const sub = svgEl('text', { class: 'node-sub', x: 11, y: 54 });
    const track = svgEl('rect', { class: 'buf-track', x: 11, y: 59, width: NODE_W - 22, height: 4, rx: 2 });
    const fill = svgEl('rect', { class: 'buf-fill', x: 11, y: 59, width: 0, height: 4, rx: 2 });
    const ready = svgEl('circle', { class: 'ready-dot', cx: NODE_W - 10, cy: 10, r: 3 });

    g.append(title, box, label, stateText, duration, sub, track, fill, ready);
    nodeLayer.appendChild(g);

    label.textContent = truncate(task.label, 22);
    duration.textContent = fmtMin(task.duration_min);

    g.addEventListener('click', () => onNodeClick(job.job_id, task.id));

    dagView.nodes.set(task.id, { g, title, sub, stateText, fill, ready });
  }
}

function updateDag(job) {
  const byId = new Map(job.tasks.map((t) => [t.id, t]));
  const innerW = NODE_W - 22;

  for (const task of job.tasks) {
    const refs = dagView.nodes.get(task.id);
    if (!refs) continue;

    const rem = task.buffer_remaining_min;
    const burning = rem < -0.5;
    const critical = task.on_critical_path;
    const actionable = task.state === 'pending' || task.state === 'blocked';

    // Only pending/blocked tasks are still ACCRUING cost: their es is floored
    // at the job's elapsed time, so the buffer keeps draining while they sit.
    // Once a task starts, es pins to when it actually started, so whatever it
    // lost is a past fact rather than an ongoing burn. Saying "BURNING" on a
    // finished task would be a visible lie.
    const accruing = actionable;

    const classes = ['node', `node-${task.state}`];
    if (critical) classes.push('node-cp');
    if (task.stalled && task.state !== 'finished') classes.push('node-stalled');
    if (burning && accruing) classes.push('node-burning');
    if (!actionable) classes.push('inert');
    refs.g.setAttribute('class', classes.join(' '));

    refs.stateText.textContent = task.state;

    // The buffer line: the whole point of the tool.
    let subText;
    let subClass;
    let barWidth;
    let barClass;

    if (burning) {
      // Present tense only while the cost is still growing.
      subText = `${accruing ? 'BURNING' : 'LATE'} ${fmtMin(-rem)}`;
      subClass = 'burn';
      barWidth = innerW;
      barClass = 'none';
    } else if (critical) {
      subText = accruing ? 'NO BUFFER' : 'ON TIME';
      subClass = accruing ? 'crit' : 'ok';
      barWidth = innerW;
      barClass = accruing ? 'none' : '';
    } else {
      const frac = task.buffer_min > 0 ? Math.max(0, Math.min(1, rem / task.buffer_min)) : 0;
      subText = `${fmtMin(rem)} ${accruing ? 'buffer' : 'spare'}`;
      subClass = frac > 0.34 ? 'ok' : 'warn';
      barWidth = innerW * frac;
      barClass = frac > 0.5 ? '' : frac > 0.15 ? 'low' : 'none';
    }

    refs.sub.textContent = subText;
    refs.sub.setAttribute('class', `node-sub ${subClass}`);
    refs.fill.setAttribute('width', Math.max(0, barWidth));
    refs.fill.setAttribute('class', `buf-fill ${barClass}`);

    refs.ready.style.display =
      task.state === 'pending' && task.startable ? '' : 'none';

    refs.title.textContent = [
      `${task.label} (${task.id})`,
      `state: ${task.state}${task.startable ? ' — ready, waiting for a worker' : ''}${task.stalled ? ' — starved by an upstream block' : ''}`,
      `duration: ${fmtMin(task.duration_min)}`,
      `earliest start/finish: ${fmtMin(task.es)} / ${fmtMin(task.ef)}`,
      `latest start/finish: ${fmtMin(task.ls)} / ${fmtMin(task.lf)}`,
      `buffer: ${fmtMin(task.buffer_min)} total, ${fmtMin(rem)} remaining`,
      critical
        ? 'ON THE CRITICAL PATH — blocking this costs delivery time immediately.'
        : 'Has buffer — blocking this is free until the buffer runs out.',
      actionable
        ? (task.state === 'pending' ? 'Click to block.' : 'Click to release.')
        : 'In-flight work runs to completion; cannot be blocked.',
    ].join('\n');
  }

  for (const edge of dagView.edges) {
    const s = byId.get(edge.source);
    const t = byId.get(edge.target);
    const classes = ['edge'];
    if (s && t && s.on_critical_path && t.on_critical_path) classes.push('edge-cp');
    if (s && (s.state === 'blocked' || s.stalled) && t && t.state !== 'finished') {
      classes.push('edge-stalled');
    }
    edge.path.setAttribute('class', classes.join(' '));
  }
}

function renderDag() {
  const job = state.selectedId ? state.jobs.get(state.selectedId) : null;

  if (!job) {
    el.dagName.textContent = 'No job selected';
    el.dagWorkflow.textContent = '';
    el.dagState.textContent = '';
    el.dagState.className = 'pill';
    el.dagReadouts.innerHTML = '';
    el.dagSvg.hidden = true;
    el.dagEmpty.hidden = false;
    el.dagHint.hidden = true;
    dagView.key = null;
    return;
  }

  el.dagName.textContent = job.name || job.job_id;
  el.dagWorkflow.textContent = job.workflow_name || WORKFLOWS[job.workflow_id] || job.workflow_id;
  el.dagState.textContent = job.state;
  el.dagState.className = `pill pill-${job.state}`;

  const late = isLate(job);
  const readouts = [
    { k: 'baseline', v: fmtMin(job.baseline_finish_min), cls: '' },
    { k: 'projected', v: fmtMin(job.projected_finish_min), cls: late ? 'ro-late' : 'ro-ok' },
    { k: 'delay incurred', v: fmtDelta(job.delay_incurred_min), cls: late ? 'ro-late' : 'ro-ok' },
    { k: 'elapsed', v: fmtMin(job.elapsed_min), cls: '' },
  ];
  // Cheap enough to rebuild: no animation lives here.
  el.dagReadouts.innerHTML = readouts
    .map((r) => `<div class="ro ${r.cls}"><span class="ro-k">${r.k}</span><span class="ro-v"></span></div>`)
    .join('');
  el.dagReadouts.querySelectorAll('.ro-v').forEach((node, i) => {
    node.textContent = readouts[i].v;
  });

  const edges = state.edgesByWorkflow.get(job.workflow_id);
  if (!edges) {
    // Detail fetch has not landed yet; the next poll brings the edges.
    el.dagSvg.hidden = true;
    el.dagEmpty.hidden = false;
    el.dagEmpty.textContent = 'Loading graph…';
    el.dagHint.hidden = true;
    return;
  }

  el.dagEmpty.hidden = true;
  el.dagSvg.hidden = false;
  el.dagHint.hidden = false;

  const key = `${job.job_id}:${job.workflow_id}:${job.tasks.length}`;
  if (dagView.key !== key) {
    buildDag(job, edges, layoutNodes(job.tasks, edges));
    dagView.key = key;
  }
  updateDag(job);
}

/* ── Events ────────────────────────────────────────────────── */

let renderedEventSeq = -1;

function eventClass(ev) {
  const m = (ev.message || '').toLowerCase();
  if (/unblock|releas|resum|clear/.test(m)) return 'ev-warn';
  if (/block|delay|late|overrun|burn|breach/.test(m)) return 'ev-hot';
  if (/finish|complet|done|deliver/.test(m)) return 'ev-good';
  return '';
}

function renderEvents() {
  if (!state.events.length) return;
  const newest = state.events[state.events.length - 1].seq;

  // Re-tag selection highlight without rebuilding the log.
  if (newest === renderedEventSeq) {
    for (const row of el.eventLog.children) {
      if (row.dataset && row.dataset.job !== undefined) {
        row.classList.toggle('ev-other', row.dataset.job !== state.selectedId);
      }
    }
    return;
  }

  const stale = el.eventLog.querySelector('.empty');
  if (stale) stale.remove();

  const fresh = state.events.filter((ev) => ev.seq > renderedEventSeq);
  const frag = document.createDocumentFragment();
  for (const ev of fresh) {
    const row = document.createElement('div');
    row.className = `ev ${eventClass(ev)}`;
    if (ev.job_id) {
      row.dataset.job = ev.job_id;
      if (ev.job_id !== state.selectedId) row.classList.add('ev-other');
    }
    const seq = document.createElement('span');
    seq.className = 'ev-seq';
    seq.textContent = ev.seq;
    const at = document.createElement('span');
    at.className = 'ev-at';
    at.textContent = fmtMin(ev.at_min);
    const msg = document.createElement('span');
    msg.className = 'ev-msg';
    msg.textContent = ev.message;
    row.append(seq, at, msg);
    frag.appendChild(row);
  }
  el.eventLog.appendChild(frag);

  while (el.eventLog.children.length > MAX_EVENTS) {
    el.eventLog.removeChild(el.eventLog.firstChild);
  }

  renderedEventSeq = newest;
  if (el.followToggle.checked) el.eventLog.scrollTop = el.eventLog.scrollHeight;
}

/* ── Actions ───────────────────────────────────────────────── */

function applySnapshot(snapshot) {
  if (!snapshot || !snapshot.job_id) return;
  state.jobs.set(snapshot.job_id, snapshot);
  if (!state.jobOrder.includes(snapshot.job_id)) state.jobOrder.push(snapshot.job_id);
  render();
}

async function createJob(workflowId) {
  try {
    const snapshot = await api('/api/jobs', {
      method: 'POST',
      body: JSON.stringify({ workflow: workflowId, name: null }),
    });
    applySnapshot(snapshot);
    state.selectedId = snapshot.job_id;
    toast(`Created ${snapshot.name || snapshot.job_id}`, 'ok', 2400);
    render();
  } catch (err) {
    if (err instanceof ApiError && err.status === 429) {
      toast(err.detail || 'At capacity — finish or wait for a job to clear.', 'warn');
    } else {
      toast(`Could not create job: ${err.message}`, 'err');
    }
  }
}

async function onNodeClick(jobId, taskId) {
  const job = state.jobs.get(jobId);
  const task = job && job.tasks.find((t) => t.id === taskId);
  if (!task) return;

  if (task.state === 'running' || task.state === 'finished') {
    toast(`${task.label} is ${task.state} — in-flight work runs to completion.`, 'warn', 3000);
    return;
  }

  const action = task.state === 'blocked' ? 'unblock' : 'block';
  try {
    const snapshot = await api(
      `/api/jobs/${encodeURIComponent(jobId)}/tasks/${encodeURIComponent(taskId)}/${action}`,
      { method: 'POST' },
    );
    applySnapshot(snapshot);
  } catch (err) {
    if (err instanceof ApiError && err.status === 409) {
      toast(err.detail, 'warn', 3600);
    } else {
      toast(`Could not ${action} ${task.label}: ${err.message}`, 'err');
    }
  }
}

async function runDemo() {
  const buttons = [el.btnDemo, el.btnRerun];
  buttons.forEach((b) => { b.disabled = true; });
  try {
    await api('/api/demo/run', { method: 'POST' });
    // Hide the banner immediately rather than waiting for the next poll, or it
    // lingers for up to 500ms over a demo that has already restarted.
    el.rerunBanner.hidden = true;
    toast('Demo started.', 'ok', 2400);
  } catch (err) {
    toast(`Demo failed: ${err.message}`, 'err');
  } finally {
    buttons.forEach((b) => { b.disabled = false; });
  }
}

/** The seeded run is over in ~35s. Without this, a reviewer arriving late sees
 *  a screen of finished jobs with no indication that anything ever moved. */
function renderRerunBanner() {
  const jobs = state.jobOrder.map((id) => state.jobs.get(id)).filter(Boolean);
  el.rerunBanner.hidden = !(
    jobs.length > 0 && jobs.every((job) => job.state === 'finished')
  );
}

/* ── Render ────────────────────────────────────────────────── */

function render() {
  renderJobs();
  renderRerunBanner();
  renderDag();
  renderEvents();
}

/* ── Boot ──────────────────────────────────────────────────── */

el.btnCompute.addEventListener('click', () => createJob('compute_rack'));
el.btnStorage.addEventListener('click', () => createJob('storage_rack'));
el.btnDemo.addEventListener('click', runDemo);
el.btnRerun.addEventListener('click', runDemo);

// Turning "follow" back on should jump to the newest line immediately.
el.followToggle.addEventListener('change', () => {
  if (el.followToggle.checked) el.eventLog.scrollTop = el.eventLog.scrollHeight;
});

pollLoop();
