#!/usr/bin/env python3
"""ヘキサうどん ライブ対戦のサンプルクライアント。

モックサーバーの /api/live/new で試合を開始し、公式フォーマットの回答
（エージェント種別・行動計画）を毎日送信して最終日まで対戦する。
自作プログラムを書くときの通信手順・回答の組み立て方の見本。

使い方:
    python app.py                 # 別ターミナルでサーバーを起動しておく
    python examples/client.py [--server http://127.0.0.1:8000] [--seed 42]

戦略（単純な貪欲法）:
- 巡回車: 最寄りの未予約スポットへ Dijkstra 経路で向かい、届く限り連鎖。
  行き切れない分は途中まで前進し、残りステップは待機。
- 補給車: 燃料が最も少ない巡回車の到達予定地点へ向かう。
※ 燃料切れの巡回車は待機扱いになるだけなので、戦略の改良はご自由に。
"""

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

# リポジトリ直下から algorithm パッケージ（貪欲法テンプレート）を借りる
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from algorithm.template import build_plans  # noqa: E402（sys.path 追加後に import するため）


def api(server: str, method: str, path: str, body=None):
    req = urllib.request.Request(
        server + path,
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req) as res:
            return json.loads(res.read())
    except urllib.error.HTTPError as exc:
        raise SystemExit(f"{method} {path} -> HTTP {exc.code}: {exc.read().decode()}")
    except urllib.error.URLError as exc:
        raise SystemExit(f"サーバーに接続できません ({server}): {exc.reason}")


# ----- 対戦の進行 -----


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--server", default="http://127.0.0.1:8000")
    ap.add_argument("--seed", type=int, default=None, help="試合の乱数シード")
    ap.add_argument("--teams", type=int, default=1, help="1=他プレイヤーなしの1人プレイ（既定）、2以上でAIチームと対戦")
    ap.add_argument("--days", type=int, default=5)
    ap.add_argument("--agents", type=int, default=4)
    args = ap.parse_args()

    query = f"?teams={args.teams}&days={args.days}&agents={args.agents}"
    if args.seed is not None:
        query += f"&seed={args.seed}"
    res = api(args.server, "POST", f"/api/live/new{query}")
    print(f"ライブ試合を開始 (seed={res['seed']})")

    match = api(args.server, "GET", "/api/match")
    num_agents = len(match["agents"])
    num_days = len(match["daySteps"])

    # 種別: 最後の1台を補給車、残りを巡回車に
    kinds = [0] * (num_agents - 1) + [1]
    api(args.server, "POST", "/api/agents", kinds)
    print(f"種別を提出: {kinds} (0=巡回車 1=補給車)")

    for day in range(num_days):
        info = api(args.server, "GET", f"/api/match/{day}")
        plans = build_plans(match, info, kinds)
        res = api(args.server, "POST", f"/api/actions?day={day}", plans)
        line = " / ".join(
            f"{s['rank']}位 {s['name']} {s['seriesCount']}種 {s['totalUdon']}玉"
            for s in res["standings"]
        )
        print(f"{day + 1}日目 完了: {line}")

    print("試合終了！ ブラウザの「現在の試合を表示」で観戦できます。")


if __name__ == "__main__":
    main()
