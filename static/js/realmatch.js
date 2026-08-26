// 試合観戦（本番用簡易サーバー procon-server）ビュー。
// このページに操作機能は無く、/api/real/* をポーリングして進行状況を表示するだけ。
// 起動・停止ボタンはこの FastAPI アプリ自身に procon-server サブプロセスの
// 起動/停止を依頼するためのもの（試合そのものへの操作ではない）。

import { hexCenter, hexPointsAttr, boardSize, idToRowCol } from './hex.js';
import { teamColor, ROAD_STATE_COLOR, ROAD_STATE_LABEL, TERRAIN_LABEL, AGENT_TYPE_LABEL } from './palette.js';

const $ = (id) => document.getElementById(id);
const POLL_MS = 1500;
const HEX = 26;
const SVG_NS = 'http://www.w3.org/2000/svg';

// spectator.py と同じ並び順（サーバー側のコード値 -> 名前）
const TERRAIN_BY_CODE = ['plain', 'road', 'mountain', 'pond'];
const ROAD_BY_CODE = ['smooth', 'congested', 'jammed'];

function el(name, attrs = {}) {
  const node = document.createElementNS(SVG_NS, name);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  return node;
}

const svg = $('board-svg');
const tooltip = $('tooltip');

const state = {
  status: null, // 直近の /api/real/status レスポンス
  settingKey: null, // 静的レイヤーを再構築すべきか判定するためのキー
  selectedDay: null,
  followLatest: true,
  selectedTeam: null, // 「推測経路」欄・軌跡ハイライトの対象チーム
  selectedAgent: null, // 選択中チーム内で軌跡をハイライト表示するエージェント番号
};

// セルID -> "(x,y)" 表記（x=列, y=行）
function cellXY(cell, width) {
  const { row, col } = idToRowCol(cell, width);
  return `(${col},${row})`;
}

let viewport = null;
let layers = null;
let cellEls = [];

// ----- パン・ズーム（render.js と同じ挙動） -----

let zoom = 1;
let panX = 0;
let panY = 0;

function applyTransform() {
  if (viewport) viewport.setAttribute('transform', `translate(${panX} ${panY}) scale(${zoom})`);
}

function zoomBy(factor) {
  zoom = Math.min(6, Math.max(0.4, zoom * factor));
  applyTransform();
}

function resetView() {
  zoom = 1;
  panX = 0;
  panY = 0;
  applyTransform();
}

(function bindPanZoom() {
  const DRAG_THRESHOLD = 4;
  let pressed = false;
  let dragging = false;
  let downX = 0;
  let downY = 0;
  let lastX = 0;
  let lastY = 0;
  let pointerId = null;
  svg.addEventListener('pointerdown', (e) => {
    pressed = true;
    dragging = false;
    downX = lastX = e.clientX;
    downY = lastY = e.clientY;
    pointerId = e.pointerId;
  });
  svg.addEventListener('pointermove', (e) => {
    if (!pressed) return;
    if (!dragging) {
      if (Math.hypot(e.clientX - downX, e.clientY - downY) < DRAG_THRESHOLD) return;
      dragging = true;
      svg.setPointerCapture(pointerId);
    }
    const rect = svg.getBoundingClientRect();
    const vb = svg.viewBox.baseVal;
    if (!vb || !vb.width) return;
    const scale = vb.width / rect.width;
    panX += (e.clientX - lastX) * scale;
    panY += (e.clientY - lastY) * scale;
    lastX = e.clientX;
    lastY = e.clientY;
    applyTransform();
  });
  const stop = () => {
    pressed = false;
    dragging = false;
  };
  svg.addEventListener('pointerup', stop);
  svg.addEventListener('pointercancel', stop);
  svg.addEventListener('wheel', (e) => { e.preventDefault(); zoomBy(e.deltaY < 0 ? 1.12 : 1 / 1.12); }, { passive: false });
})();

$('btn-zoom-in').addEventListener('click', () => zoomBy(1.25));
$('btn-zoom-out').addEventListener('click', () => zoomBy(1 / 1.25));
$('btn-zoom-reset').addEventListener('click', () => resetView());

window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => {
  renderLegend();
  renderDay();
});

// ----- ツールチップ -----

