// 試合観戦ビュー（描画本体）。
//
// 2つのデータ源を同じ見た目で表示するための共通モジュール。
//   /api/real/*  公式配布の簡易サーバー procon-server（軌跡は推定）
//   /api/sim/*   公式ルール忠実シミュレーター simulator/（軌跡は実測・ステップ再生あり）
//
// どちらも `visualizer/spectator.py` と `visualizer/sim_spectator.py` が
// 同じ形の JSON を返すので、違いは apiBase と操作ボタンの中身だけになる。
//
// createMatchView(options) の options:
//   apiBase        必須。'./api/real' など。start/stop/status を生やす前置き
//   startQuery     () => string。起動 POST に付けるクエリ（省略可）
//   isRunning      (status) => bool。停止ボタンを有効にするか
//   emptyMessage   盤面が無いときの案内（DOM 側に書いてあるので通常は不要）
//   noTrajMessage  (day, status) => string。軌跡が無い日の説明
//   phaseLabel     { phase文字列: 表示名 }
//   idleMessage    未起動時のステータス行

import { hexCenter, hexPointsAttr, boardSize, idToRowCol } from './hex.js';
import { teamColor, ROAD_STATE_COLOR, ROAD_STATE_LABEL, TERRAIN_LABEL, AGENT_TYPE_LABEL } from './palette.js';

const $ = (id) => document.getElementById(id);
const HEX = 26;
const SVG_NS = 'http://www.w3.org/2000/svg';

// spectator.py / sim_spectator.py と同じ並び順（サーバー側のコード値 -> 名前）
const TERRAIN_BY_CODE = ['plain', 'road', 'mountain', 'pond'];
const ROAD_BY_CODE = ['smooth', 'congested', 'jammed'];

const DEFAULT_PHASE_LABEL = {
  connecting: '接続中…',
  waiting: 'エージェント種別受付・開始待ち…',
  running: '試合進行中',
  ended: '試合終了',
};

function el(name, attrs = {}) {
  const node = document.createElementNS(SVG_NS, name);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  return node;
}

