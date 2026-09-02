// シミュレーター観戦（公式ルール忠実シミュレーター simulator/）ビュー。
//
// 盤面の描画本体は matchview.js、プレイヤーごとの戦略設定は
// strategypanel.js（本番試合観戦と共用）に委ねている。このファイルは
// 両者をつなぐだけ。
//
// 実時間の締切が無いので、「実行」を押すと全日程が一瞬で終わる。
// そのためポーリングは行わず、操作したときだけ取りに行く。

import { createMatchView } from './matchview.js';
import { initStrategyPanel } from './strategypanel.js';

const panel = initStrategyPanel({
  schemaUrl: './api/sim/strategies',
  // 設定を変えたらそのまま押し直せるように実行ボタンを戻す
  onChange: () => {
    document.getElementById('btn-start').disabled = false;
  },
});

createMatchView({
  apiBase: './api/sim',
  pollMs: 0,
  isRunning: (s) => !!s?.started,
  idleMessage: 'まだ実行していません。戦略を決めて「実行」を押してください。',
  noTrajMessage: (day) => `${day.day + 1}日目: 軌跡データがありません。`,
  startBody: () => ({ players: panel.getSetups() }),
});