function showTooltip(evt, html) {
  if (!html) return hideTooltip();
  tooltip.innerHTML = html;
  tooltip.classList.remove('hidden');
  const wrap = svg.parentElement.getBoundingClientRect();
  let tx = evt.clientX - wrap.left + 14;
  let ty = evt.clientY - wrap.top + 10;
  const tw = tooltip.offsetWidth;
  if (tx + tw > wrap.width - 8) tx = evt.clientX - wrap.left - tw - 10;
  tooltip.style.left = `${tx}px`;
  tooltip.style.top = `${ty}px`;
}

function hideTooltip() {
  tooltip.classList.add('hidden');
}

// ----- 静的レイヤー（盤面・スポット）の構築 -----

function buildBoard(setting) {
  const { width, height, cells } = setting.map;
  svg.innerHTML = '';
  zoom = 1;
  panX = 0;
  panY = 0;

  const { w, h } = boardSize(width, height, HEX);
  svg.setAttribute('viewBox', `0 0 ${w} ${h}`);

  viewport = el('g', { id: 'viewport' });
  svg.appendChild(viewport);

  layers = {
    terrain: el('g', { id: 'layer-terrain' }),
    roads: el('g', { id: 'layer-roads' }),
    spots: el('g', { id: 'layer-spots' }),
    paths: el('g', { id: 'layer-paths' }),
    agents: el('g', { id: 'layer-agents' }),
  };
  Object.values(layers).forEach((l) => viewport.appendChild(l));

  cellEls = [];
  for (let id = 0; id < width * height; id++) {
    const row = Math.floor(id / width);
    const col = id % width;
    const terrain = TERRAIN_BY_CODE[cells[row][col]];
    const { x, y } = hexCenter(id, width, HEX);
    const poly = el('polygon', {
      points: hexPointsAttr(x, y, HEX - 0.6),
      class: `cell cell-${terrain}`,
      'data-cell': id,
    });
    poly.addEventListener('mousemove', (e) => showTooltip(e, cellTooltip(id, terrain)));
    poly.addEventListener('mouseleave', hideTooltip);
    layers.terrain.appendChild(poly);
    cellEls.push(poly);
    if (terrain === 'mountain' || terrain === 'pond') {
      const glyph = el('text', { x, y: y + 4, class: 'terrain-glyph', 'text-anchor': 'middle' });
      glyph.textContent = terrain === 'mountain' ? '▲' : '≈';
      layers.terrain.appendChild(glyph);
    }
  }

  for (const spot of setting.spots) {
    const { x, y } = hexCenter(spot.pos, width, HEX);
    const g = el('g', { class: 'spot', 'data-spot': spot.pos });
    g.appendChild(el('circle', { cx: x, cy: y - 2, r: 9.5, class: 'spot-badge', stroke: '#898781' }));
    const label = el('text', { x, y: y + 1.5, class: 'spot-label', 'text-anchor': 'middle' });
    label.textContent = String.fromCharCode(65 + (spot.brand % 26));
    g.appendChild(label);
    layers.spots.appendChild(g);
  }

  applyTransform();
}

function cellTooltip(cell, terrain) {
  const day = currentDayData();
  let html = `<b>セル ${cell}</b> ${TERRAIN_LABEL[terrain]}`;
  if (day) {
    const t = day.traffics.find((tr) => tr.pos === cell);
    if (t && t.status !== 0) {
      const st = ROAD_BY_CODE[t.status];
      html += `（${ROAD_STATE_LABEL[st]}）`;
    }
  }
  const setting = state.status?.setting;
  const spot = setting?.spots?.find((s) => s.pos === cell);
  if (spot) html += `<br>スポット: 系列${spot.brand}（最大在庫 ${spot.stocks}）`;
  return html;
}

// ----- 日ごとの描画（道路状態・スポット・エージェント・軌跡） -----

function currentDayData() {
  const days = state.status?.days ?? [];
  return days.find((d) => d.day === state.selectedDay) ?? null;
}

