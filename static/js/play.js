// ライブ対戦を GUI から操作するクライアント。
// /api/live/new → /api/agents → /api/match/{day} → /api/actions を
// ブラウザの操作（クリックで移動）だけで叩けるようにする。

import { applyDirection } from './hex.js';
import { BoardRenderer } from './render.js';
import { teamColor, AGENT_TYPE_LABEL, TERRAIN_LABEL } from './palette.js';

const $ = (id) => document.getElementById(id);

const TERRAIN_BY_CODE = ['plain', 'road', 'mountain', 'pond'];
const ROAD_BY_CODE = ['smooth', 'congested', 'jammed'];
const STEP_COST = { plain: 2, mountain: 3, road: { smooth: 1, congested: 2, jammed: 4 } };
const FUEL_COST = { plain: 1, mountain: 2, road: 2 };
const STATUS_LABEL = {
  waiting_agents: 'エージェント種別 回答待ち',
  waiting_actions: '行動計画 回答待ち',
  finished: '試合終了',
};

const state = {
  match: null, // GET /api/match（試合中は不変）
  meta: null, // GET /api/replay の meta（チーム名など、ラベル用）
  live: null, // GET /api/live
  day: null, // GET /api/match/{day}（現在受付中の日）
  agents: [], // 行動計画ビルダーの状態（自チームのみ）
  armed: null, // 現在選択中のエージェント index
  loadingDay: -1,
};

const renderer = new BoardRenderer($('board-svg'), $('tooltip'), {
  onSelect: onBoardSelect,
  getTooltip: buildTooltip,
});

$('btn-zoom-in').addEventListener('click', () => renderer.zoomBy(1.25));
$('btn-zoom-out').addEventListener('click', () => renderer.zoomBy(1 / 1.25));
$('btn-zoom-reset').addEventListener('click', () => renderer.resetView());

// ----- API -----

