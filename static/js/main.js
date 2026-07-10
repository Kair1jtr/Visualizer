// エントリポイント: データ読込・各コンポーネントの結線・サイドパネル描画。

import { buildReplay } from './replay.js';
import { BoardRenderer } from './render.js';
import { Timeline } from './timeline.js';
import {
  teamColor,
  seriesColor,
  ROAD_STATE_LABEL,
  TERRAIN_LABEL,
  AGENT_TYPE_LABEL,
} from './palette.js';

const $ = (id) => document.getElementById(id);

const state = {
  replay: null,
  day: 0,
  step: 0,
  focusTeam: 0, // 在庫バッジをどのチーム視点で表示するか
  selection: null,
  shownEventFrame: -1,
};

const renderer = new BoardRenderer($('board-svg'), $('tooltip'), {
  onSelect: (sel) => {
    state.selection = sel;
    renderInspector();
  },
  getTooltip: buildTooltip,
});

const timeline = new Timeline({
  tabsEl: $('day-tabs'),
  sliderEl: $('step-slider'),
  playBtn: $('btn-play'),
  speedSel: $('speed-select'),
  labelEl: $('step-label'),
  onChange: (day, step) => {
    const dayChanged = day !== state.day;
    state.day = day;
    state.step = step;
    if (dayChanged) renderer.setRoadStates(state.replay.days[day].roadStates);
    refreshBoard();
    renderScoreboard();
    renderTicker();
    if (state.selection) renderInspector();
  },
});

// ----- データ読込 -----

async function loadSample() {
  const btn = $('btn-sample');
  btn.disabled = true;
  btn.textContent = '生成中…';
  try {
    const seed = Math.floor(Math.random() * 1_000_000_000);
    await fetch(`./api/new?seed=${seed}`, { method: 'POST' });
    const res = await fetch('./api/replay');
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    setMatch(await res.json());
  } catch (err) {
    alert(`試合データの取得に失敗しました: ${err.message}`);
  } finally {
    btn.disabled = false;
    btn.textContent = 'サンプル試合を生成';
  }
}

// 再生成せずにサーバーが保持している現在の試合を表示（ライブ対戦の観戦用）
async function loadCurrent() {
  const btn = $('btn-reload');
  btn.disabled = true;
  try {
    const res = await fetch('./api/replay');
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    setMatch(await res.json());
  } catch (err) {
    alert(`試合データの取得に失敗しました: ${err.message}`);
  } finally {
    btn.disabled = false;
  }
}

function setMatch(bundle) {
  if (bundle.format !== 'hexaudon-official-v1' || !bundle.match || !bundle.days) {
    alert('対応していないデータ形式です（hexaudon-official-v1 が必要）');
    return;
  }
  if (!bundle.days.length) {
    alert('まだ完了した日がありません（ライブ対戦で行動計画を提出すると1日ずつ増えます）');
    return;
  }
  state.replay = buildReplay(bundle);
  state.focusTeam = 0;
  state.selection = null;
  $('board-empty').classList.add('hidden');
  renderer.setMatch(state.replay);
  renderer.setRoadStates(state.replay.days[0].roadStates);
  timeline.setMatch(state.replay.days);
  renderLegend();
  renderInspector();
}

$('btn-sample').addEventListener('click', loadSample);
$('btn-reload').addEventListener('click', loadCurrent);
$('file-input').addEventListener('change', async (e) => {
  const file = e.target.files[0];
  if (!file) return;
  try {
    setMatch(JSON.parse(await file.text()));
  } catch (err) {
    alert(`JSONの読み込みに失敗しました: ${err.message}`);
  }
  e.target.value = '';
});

$('btn-zoom-in').addEventListener('click', () => renderer.zoomBy(1.25));
$('btn-zoom-out').addEventListener('click', () => renderer.zoomBy(1 / 1.25));
$('btn-zoom-reset').addEventListener('click', () => renderer.resetView());

window
  .matchMedia('(prefers-color-scheme: dark)')
  .addEventListener('change', () => {
    renderer.refreshColors();
    renderLegend();
    renderScoreboard();
  });

// ----- 盤面更新 -----

// 現在フレームでの注目チームの残在庫 (pos -> 残数)
function stocksAt(day, frame, team) {
  const r = state.replay;
  const stocks = new Map();
  for (const [pos, s] of r.spots) stocks.set(pos, s.stocks);
  for (const ev of r.days[day].events) {
    if (ev.type === 'acquire' && ev.team === team && ev.frame <= frame) {
      stocks.set(ev.cell, stocks.get(ev.cell) - 1);
    }
  }
  return stocks;
}