function renderDay() {
  const day = currentDayData();
  if (!layers) return;

  layers.roads.innerHTML = '';
  layers.paths.innerHTML = '';
  layers.agents.innerHTML = '';
  if (!day) return;

  const width = state.status.setting.map.width;

  // 道路状態オーバーレイ
  for (const t of day.traffics) {
    if (t.status === 0) continue;
    const st = ROAD_BY_CODE[t.status];
    const { x, y } = hexCenter(t.pos, width, HEX);
    const ring = el('polygon', { points: hexPointsAttr(x, y, HEX - 4), class: `road-ring road-ring-${st}`, stroke: ROAD_STATE_COLOR[st] });
    layers.roads.appendChild(ring);
    const mark = el('text', { x, y: y - 8, class: 'road-mark', fill: ROAD_STATE_COLOR[st], 'text-anchor': 'middle' });
    mark.textContent = st === 'jammed' ? '×' : '!';
    layers.roads.appendChild(mark);
  }

  const teams = state.status.teams ?? [];
  for (const team of teams) {
    const tid = team.id;
    const color = teamColor(tid);
    const agents = day.agentsByTeam[tid] ?? [];
    const traj = day.trajectories?.[tid];

    agents.forEach((a, ai) => {
      const row = traj ? traj[ai] : null;
      const endPos = row ? row.end : a.pos;

      // 選択中の車両（推測経路欄でクリックされたもの）だけ、半透明の軌跡を表示する
      if (row && row.start !== row.end && tid === state.selectedTeam && ai === state.selectedAgent) {
        const pts = row.path.map((c) => hexCenter(c, width, HEX));
        const line = el('polyline', {
          points: pts.map((p) => `${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(' '),
          class: 'traj-line traj-line-selected',
          stroke: color,
          fill: 'none',
        });
        layers.paths.appendChild(line);
      }

      // 開始位置（薄い輪）
      const startXY = hexCenter(a.pos, width, HEX);
      const startRing = el('circle', { cx: startXY.x, cy: startXY.y, r: 7, class: 'agent-start-ring', stroke: color });
      layers.paths.appendChild(startRing);

      // 現在（この日終了時点）位置のマーカー
      const { x, y } = hexCenter(endPos, width, HEX);
      const slot = tid * agents.length + ai;
      const angle = (2 * Math.PI * slot) / (teams.length * Math.max(agents.length, 1));
      const ox = Math.cos(angle) * 5;
      const oy = Math.sin(angle) * 5;
      const isSelected = tid === state.selectedTeam && ai === state.selectedAgent;
      const g = el('g', {
        class: `agent agent-${a.kind === 0 ? 'patrol' : 'supply'}${isSelected ? ' agent-selected' : ''}`,
        transform: `translate(${x + ox},${y + oy})`,
      });
      let shape;
      if (a.kind === 0) {
        shape = el('circle', { cx: 0, cy: 0, r: 7 });
      } else {
        shape = el('rect', { x: -6.4, y: -6.4, width: 12.8, height: 12.8, rx: 2, transform: 'rotate(45)' });
      }
      shape.setAttribute('class', 'agent-shape');
      shape.style.fill = color;
      g.appendChild(shape);
      const num = el('text', { x: 0, y: 3.2, class: 'agent-num', 'text-anchor': 'middle' });
      num.textContent = ai + 1;
      g.appendChild(num);
      g.addEventListener('mousemove', (e) => showTooltip(e, agentTooltip(team, a, ai, row)));
      g.addEventListener('mouseleave', hideTooltip);
      layers.agents.appendChild(g);
    });
  }

  renderTrajPanel(day);
}

// ----- 推測経路欄（選択中チームの車両ごとの出発点・到達点・経路） -----

function renderTrajPanel(day) {
  const root = $('traj-panel');
  const teams = state.status?.teams ?? [];
  if (!day || !teams.length) {
    root.innerHTML = '<p class="muted">試合開始後、チームを選択すると表示されます。</p>';
    return;
  }
  const width = state.status.setting.map.width;
  const team = teams.find((t) => t.id === state.selectedTeam) ?? teams[0];
  state.selectedTeam = team.id;

  const isLastKnownDay = day.day === Math.max(...(state.status.days ?? []).map((d) => d.day));
  if (!day.trajectories) {
    const msg = isLastKnownDay
      ? (state.status.phase === 'ended'
          ? `${day.day + 1}日目（最終日）: 次の日のスナップショットが無いため、この日の移動軌跡は観測できません。`
          : `${day.day + 1}日目: 進行中（次の日になるまで、この日の移動軌跡は確定しません）。`)
      : `${day.day + 1}日目: 軌跡データがありません。`;
    root.innerHTML = `<p class="muted">${msg}</p>`;
    return;
  }

  const agents = day.agentsByTeam[team.id] ?? [];
  const rows = agents.map((a, ai) => {
    const row = day.trajectories[team.id]?.[ai];
    if (!row) return '';
    const kindLabel = AGENT_TYPE_LABEL[a.kind === 0 ? 'patrol' : 'supply'];
    const pathCells = row.path.filter((c, i) => i === 0 || c !== row.path[i - 1]);
    const pathStr = pathCells.map((c) => cellXY(c, width)).join(', ');
    const selected = ai === state.selectedAgent;
    return `
      <tr class="traj-row${selected ? ' selected' : ''}" data-agent="${ai}">
        <td>${kindLabel}${ai + 1}</td>
        <td>${cellXY(row.start, width)}</td>
        <td>${cellXY(row.end, width)}</td>
        <td class="traj-path">${pathStr}</td>
      </tr>`;
  }).join('');

  root.innerHTML = `
    <div class="traj-table-wrap">
      <table class="traj-table">
        <thead><tr><th>車両</th><th>出発点</th><th>到達点</th><th>経路</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>`;

  root.querySelectorAll('.traj-row').forEach((tr) => {
    tr.addEventListener('click', () => {
      const ai = Number(tr.dataset.agent);
      state.selectedAgent = state.selectedAgent === ai ? null : ai;
      renderDay();
    });
  });
}

function agentTooltip(team, agent, ai, row) {
  const kindLabel = AGENT_TYPE_LABEL[agent.kind === 0 ? 'patrol' : 'supply'];
  let html = `<b>${team.name}</b> ${kindLabel}${ai + 1}`;
  if (agent.kind === 0) html += `<br>燃料 ${agent.fuel}`;
  if (row) {
    html += `<br>開始セル ${row.start} → 終了セル ${row.end}`;
  } else {
    html += `<br>セル ${agent.pos}（日開始時点）`;
  }
  return html;
}

// ----- 日タブ -----

function renderDayTabs() {
  const root = $('day-tabs');
  const days = state.status?.days ?? [];
  root.innerHTML = '';
  for (const d of days) {
    const btn = document.createElement('button');
    btn.className = 'day-tab' + (d.day === state.selectedDay ? ' active' : '');
    btn.textContent = `${d.day + 1}日目`;
    btn.addEventListener('click', () => {
      state.selectedDay = d.day;
      state.followLatest = false;
      renderDayTabs();
      renderDay();
    });
    root.appendChild(btn);
  }
}

$('btn-latest').addEventListener('click', () => {
  state.followLatest = true;
  const days = state.status?.days ?? [];
  if (days.length) state.selectedDay = days[days.length - 1].day;
  renderDayTabs();
  renderDay();
});

// ----- サイドパネル -----

function renderTeamList() {
  const root = $('teamlist');
  const teams = state.status?.teams ?? [];
  if (!teams.length) {
    root.innerHTML = '<p class="muted">試合開始後に表示されます。</p>';
    return;
  }
  if (state.selectedTeam == null) state.selectedTeam = teams[0].id;

  root.innerHTML = teams
    .map((t) => {
      const focused = t.id === state.selectedTeam ? ' focused' : '';
      return `<button type="button" class="score-row${focused}" data-team="${t.id}"><span class="chip" style="background:${teamColor(t.id)}"></span><span class="team-name">${t.name}</span></button>`;
    })
    .join('');

  root.querySelectorAll('.score-row').forEach((btn) => {
    btn.addEventListener('click', () => {
      const tid = Number(btn.dataset.team);
      if (tid === state.selectedTeam) return;
      state.selectedTeam = tid;
      state.selectedAgent = null;
      renderTeamList();
      renderDay();
    });
  });
}

function renderSettingInfo() {
  const root = $('setting-info');
  const setting = state.status?.setting;
  if (!setting) {
    root.innerHTML = '<p class="muted">試合開始後に表示されます。</p>';
    return;
  }
  const rows = [
    ['盤面', `${setting.map.width} × ${setting.map.height}`],
    ['日数', state.status.numDays],
    ['1日の秒数', setting.daySeconds?.join(', ')],
    ['プレイヤー数(チーム)', setting.players],
    ['燃料上限', setting.fuelLimits],
  ];
  root.innerHTML = `<table class="detail-table">${rows.map(([k, v]) => `<tr><th>${k}</th><td>${v}</td></tr>`).join('')}</table>`;
}

function renderLegend() {
  const root = $('legend');
  const terrainRows = ['plain', 'road', 'mountain', 'pond']
    .map((t) => `<span class="legend-item"><span class="swatch terrain-swatch-${t}"></span>${TERRAIN_LABEL[t]}</span>`)
    .join('');
  const teams = state.status?.teams ?? [];
  const teamRows = teams
    .map((t) => `<span class="legend-item"><span class="chip" style="background:${teamColor(t.id)}"></span>${t.name}</span>`)
    .join('');
  root.innerHTML = `
    <div class="legend-group"><h3>地形</h3>${terrainRows}</div>
    ${teamRows ? `<div class="legend-group"><h3>チーム</h3>${teamRows}</div>` : ''}
    <div class="legend-group"><h3>凡例</h3>
      <span class="legend-item">半透明の実線: 選択中の車両の推測経路</span>
      <span class="legend-item">薄い輪: 日開始時点の位置</span>
    </div>`;
}

// ----- ステータス行・起動/停止ボタン -----

const PHASE_LABEL = {
  connecting: '接続中…',
  waiting: 'エージェント種別受付・開始待ち…',
  running: '試合進行中',
  ended: '試合終了',
};

function renderStatusLine() {
  const s = state.status;
  const el = $('real-status');
  if (!s || !s.started) {
    el.textContent = '試合サーバーは停止しています。';
    return;
  }
  const phase = PHASE_LABEL[s.phase] ?? s.phase;
  const dayInfo = s.currentDay != null ? ` — ${s.currentDay + 1}日目 / ${s.numDays}日`: '';
  const errInfo = s.error && s.phase !== 'ended' ? `（${s.error}）` : '';
  el.textContent = `${phase}${dayInfo}${errInfo}`;
}

function updateButtons() {
  const s = state.status;
  const running = !!s?.processAlive;
  $('btn-start').disabled = running;
  $('btn-stop').disabled = !running;
}

$('btn-start').addEventListener('click', async () => {
  $('btn-start').disabled = true;
  try {
    const res = await fetch('./api/real/start', { method: 'POST' });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      alert(`起動に失敗しました: ${body.detail ?? res.status}`);
    }
  } catch (e) {
    alert(`起動に失敗しました: ${e}`);
  }
  await poll();
});

$('btn-stop').addEventListener('click', async () => {
  $('btn-stop').disabled = true;
  try {
    await fetch('./api/real/stop', { method: 'POST' });
  } catch {
    // 通信エラーは無視（次のポーリングで状態を反映）
  }
  await poll();
});

// ----- ポーリング -----

function settingKeyOf(setting) {
  if (!setting) return null;
  return `${setting.map.width}x${setting.map.height}:${setting.startsAt}`;
}

async function poll() {
  try {
    const res = await fetch('./api/real/status');
    if (res.ok) {
      state.status = await res.json();
      const key = settingKeyOf(state.status.setting);
      if (key && key !== state.settingKey) {
        state.settingKey = key;
        buildBoard(state.status.setting);
        state.selectedDay = null;
        state.followLatest = true;
      }
      if (!state.status.setting) {
        state.settingKey = null;
      }
      const days = state.status.days ?? [];
      if (days.length) {
        $('board-empty').classList.add('hidden');
        if (state.followLatest || state.selectedDay == null) {
          state.selectedDay = days[days.length - 1].day;
        }
      } else {
        $('board-empty').classList.remove('hidden');
      }
      renderStatusLine();
      updateButtons();
      renderTeamList();
      renderSettingInfo();
      renderLegend();
      renderDayTabs();
      renderDay();
    }
  } catch {
    // サーバー未起動・一時的な通信断は無視して次回ポーリングで再試行
  }
}

renderLegend();
poll();
setInterval(poll, POLL_MS);
