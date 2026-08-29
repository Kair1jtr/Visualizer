// シミュレーター観戦（公式ルール忠実シミュレーター simulator/）ビュー。
//
// 盤面の描画本体は matchview.js で、本番用簡易サーバーの観戦（index.html）と共通。
// このファイル固有の仕事は「プレイヤーごとの戦略設定」である。
//
//   - /api/sim/strategies から戦略とパラメータのスキーマを取る
//   - プレイヤーごとにボタンを並べ、押すとダイアログ（ページ遷移なし）を開く
//   - ダイアログで戦略とパラメータを決め、実行時に JSON ボディで送る
//
// フォームはスキーマから生成しているので、simulator/strategy.py に戦略クラスを
// 足して @register を付ければ、このファイルは触らなくても選べるようになる。

import { createMatchView } from './matchview.js';
import { teamColor } from './palette.js';

const $ = (id) => document.getElementById(id);

// /api/sim/strategies の中身
let schema = { strategies: [], players: [], default: 'greedy' };
// プレイヤー番号順の設定 [{strategy, params:{...}}, ...]
let setups = [];
// ダイアログで編集中のプレイヤー番号（null なら閉じている）
let editing = null;

const dialog = $('strategy-dialog');

createMatchView({
  apiBase: './api/sim',
  pollMs: 0, // 走り切ったら状態は変わらないので定期ポーリングしない
  isRunning: (s) => !!s?.started,
  idleMessage: 'まだ実行していません。戦略を決めて「実行」を押してください。',
  noTrajMessage: (day) => `${day.day + 1}日目: 軌跡データがありません。`,
  startBody: () => ({ players: setups }),
});

// ----- スキーマの取得 -----

function strategyByName(name) {
  return schema.strategies.find((s) => s.name === name) ?? schema.strategies[0];
}

function defaultParams(name) {
  const entry = strategyByName(name);
  const out = {};
  for (const p of entry?.params ?? []) out[p.name] = p.default;
  return out;
}

async function load() {
  const root = $('strategy-panel');
  try {
    const res = await fetch('./api/sim/strategies');
    if (!res.ok) throw new Error(await res.text());
    schema = await res.json();
  } catch (e) {
    root.innerHTML = `<p class="muted">戦略一覧を取得できませんでした: ${e}</p>`;
    return;
  }
  setups = schema.players.map(() => ({
    strategy: schema.default,
    params: defaultParams(schema.default),
  }));
  renderPanel();
}

// ----- プレイヤー一覧 -----

// 既定値から変えたパラメータの数（ボタンに出す）
function changedCount(setup) {
  const base = defaultParams(setup.strategy);
  return Object.keys(base).filter((k) => setup.params[k] !== base[k]).length;
}

function renderPanel() {
  const root = $('strategy-panel');
  root.innerHTML = schema.players
    .map((player, index) => {
      const setup = setups[index];
      const entry = strategyByName(setup.strategy);
      const changed = changedCount(setup);
      const badge = changed ? `<span class="strategy-badge">調整 ${changed}</span>` : '';
      return `<div class="strategy-row">
        <span class="chip" style="background:${teamColor(player.id)}"></span>
        <span class="strategy-name">${player.name}</span>
        <button type="button" class="btn strategy-btn" data-player="${index}">
          ${entry?.label ?? setup.strategy}${badge}
        </button>
      </div>`;
    })
    .join('');

  root.querySelectorAll('.strategy-btn').forEach((btn) => {
    btn.addEventListener('click', () => openDialog(Number(btn.dataset.player)));
  });
}

// ----- 設定ダイアログ -----

function openDialog(index) {
  editing = index;
  const setup = setups[index];
  $('dialog-title').textContent = `${schema.players[index].name} の戦略`;

  const select = $('dialog-strategy');
  select.innerHTML = schema.strategies
    .map(
      (s) =>
        `<option value="${s.name}"${s.name === setup.strategy ? ' selected' : ''}>${s.label}</option>`
    )
    .join('');

  renderParams(setup.strategy, setup.params);
  dialog.showModal();
}

// 戦略を切り替えたとき: 同じ名前のパラメータは値を引き継ぎ、残りは新しい既定値。
function carryOver(name, current) {
  const base = defaultParams(name);
  for (const key of Object.keys(base)) {
    if (key in current) base[key] = current[key];
  }
  return base;
}

function renderParams(name, values) {
  const entry = strategyByName(name);
  $('dialog-desc').textContent = entry?.description ?? '';
  const root = $('dialog-params');
  if (!entry || !entry.params.length) {
    root.innerHTML = '<p class="muted">この戦略に調整できるパラメータはありません。</p>';
    return;
  }
  root.innerHTML = entry.params
    .map((p) => {
      const value = values[p.name] ?? p.default;
      let control;
      if (p.kind === 'bool') {
        control = `<input type="checkbox" data-param="${p.name}" data-kind="bool"${value ? ' checked' : ''} />`;
      } else if (p.kind === 'choice') {
        const options = (p.choices ?? [])
          .map((c) => `<option value="${c}"${c === value ? ' selected' : ''}>${c}</option>`)
          .join('');
        control = `<select data-param="${p.name}" data-kind="choice">${options}</select>`;
      } else {
        const step = p.step ?? (p.kind === 'int' ? 1 : 0.1);
        const min = p.min != null ? ` min="${p.min}"` : '';
        const max = p.max != null ? ` max="${p.max}"` : '';
        control = `<input type="number" data-param="${p.name}" data-kind="${p.kind}" value="${value}" step="${step}"${min}${max} />`;
      }
      return `<div class="param-row">
        <div class="param-head"><span class="param-label">${p.label}</span>${control}</div>
        ${p.description ? `<p class="param-desc">${p.description}</p>` : ''}
      </div>`;
    })
    .join('');
}

// フォームから現在の入力値を読み取る
function readParams() {
  const out = {};
  $('dialog-params')
    .querySelectorAll('[data-param]')
    .forEach((node) => {
      const key = node.dataset.param;
      if (node.dataset.kind === 'bool') out[key] = node.checked;
      else if (node.dataset.kind === 'choice') out[key] = node.value;
      else out[key] = Number(node.value);
    });
  return out;
}

$('dialog-strategy').addEventListener('change', () => {
  const name = $('dialog-strategy').value;
  renderParams(name, carryOver(name, readParams()));
});

$('dialog-reset').addEventListener('click', () => {
  const name = $('dialog-strategy').value;
  renderParams(name, defaultParams(name));
});

function closeDialog() {
  editing = null;
  dialog.close();
}

$('dialog-cancel').addEventListener('click', closeDialog);
$('dialog-close').addEventListener('click', closeDialog);

$('dialog-apply').addEventListener('click', () => {
  if (editing == null) return;
  setups[editing] = { strategy: $('dialog-strategy').value, params: readParams() };
  closeDialog();
  renderPanel();
  // 設定を変えたらそのまま押し直せるように実行ボタンを戻す
  $('btn-start').disabled = false;
});

load();