function refreshBoard() {
  const r = state.replay;
  if (!r) return;
  const frame = r.days[state.day].frames[state.step];
  renderer.update(frame, stocksAt(state.day, state.step, state.focusTeam), state.focusTeam);

  // 現在ステップで発生したイベントをエフェクト表示
  if (state.shownEventFrame !== state.step) {
    state.shownEventFrame = state.step;
    for (const ev of r.days[state.day].events) {
      if (ev.frame === state.step && ev.type === 'acquire') {
        renderer.pulse(ev.cell, teamColor(ev.team));
      }
    }
  }
}

// ----- スコアボード -----

function renderScoreboard() {
  const r = state.replay;
  const root = $('scoreboard');
  if (!r) {
    root.innerHTML = '<p class="muted">試合が読み込まれていません。</p>';
    return;
  }
  const scores = r.days[state.day].scores[state.step];
  const isFinal =
    state.day === r.days.length - 1 && state.step === r.days[state.day].steps;

  const order = [...Array(r.numTeams).keys()].sort((a, b) => {
    const A = scores[a];
    const B = scores[b];
    return (
      B.seriesCount - A.seriesCount ||
      B.dailySeriesCum - A.dailySeriesCum ||
      B.total - A.total ||
      a - b
    );
  });

  const rows = order
    .map((ti, rank) => {
      const s = scores[ti];
      const medal = isFinal ? ['🥇', '🥈', '🥉'][rank] ?? '' : '';
      return `
      <button class="score-row ${ti === state.focusTeam ? 'focused' : ''}" data-team="${ti}">
        <span class="chip" style="background:${teamColor(ti)}"></span>
        <span class="team-name">${r.teamNames[ti]}${medal}</span>
        <span class="score-cells">
          <span title="うどんの種類数（勝敗第1条件）"><b>${s.seriesCount}</b>種</span>
          <span title="日ごとの種類数の累積（第2条件）"><b>${s.dailySeriesCum}</b>累積</span>
          <span title="うどんの玉数（第3条件）"><b>${s.total}</b>玉</span>
        </span>
      </button>`;
    })
    .join('');
  root.innerHTML = rows;
  root.querySelectorAll('.score-row').forEach((row) => {
    row.addEventListener('click', () => {
      state.focusTeam = Number(row.dataset.team);
      renderScoreboard();
      refreshBoard();
    });
  });
}

// ----- イベントティッカー -----

function renderTicker() {
  const r = state.replay;
  const root = $('event-ticker');
  if (!r) {
    root.innerHTML = '';
    return;
  }
  const events = r.days[state.day].events
    .filter((ev) => ev.frame <= state.step)
    .slice(-4)
    .reverse();
  root.innerHTML = events
    .map((ev) => {
      const team = `<span class="chip chip-sm" style="background:${teamColor(ev.team)}"></span>${r.teamNames[ev.team]}`;
      if (ev.type === 'acquire') {
        const name = r.seriesNames[ev.brand] ?? `系列${ev.brand}`;
        const star = ev.newOverall ? ' <span class="new-series">NEW</span>' : '';
        return `<div class="tick">[${ev.frame}] ${team} 巡回車${ev.agent + 1} が
          <span class="series-name" style="color:${seriesColor(ev.brand)}">${name}</span> を獲得${star}</div>`;
      }
      return `<div class="tick">[${ev.frame}] ${team} 巡回車${ev.agent + 1} が燃料補給 ⛽</div>`;
    })
    .join('');
}

// ----- ツールチップ・詳細 -----

function buildTooltip(cell, agentRef) {
  const r = state.replay;
  if (!r) return null;
  if (agentRef) {
    const a = r.days[state.day].frames[state.step][agentRef.team][agentRef.agent];
    const kind = r.agentKinds[agentRef.team][agentRef.agent];
    const fuel = kind === 'patrol' ? ` ／ 燃料 ${a.fuel}/${r.fuelLimits}` : '';
    return `<b>${r.teamNames[agentRef.team]}</b> ${AGENT_TYPE_LABEL[kind]}${agentRef.agent + 1}<br>セル ${a.cell}${fuel}`;
  }
  if (cell === null) return null;
  const t = r.terrain[cell];
  let html = `<b>セル ${cell}</b> ${TERRAIN_LABEL[t]}`;
  if (t === 'road') {
    const st = r.days[state.day].roadStates.get(cell) ?? 'smooth';
    html += `（${ROAD_STATE_LABEL[st]}）`;
  }
  const spot = r.spots.get(cell);
  if (spot) {
    const name = r.seriesNames[spot.brand] ?? `系列${spot.brand}`;
    const left = stocksAt(state.day, state.step, state.focusTeam).get(cell);
    html += `<br>スポット: ${name} ／ 在庫 ${left}/${spot.stocks}（${r.teamNames[state.focusTeam]}視点）`;
  }
  const here = [];
  r.days[state.day].frames[state.step].forEach((agents, ti) => {
    agents.forEach((a, ai) => {
      if (a.cell === cell)
        here.push(`${r.teamNames[ti]} ${AGENT_TYPE_LABEL[r.agentKinds[ti][ai]]}${ai + 1}`);
    });
  });
  if (here.length) html += `<br>滞在: ${here.join('、')}`;
  return html;
}