export function createMatchView(options) {
  const {
    apiBase,
    pollMs = 1500,
    startQuery = () => '',
    isRunning = (s) => !!s?.processAlive,
    noTrajMessage = (day) => `${day.day + 1}日目: 軌跡データがありません。`,
    phaseLabel = DEFAULT_PHASE_LABEL,
    idleMessage = '試合サーバーは停止しています。',
  } = options;

  const svg = $('board-svg');
  const tooltip = $('tooltip');

  const state = {
    status: null, // 直近の status レスポンス
    settingKey: null, // 静的レイヤーを再構築すべきか判定するためのキー
    selectedDay: null,
    followLatest: true,
    selectedTeam: null, // 「経路」欄・軌跡ハイライトの対象チーム
    selectedAgent: null, // 選択中チーム内で軌跡をハイライトするエージェント番号
    step: null, // ステップ再生の現在位置（null = その日の最終状態）
  };

  let viewport = null;
  let layers = null;

  // セルID -> "(x,y)" 表記（x=列, y=行）
  function cellXY(cell, width) {
    const { row, col } = idToRowCol(cell, width);
    return `(${col},${row})`;
  }

  // ----- パン・ズーム -----

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

  function showTooltip(e, html) {
    tooltip.innerHTML = html;
    tooltip.classList.remove('hidden');
    const rect = svg.getBoundingClientRect();
    tooltip.style.left = `${e.clientX - rect.left + 14}px`;
    tooltip.style.top = `${e.clientY - rect.top + 14}px`;
  }

  function hideTooltip() {
    tooltip.classList.add('hidden');
  }

  // ----- 静的レイヤー（盤面・スポット）の構築 -----

  function buildBoard(setting) {
    const { width, height, cells } = setting.map;
    svg.innerHTML = '';
    resetView();

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
        html += `（${ROAD_STATE_LABEL[ROAD_BY_CODE[t.status]]}）`;
      }
      const volume = day.volumes?.[String(cell)];
      if (volume != null) html += `<br>交通量 ${Number(volume).toFixed(2)}`;
    }
    const setting = state.status?.setting;
    const spot = setting?.spots?.find((s) => s.pos === cell);
    if (spot) html += `<br>スポット: 系列${spot.brand}（最大在庫 ${spot.stocks}）`;
    return html;
  }

  // ----- 日・ステップの選択 -----

  function currentDayData() {
    const days = state.status?.days ?? [];
    return days.find((d) => d.day === state.selectedDay) ?? null;
  }

  // ステップ再生できるのはシミュレーター側のデータのみ（steps を持つ）
  function hasSteps(day) {
    return Array.isArray(day?.steps) && day.steps.length > 0;
  }

  function lastStepIndex(day) {
    return hasSteps(day) ? day.steps.length - 1 : 0;
  }

  function currentStep(day) {
    if (!hasSteps(day)) return null;
    const last = lastStepIndex(day);
    return state.step == null ? last : Math.min(state.step, last);
  }

  // その時点でのエージェント配列（ステップ再生中はそのステップの状態）
  function agentsAt(day, teamId) {
    const step = currentStep(day);
    if (step == null) return day.agentsByTeam?.[teamId] ?? [];
    return day.steps[step].agentsByTeam?.[teamId] ?? [];
  }

  // ステップ0から現在ステップまでに通ったセル列（連続する重複は畳む）
  function pathUpTo(day, teamId, agentIndex) {
    const step = currentStep(day);
    if (step == null) return null;
    const out = [];
    for (let s = 0; s <= step; s++) {
      const cell = day.steps[s].agentsByTeam?.[teamId]?.[agentIndex]?.pos;
      if (cell == null) continue;
      if (!out.length || out[out.length - 1] !== cell) out.push(cell);
    }
    return out;
  }

  // ----- 日ごとの描画 -----

  function renderDay() {
    const day = currentDayData();
    if (!layers) return;

    layers.roads.innerHTML = '';
    layers.paths.innerHTML = '';
    layers.agents.innerHTML = '';
    renderStepControls(day);
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
      const startAgents = day.agentsByTeam[tid] ?? [];
      const agents = agentsAt(day, tid);
      const traj = day.trajectories?.[tid];

      agents.forEach((a, ai) => {
        const row = traj ? traj[ai] : null;
        const walked = pathUpTo(day, tid, ai);
        const endPos = walked ? a.pos : (row ? row.end : a.pos);
        const isSelected = tid === state.selectedTeam && ai === state.selectedAgent;

        // 選択中の車両だけ、半透明の軌跡を表示する
        const linePath = walked ?? (row && row.start !== row.end ? row.path : null);
        if (isSelected && linePath && linePath.length > 1) {
          const pts = linePath.map((c) => hexCenter(c, width, HEX));
          const line = el('polyline', {
            points: pts.map((p) => `${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(' '),
            class: 'traj-line traj-line-selected',
            stroke: color,
            fill: 'none',
          });
          layers.paths.appendChild(line);
        }

        // 日開始時点の位置（薄い輪）
        const startPos = startAgents[ai]?.pos ?? a.pos;
        const startXY = hexCenter(startPos, width, HEX);
        layers.paths.appendChild(
          el('circle', { cx: startXY.x, cy: startXY.y, r: 7, class: 'agent-start-ring', stroke: color })
        );

        // 現在位置のマーカー
        const { x, y } = hexCenter(endPos, width, HEX);
        const slot = tid * agents.length + ai;
        const angle = (2 * Math.PI * slot) / (teams.length * Math.max(agents.length, 1));
        const ox = Math.cos(angle) * 5;
        const oy = Math.sin(angle) * 5;
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
        g.addEventListener('mousemove', (e) => showTooltip(e, agentTooltip(day, team, a, ai, row)));
        g.addEventListener('mouseleave', hideTooltip);
        layers.agents.appendChild(g);
      });
    }

    renderTrajPanel(day);
    renderScorePanel(day);
  }

  // ----- ステップ再生の操作列 -----

  function renderStepControls(day) {
    const root = $('step-controls');
    if (!root) return;
    if (!hasSteps(day)) {
      root.classList.add('hidden');
      return;
    }
    root.classList.remove('hidden');
    const last = lastStepIndex(day);
    const step = currentStep(day);
    const slider = $('step-slider');
    const label = $('step-label');
    if (slider.max !== String(last)) slider.max = String(last);
    if (Number(slider.value) !== step) slider.value = String(step);
    // 0ステップ目はアクションのみ、最終ステップは反映のみ〔Q6〕〔補足〕
    const note = step === 0 ? '（アクションのみ）' : step === last ? '（反映のみ・日終了）' : '';
    label.textContent = `${step} / ${last} ステップ ${note}`;
  }

  function bindStepControls() {
    const slider = $('step-slider');
    if (!slider) return;
    slider.addEventListener('input', () => {
      state.step = Number(slider.value);
      renderDay();
    });
    $('btn-step-prev')?.addEventListener('click', () => {
      const day = currentDayData();
      const step = currentStep(day);
      if (step == null) return;
      state.step = Math.max(0, step - 1);
      renderDay();
    });
    $('btn-step-next')?.addEventListener('click', () => {
      const day = currentDayData();
      const step = currentStep(day);
      if (step == null) return;
      state.step = Math.min(lastStepIndex(day), step + 1);
      renderDay();
    });
    $('btn-step-end')?.addEventListener('click', () => {
      state.step = null;
      renderDay();
    });
    let timer = null;
    $('btn-step-play')?.addEventListener('click', () => {
      const btn = $('btn-step-play');
      if (timer) {
        clearInterval(timer);
        timer = null;
        btn.textContent = '▶ 再生';
        return;
      }
      btn.textContent = '⏸ 停止';
      const day0 = currentDayData();
      if (currentStep(day0) === lastStepIndex(day0)) state.step = 0;
      timer = setInterval(() => {
        const day = currentDayData();
        const step = currentStep(day);
        if (step == null || step >= lastStepIndex(day)) {
          clearInterval(timer);
          timer = null;
          btn.textContent = '▶ 再生';
          return;
        }
        state.step = step + 1;
        renderDay();
      }, 320);
    });
  }

  // ----- 経路欄 -----

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

    if (!day.trajectories) {
      root.innerHTML = `<p class="muted">${noTrajMessage(day, state.status)}</p>`;
      return;
    }

    const rejected = day.rejected?.[String(team.id)];
    const agents = day.agentsByTeam[team.id] ?? [];
    const rows = agents.map((a, ai) => {
      const row = day.trajectories[team.id]?.[ai];
      if (!row) return '';
      const kindLabel = AGENT_TYPE_LABEL[a.kind === 0 ? 'patrol' : 'supply'];
      const walked = pathUpTo(day, team.id, ai);
      const cells = walked ?? row.path.filter((c, i) => i === 0 || c !== row.path[i - 1]);
      const pathStr = cells.map((c) => cellXY(c, width)).join(', ');
      const selected = ai === state.selectedAgent;
      return `
      <tr class="traj-row${selected ? ' selected' : ''}" data-agent="${ai}">
        <td>${kindLabel}${ai + 1}</td>
        <td>${cellXY(row.start, width)}</td>
        <td>${cellXY(cells[cells.length - 1], width)}</td>
        <td class="traj-path">${pathStr}</td>
      </tr>`;
    }).join('');

    root.innerHTML = `
    ${rejected ? `<p class="muted">⚠ この日の回答はリジェクトされました（全員待機）: ${rejected}</p>` : ''}
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

  // ----- 得点欄（シミュレーター側だけが持つ） -----

  function renderScorePanel(day) {
    const root = $('score-panel');
    if (!root) return;
    const scores = day?.scores;
    if (!scores) {
      root.innerHTML = '<p class="muted">実行後に表示されます。</p>';
      return;
    }
    const teams = state.status?.teams ?? [];
    const nameOf = (tid) => teams.find((t) => t.id === tid)?.name ?? `Player ${tid}`;
    const rows = scores
      .map(
        (s) => `<tr><td><span class="chip" style="background:${teamColor(s.teamId)}"></span>${nameOf(s.teamId)}</td>
          <td>${s.brandCount}</td><td>${s.dailyCumulative}</td><td>${s.totalUdon}</td></tr>`
      )
      .join('');
    root.innerHTML = `
      <table class="detail-table">
        <thead><tr><th>チーム</th><th>①種類</th><th>②累積</th><th>③玉</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
      <p class="hint">${day.day + 1}日目終了時点。勝敗は ①種類数 → ②日ごと種類数の累積 → ③玉数 → ④回答時間 の順。</p>`;
  }

  function agentTooltip(day, team, agent, ai, row) {
    const kindLabel = AGENT_TYPE_LABEL[agent.kind === 0 ? 'patrol' : 'supply'];
    let html = `<b>${team.name}</b> ${kindLabel}${ai + 1}`;
    if (agent.kind === 0) html += `<br>燃料 ${agent.fuel}`;
    const step = currentStep(day);
    if (step != null) {
      html += `<br>セル ${agent.pos}（${step} ステップ目）`;
    } else if (row) {
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
        state.step = null;
        renderDayTabs();
        renderDay();
      });
      root.appendChild(btn);
    }
  }

  $('btn-latest').addEventListener('click', () => {
    state.followLatest = true;
    state.step = null;
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
        const strategy = t.strategy ? `<span class="muted"> — ${t.strategy}</span>` : '';
        return `<button type="button" class="score-row${focused}" data-team="${t.id}"><span class="chip" style="background:${teamColor(t.id)}"></span><span class="team-name">${t.name}${strategy}</span></button>`;
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
      ['1日のステップ数', setting.daySteps?.join(', ')],
      ['1日の秒数', setting.daySeconds?.join(', ')],
      ['プレイヤー数(チーム)', setting.players],
      ['燃料上限', setting.fuelLimits],
      ['混雑/渋滞基準値', `${setting.busyThreshold} / ${setting.jammedThreshold}`],
    ];
    root.innerHTML = `<table class="detail-table">${rows.map(([k, v]) => `<tr><th>${k}</th><td>${v}</td></tr>`).join('')}</table>`;
    const policies = state.status?.policies;
    const pol = $('policy-info');
    if (pol && policies) {
      pol.innerHTML = `<pre class="policy-list">${policies.join('\n')}</pre>`;
    }
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
    const trajLabel = state.status?.exact
      ? '半透明の実線: 選択中の車両の実測経路'
      : '半透明の実線: 選択中の車両の推測経路';
    root.innerHTML = `
    <div class="legend-group"><h3>地形</h3>${terrainRows}</div>
    ${teamRows ? `<div class="legend-group"><h3>チーム</h3>${teamRows}</div>` : ''}
    <div class="legend-group"><h3>凡例</h3>
      <span class="legend-item">${trajLabel}</span>
      <span class="legend-item">薄い輪: 日開始時点の位置</span>
    </div>`;
  }

  // ----- ステータス行・起動/停止ボタン -----

  function renderStatusLine() {
    const s = state.status;
    const node = $('real-status');
    if (!s || !s.started) {
      node.textContent = idleMessage;
      return;
    }
    const phase = phaseLabel[s.phase] ?? s.phase;
    const dayInfo = s.currentDay != null ? ` — ${s.currentDay + 1}日目 / ${s.numDays}日` : '';
    const errInfo = s.error && s.phase !== 'ended' ? `（${s.error}）` : '';
    node.textContent = `${phase}${dayInfo}${errInfo}`;
  }

  function updateButtons() {
    const running = isRunning(state.status);
    $('btn-start').disabled = running;
    $('btn-stop').disabled = !running;
  }

  $('btn-start').addEventListener('click', async () => {
    $('btn-start').disabled = true;
    try {
      const res = await fetch(`${apiBase}/start${startQuery()}`, { method: 'POST' });
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
      await fetch(`${apiBase}/stop`, { method: 'POST' });
    } catch {
      // 通信エラーは無視（次のポーリングで状態を反映）
    }
    state.step = null;
    await poll();
  });

  // ----- ポーリング -----

  function settingKeyOf(setting) {
    if (!setting) return null;
    return `${setting.map.width}x${setting.map.height}:${setting.key ?? setting.startsAt}`;
  }

  async function poll() {
    try {
      const res = await fetch(`${apiBase}/status`);
      if (!res.ok) return;
      state.status = await res.json();
      const key = settingKeyOf(state.status.setting);
      if (key && key !== state.settingKey) {
        state.settingKey = key;
        buildBoard(state.status.setting);
        state.selectedDay = null;
        state.followLatest = true;
        state.step = null;
      }
      if (!state.status.setting) state.settingKey = null;

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
    } catch {
      // サーバー未起動・一時的な通信断は無視して次回ポーリングで再試行
    }
  }

  bindStepControls();
  renderLegend();
  poll();
  if (pollMs > 0) setInterval(poll, pollMs);

  return { poll, state };
}