async function api(method, path, body) {
  const res = await fetch(path, {
    method,
    headers: body !== undefined ? { 'Content-Type': 'application/json' } : undefined,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  const text = await res.text();
  const data = text ? JSON.parse(text) : null;
  if (!res.ok) {
    const err = new Error(data?.detail ?? `HTTP ${res.status}`);
    err.status = res.status;
    throw err;
  }
  return data;
}

// ----- 地形・コスト -----

function terrainAt(match, cell) {
  const { width, cells } = match.map;
  const r = Math.floor(cell / width);
  const c = cell % width;
  return TERRAIN_BY_CODE[cells[r][c]];
}

function costOf(match, trafficMap, cell) {
  const terrain = terrainAt(match, cell);
  if (terrain === 'pond') return null;
  if (terrain === 'road') {
    const state = ROAD_BY_CODE[trafficMap.get(cell) ?? 0];
    return { step: STEP_COST.road[state], fuel: FUEL_COST.road, terrain, state };
  }
  return { step: STEP_COST[terrain], fuel: FUEL_COST[terrain], terrain };
}

// ----- 開始フォーム -----

$('setup-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const fd = new FormData(e.target);
  const params = new URLSearchParams();
  for (const [k, v] of fd) if (v !== '') params.set(k, v);
  try {
    await api('POST', `/api/live/new?${params}`);
    state.match = null;
    state.meta = null;
    state.day = null;
    await refreshLive();
  } catch (err) {
    alert(`開始に失敗しました: ${err.message}`);
  }
});

$('btn-restart').addEventListener('click', () => {
  $('finished-card').classList.add('hidden');
  $('setup-card').classList.remove('hidden');
});

// ----- 進行状況の取得・画面切り替え -----

async function refreshLive() {
  state.live = await api('GET', '/api/live');
  render();
}

async function ensureMatch() {
  if (state.match) return;
  state.match = await api('GET', '/api/match');
}

async function ensureMeta() {
  try {
    const bundle = await api('GET', '/api/replay');
    state.meta = bundle.meta;
  } catch {
    state.meta = { teamNames: [], seriesNames: [] };
  }
}

function render() {
  const live = state.live;
  $('setup-card').classList.toggle('hidden', !!(live && live.live));
  $('kind-card').classList.toggle('hidden', !(live?.live && live.status === 'waiting_agents'));
  $('plan-card').classList.toggle('hidden', !(live?.live && live.status === 'waiting_actions'));
  $('finished-card').classList.toggle('hidden', !(live?.live && live.status === 'finished'));

  if (!live || !live.live) {
    $('status-line').textContent = 'ライブ対戦は未開始（右側のフォームから開始）';
    $('board-empty').classList.remove('hidden');
    renderStandings($('standings'), []);
    return;
  }

  $('status-line').textContent =
    `seed=${live.seed} ／ ${live.day + 1}/${live.numDays} 日目 ／ ${STATUS_LABEL[live.status] ?? live.status}`;
  renderStandings($('standings'), live.standings, live.solo);

  if (live.status === 'waiting_agents') {
    $('board-empty').classList.remove('hidden');
    ensureMatch().then(renderKindForm);
  } else if (live.status === 'waiting_actions') {
    $('board-empty').classList.add('hidden');
    if (state.loadingDay !== live.day) loadDay(live.day);
  } else if (live.status === 'finished') {
    $('board-empty').classList.add('hidden');
    renderStandings($('final-standings'), live.standings, live.solo);
  }
}

function renderStandings(root, standings, solo) {
  if (!standings || !standings.length) {
    root.innerHTML = '<p class="muted">-</p>';
    return;
  }
  root.innerHTML = standings
    .map(
      (s) => `<div class="standing-row">
        <span class="chip" style="background:${teamColor(s.team)}"></span>
        ${solo ? '' : `<b>${s.rank}位</b> `}${s.name}
        <span class="muted">（${s.seriesCount}種／累積${s.dailySeriesCum}／${s.totalUdon}玉）</span>
      </div>`
    )
    .join('');
}

// ----- エージェント種別フォーム -----

function renderKindForm() {
  const n = state.match.agents.length;
  $('kind-list').innerHTML = Array.from({ length: n }, (_, i) => {
    const defaultKind = i === n - 1 ? 1 : 0; // 例と同じく最後の1台を補給車に
    return `<div class="kind-row">
      <span>#${i}（開始位置 ${state.match.agents[i]}）</span>
      <select data-idx="${i}">
        <option value="0" ${defaultKind === 0 ? 'selected' : ''}>巡回車</option>
        <option value="1" ${defaultKind === 1 ? 'selected' : ''}>補給車</option>
      </select>
    </div>`;
  }).join('');
}

$('btn-submit-kinds').addEventListener('click', async () => {
  const kinds = [...$('kind-list').querySelectorAll('select')].map((s) => Number(s.value));
  try {
    await api('POST', '/api/agents', kinds);
    await refreshLive();
  } catch (err) {
    alert(`種別の提出に失敗しました: ${err.message}`);
  }
});

// ----- 日の読み込み・盤面初期化 -----

let boardReady = false;

async function loadDay(day) {
  state.loadingDay = day;
  await ensureMatch();
  if (!state.meta) await ensureMeta();
  state.day = await api('GET', `/api/match/${day}`);
  state.armed = null;

  const numTeams = 1 + state.day.others.length;
  const agentKindsAll = [
    state.day.agents.map((a) => (a.kind === 0 ? 'patrol' : 'supply')),
    ...state.day.others.map((o) => o.agents.map((a) => (a.kind === 0 ? 'patrol' : 'supply'))),
  ];
  const { width, height, cells } = state.match.map;
  const terrain = [];
  for (let r = 0; r < height; r++) {
    for (let c = 0; c < width; c++) terrain.push(TERRAIN_BY_CODE[cells[r][c]]);
  }
  const spots = new Map(state.match.spots.map((s) => [s.pos, { brand: s.brand, stocks: s.stocks }]));

  if (!boardReady) {
    renderer.setMatch({
      width,
      height,
      terrain,
      spots,
      numTeams,
      agentKinds: agentKindsAll,
      teamNames: state.meta.teamNames?.length ? state.meta.teamNames : agentKindsAll.map((_, i) => `チーム${i}`),
      seriesNames: state.meta.seriesNames ?? [],
      fuelLimits: state.match.fuelLimits,
    });
    boardReady = true;
  }

  const trafficMap = new Map(state.day.traffics.map((t) => [t.pos, t.status]));
  const roadStates = new Map(
    state.day.traffics.map((t) => [t.pos, ROAD_BY_CODE[t.status]])
  );
  renderer.setRoadStates(roadStates);
  state.trafficMap = trafficMap;

  const daySteps = state.match.daySteps[day];
  state.agents = state.day.agents.map((a, i) => ({
    idx: i,
    kind: a.kind === 0 ? 'patrol' : 'supply',
    startPos: a.pos,
    virtualPos: a.pos,
    fuel: a.fuel,
    remaining: daySteps,
    codes: [],
    history: [],
  }));
  $('plan-day-label').textContent = `${day + 1}日目・全${daySteps}ステップ`;

  drawFrame();
  renderAgentPanel();
}

function otherStaticFrame() {
  return state.day.others.map((o) => o.agents.map((a) => ({ cell: a.pos, fuel: a.fuel })));
}

function drawFrame() {
  const stocks = new Map(state.match.spots.map((s) => [s.pos, s.stocks]));
  const frame = [state.agents.map((a) => ({ cell: a.virtualPos, fuel: a.fuel })), ...otherStaticFrame()];
  renderer.update(frame, stocks, 0);
  highlightArmable();
}

function highlightArmable() {
  renderer.cellEls?.forEach((poly) => poly.classList.remove('cell-armable'));
  if (state.armed === null) return;
  const agent = state.agents[state.armed];
  if (!agent || agent.remaining <= 0) return;
  const { width, height } = state.match.map;
  // 移動コストは出発セル（現在地）の地形で決まる。目的地の地形はコストに関係ない。
  const cost = costOf(state.match, state.trafficMap, agent.virtualPos);
  if (cost === null || cost.step > agent.remaining) return;
  for (let d = 0; d < 6; d++) {
    const nb = applyDirection(agent.virtualPos, d, width, height);
    if (nb === null) continue;
    if (terrainAt(state.match, nb) === 'pond') continue; // 目的地は池不可
    renderer.cellEls[nb]?.classList.add('cell-armable');
  }
}

// ----- 盤面クリック -----

function onBoardSelect(sel) {
  if (sel.type === 'agent') {
    if (sel.team !== 0) return; // AIチームは操作対象外
    armAgent(sel.agent);
    return;
  }
  if (sel.type === 'cell' && state.armed !== null) {
    tryMove(sel.cell);
  }
}

function armAgent(idx) {
  state.armed = idx;
  drawFrame();
  renderAgentPanel();
}

function tryMove(targetCell) {
  const agent = state.agents[state.armed];
  if (!agent || agent.remaining <= 0) return;
  const { width, height } = state.match.map;
  let dir = null;
  for (let d = 0; d < 6; d++) {
    if (applyDirection(agent.virtualPos, d, width, height) === targetCell) {
      dir = d;
      break;
    }
  }
  if (dir === null) return; // 隣接セルでない
  if (terrainAt(state.match, targetCell) === 'pond') return alert('池には移動できません');
  // 移動コストは出発セル（現在地）の地形で決まる。
  const cost = costOf(state.match, state.trafficMap, agent.virtualPos);
  if (cost.step > agent.remaining) {
    return alert(`残り${agent.remaining}ステップでは移動できません（必要${cost.step}ステップ）`);
  }
  agent.history.push({ remaining: agent.remaining, virtualPos: agent.virtualPos, fuel: agent.fuel });
  agent.codes.push(dir);
  agent.remaining -= cost.step;
  agent.virtualPos = targetCell;
  if (agent.kind === 'patrol') agent.fuel = Math.max(0, agent.fuel - cost.fuel);
  drawFrame();
  renderAgentPanel();
}

function addWait(idx) {
  const agent = state.agents[idx];
  if (!agent || agent.remaining <= 0) return;
  agent.history.push({ remaining: agent.remaining, virtualPos: agent.virtualPos, fuel: agent.fuel });
  agent.codes.push(-1);
  agent.remaining -= 1;
  drawFrame();
  renderAgentPanel();
}

function fillWait(idx) {
  const agent = state.agents[idx];
  if (!agent || agent.remaining <= 0) return;
  agent.history.push({ remaining: agent.remaining, virtualPos: agent.virtualPos, fuel: agent.fuel });
  agent.codes.push(-agent.remaining);
  agent.remaining = 0;
  drawFrame();
  renderAgentPanel();
}

function undo(idx) {
  const agent = state.agents[idx];
  if (!agent || !agent.codes.length) return;
  agent.codes.pop();
  Object.assign(agent, agent.history.pop());
  drawFrame();
  renderAgentPanel();
}

// ----- サイドパネル: エージェント一覧 -----

function renderAgentPanel() {
  const root = $('agent-list');
  root.innerHTML = state.agents
    .map((a) => {
      const done = a.remaining === 0;
      const planText = a.codes.length ? a.codes.join(' ') : '(未計画)';
      return `<div class="agent-card ${state.armed === a.idx ? 'armed' : ''} ${done ? 'done' : ''}" data-idx="${a.idx}">
        <div class="agent-card-head">
          <button class="btn btn-icon agent-pick" data-idx="${a.idx}">#${a.idx}</button>
          <span>${AGENT_TYPE_LABEL[a.kind]}</span>
          ${a.kind === 'patrol' ? `<span class="muted">燃料${a.fuel}</span>` : ''}
          <span class="agent-remaining ${done ? 'ok' : ''}">残${a.remaining}</span>
        </div>
        <div class="plan-code">${planText}</div>
        <div class="agent-card-actions">
          <button class="btn btn-icon" data-act="wait" data-idx="${a.idx}" ${done ? 'disabled' : ''}>待機+1</button>
          <button class="btn btn-icon" data-act="fill" data-idx="${a.idx}" ${done ? 'disabled' : ''}>残り待機</button>
          <button class="btn btn-icon" data-act="undo" data-idx="${a.idx}" ${a.codes.length ? '' : 'disabled'}>戻す</button>
        </div>
      </div>`;
    })
    .join('');

  root.querySelectorAll('.agent-pick').forEach((btn) =>
    btn.addEventListener('click', () => armAgent(Number(btn.dataset.idx)))
  );
  root.querySelectorAll('[data-act]').forEach((btn) =>
    btn.addEventListener('click', () => {
      const idx = Number(btn.dataset.idx);
      if (btn.dataset.act === 'wait') addWait(idx);
      else if (btn.dataset.act === 'fill') fillWait(idx);
      else if (btn.dataset.act === 'undo') undo(idx);
    })
  );

  const allDone = state.agents.every((a) => a.remaining === 0);
  $('btn-submit-actions').disabled = !allDone;
}

$('btn-submit-actions').addEventListener('click', async () => {
  const day = state.day.day;
  const plans = state.agents.map((a) => a.codes);
  $('btn-submit-actions').disabled = true;
  try {
    await api('POST', `/api/actions?day=${day}`, plans);
    await refreshLive();
  } catch (err) {
    alert(`行動計画の提出に失敗しました: ${err.message}`);
    $('btn-submit-actions').disabled = false;
  }
});

// ----- ツールチップ -----

function buildTooltip(cell, agentRef) {
  if (!state.match || !state.day) return null;
  if (agentRef) {
    if (agentRef.team === 0) {
      const a = state.agents[agentRef.agent];
      return `${AGENT_TYPE_LABEL[a.kind]}#${agentRef.agent}<br>セル ${a.virtualPos}／残${a.remaining}ステップ`;
    }
    const o = state.day.others[agentRef.team - 1].agents[agentRef.agent];
    return `他チーム ${AGENT_TYPE_LABEL[o.kind === 0 ? 'patrol' : 'supply']}<br>セル ${o.pos}`;
  }
  if (cell === null) return null;
  const terrain = terrainAt(state.match, cell);
  let html = `セル ${cell}／${TERRAIN_LABEL[terrain]}`;
  if (terrain === 'road') {
    const st = ROAD_BY_CODE[state.trafficMap?.get(cell) ?? 0];
    html += `（${{ smooth: '順調', congested: '混雑', jammed: '渋滞' }[st]}）`;
  }
  return html;
}

// ----- 初期化 -----

refreshLive();
