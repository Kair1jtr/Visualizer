// シミュレーター観戦（公式ルール忠実シミュレーター simulator/）ビュー。
//
// 描画本体は matchview.js で、本番用簡易サーバーの観戦（index.html）と共通。
// 違いは3つだけ。
//   - データ源が /api/sim/*
//   - 軌跡が推定ではなく実測（全ステップの状態が残っている）
//   - ステップ単位の再生ができる
//
// 実時間の締切が無いので、「実行」を押すと全日程が一瞬で終わる。
// そのためポーリングは行わず、操作したときだけ取りに行く。

import { createMatchView } from './matchview.js';

const view = createMatchView({
  apiBase: './api/sim',
  pollMs: 0, // 走り切ったら状態は変わらないので定期ポーリングしない
  isRunning: (s) => !!s?.started,
  idleMessage: 'まだ実行していません。「実行」を押すとシミュレーターが1試合を走らせます。',
  noTrajMessage: (day) => `${day.day + 1}日目: 軌跡データがありません。`,
  startQuery: () => {
    const strategy = document.getElementById('sim-strategy').value;
    return `?strategy=${encodeURIComponent(strategy)}`;
  },
});

// 戦略を変えたら、そのまま押し直せるように起動ボタンを有効に戻す。
document.getElementById('sim-strategy').addEventListener('change', () => {
  document.getElementById('btn-start').disabled = false;
});
