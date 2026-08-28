// 試合観戦（本番用簡易サーバー procon-server）ビュー。
// このページに操作機能は無く、/api/real/* をポーリングして進行状況を表示するだけ。
// 起動・停止ボタンはこの FastAPI アプリ自身に procon-server サブプロセスの
// 起動/停止を依頼するためのもの（試合そのものへの操作ではない）。
//
// 描画本体は matchview.js。シミュレーター観戦（sim.html）と共通で、
// ここはデータ源と文言の違いだけを渡している。

import { createMatchView } from './matchview.js';

createMatchView({
  apiBase: './api/real',
  pollMs: 1500,
  // procon-server サブプロセスが生きている間は起動ボタンを無効にする
  isRunning: (s) => !!s?.processAlive,
  idleMessage: '試合サーバーは停止しています。',
  // 公式APIは各日開始時のスナップショットしか返さないため、
  // 翌日の分が揃うまでその日の軌跡は確定しない。
  noTrajMessage: (day, status) => {
    const days = status?.days ?? [];
    const isLastKnownDay = day.day === Math.max(...days.map((d) => d.day));
    if (!isLastKnownDay) return `${day.day + 1}日目: 軌跡データがありません。`;
    return status?.phase === 'ended'
      ? `${day.day + 1}日目（最終日）: 次の日のスナップショットが無いため、この日の移動軌跡は観測できません。`
      : `${day.day + 1}日目: 進行中（次の日になるまで、この日の移動軌跡は確定しません）。`;
  },
});
