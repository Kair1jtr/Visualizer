// 試合観戦（本番用簡易サーバー procon-server）ビュー。
//
// 「実行」を押すと procon-server が起動し、プレイヤーごとに割り当てた戦略で
// 実際に対戦するクライアント（visualizer/procon_client.py）も同時に立ち上がる
// （/api/real/start が players を受け取ると起動する）。起動・停止ボタンは
// この FastAPI アプリ自身にサブプロセス/クライアントの起動・停止を依頼する。
//
// 描画本体は matchview.js、戦略設定は strategypanel.js（シミュレーター観戦と
// 共用）。対戦クライアントが操作しているチームは、軌跡が推定ではなく実測になる
// （procon_client.py が自チームの全ステップの位置を手元で再現しているため）。
//
// 戦略のスキーマは `/api/sim/strategies` から取る。本番観戦の既定設定
// （server/試合設定用JSONファイル/example.json）と同じファイルを
// シミュレーター側の既定にも使っているため、プレイヤー一覧が一致する。

import { createMatchView } from './matchview.js';
import { initStrategyPanel } from './strategypanel.js';

const panel = initStrategyPanel({
  schemaUrl: './api/sim/strategies',
  onChange: () => {
    document.getElementById('btn-start').disabled = false;
  },
});

createMatchView({
  apiBase: './api/real',
  pollMs: 1500,
  // procon-server サブプロセスが生きている間は起動ボタンを無効にする
  isRunning: (s) => !!s?.processAlive,
  idleMessage: '試合サーバーは停止しています。',
  startBody: () => ({ players: panel.getSetups() }),
  // 公式APIは各日開始時のスナップショットしか返さないため、
  // 翌日の分が揃うまでその日の軌跡は確定しない（対戦クライアントが
  // 操作しているチームは例外で、実測の軌跡がその日のうちに出る）。
  noTrajMessage: (day, status) => {
    const days = status?.days ?? [];
    const isLastKnownDay = day.day === Math.max(...days.map((d) => d.day));
    if (!isLastKnownDay) return `${day.day + 1}日目: 軌跡データがありません。`;
    return status?.phase === 'ended'
      ? `${day.day + 1}日目（最終日）: 次の日のスナップショットが無いため、この日の移動軌跡は観測できません。`
      : `${day.day + 1}日目: 進行中（次の日になるまで、この日の移動軌跡は確定しません）。`;
  },
});