function renderInspector() {
  const r = state.replay;
  const root = $('inspector');
  if (!r || !state.selection) {
    root.innerHTML = '<p class="muted">セルやエージェントをクリックすると詳細を表示します。</p>';
    return;
  }
  const sel = state.selection;
  if (sel.type === 'agent') {
    const a = r.days[state.day].frames[state.step][sel.team][sel.agent];
    const kind = r.agentKinds[sel.team][sel.agent];
    const rows = [
      ['チーム', r.teamNames[sel.team]],
      ['種別', AGENT_TYPE_LABEL[kind]],
      ['現在セル', a.cell],
    ];
    if (kind === 'patrol') rows.push(['燃料', `${a.fuel} / ${r.fuelLimits}`]);
    const acquired = r.days[state.day].events.filter(
      (e) => e.type === 'acquire' && e.team === sel.team && e.agent === sel.agent && e.frame <= state.step
    ).length;
    rows.push(['本日の獲得', `${acquired}玉`]);
    root.innerHTML = detailTable(rows);
    return;
  }
  // セル
  const cell = sel.cell;
  const t = r.terrain[cell];
  const rows = [
    ['セル番号', cell],
    ['地形', TERRAIN_LABEL[t]],
  ];
  if (t === 'road') {
    const st = r.days[state.day].roadStates.get(cell) ?? 'smooth';
    rows.push(['道路状態', ROAD_STATE_LABEL[st]]);
  }
  if (t !== 'pond') {
    const stepCost = { plain: 2, mountain: 3, road: { smooth: 1, congested: 2, jammed: 4 } };
    const fuelCost = { plain: 1, mountain: 2, road: 2 };
    const sc = t === 'road' ? stepCost.road[r.days[state.day].roadStates.get(cell) ?? 'smooth'] : stepCost[t];
    rows.push(['移動時間', `${sc}ステップ`], ['燃料', `${t === 'road' ? fuelCost.road : fuelCost[t]}`]);
  }
  const spot = r.spots.get(cell);
  if (spot) {
    rows.push(['スポット系列', r.seriesNames[spot.brand] ?? `系列${spot.brand}`]);
    rows.push(['最大在庫', spot.stocks]);
    for (let ti = 0; ti < r.numTeams; ti++) {
      rows.push([`残在庫(${r.teamNames[ti]})`, stocksAt(state.day, state.step, ti).get(cell)]);
    }
  }
  root.innerHTML = detailTable(rows);
}

function detailTable(rows) {
  return `<table class="detail-table">${rows
    .map(([k, v]) => `<tr><th>${k}</th><td>${v}</td></tr>`)
    .join('')}</table>`;
}

// ----- 凡例 -----

function renderLegend() {
  const r = state.replay;
  const root = $('legend');
  const terrainRows = ['plain', 'road', 'mountain', 'pond']
    .map(
      (t) =>
        `<span class="legend-item"><span class="swatch terrain-swatch-${t}"></span>${TERRAIN_LABEL[t]}</span>`
    )
    .join('');
  const roadRows = `
    <span class="legend-item"><span class="road-badge" style="color:#fab219">!</span>混雑（2ステップ）</span>
    <span class="legend-item"><span class="road-badge" style="color:#d03b3b">×</span>渋滞（4ステップ）</span>`;
  const shapeRows = `
    <span class="legend-item"><span class="shape-demo shape-circle"></span>巡回車</span>
    <span class="legend-item"><span class="shape-demo shape-diamond"></span>補給車</span>`;
  let seriesRows = '';
  let teamRows = '';
  if (r) {
    seriesRows = r.seriesNames
      .map(
        (name, i) =>
          `<span class="legend-item"><span class="swatch ring" style="border-color:${seriesColor(i)}"></span>${name}</span>`
      )
      .join('');
    teamRows = r.teamNames
      .map(
        (name, i) =>
          `<span class="legend-item"><span class="chip" style="background:${teamColor(i)}"></span>${name}</span>`
      )
      .join('');
  }
  root.innerHTML = `
    <div class="legend-group"><h3>地形</h3>${terrainRows}</div>
    <div class="legend-group"><h3>道路状態</h3>${roadRows}</div>
    <div class="legend-group"><h3>エージェント</h3>${shapeRows}</div>
    ${seriesRows ? `<div class="legend-group"><h3>うどん系列</h3>${seriesRows}</div>` : ''}
    ${teamRows ? `<div class="legend-group"><h3>チーム</h3>${teamRows}</div>` : ''}`;
}

renderLegend();
renderScoreboard();
