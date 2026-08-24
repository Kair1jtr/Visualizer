#!/usr/bin/env python3
"""algorithm/plan_builder.py の使い方サンプル。

JSON（方向コード・待機の負数）を直接組み立てず、move()/wait()/goto() の
呼び出しだけで1日分の行動計画を作り、ライブ対戦を最後までプレイする。
examples/client.py（Dijkstraで最適化した貪欲法）と比べて、あえて単純な
戦略にしてある。plan_builder.py のAPIの使い方を見るのが目的。

使い方:
    python app.py                        # 別ターミナルでサーバー起動
    python algorithm/example_manual.py --seed 42
"""

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from algorithm.plan_builder import DayPlan  # noqa: E402


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


def decide(match: dict, info: dict, kinds: list[int]) -> list[list[int]]:
    """試合状況 → 行動計画（移動JSON）。ここが plan_builder.py の使い方の本体。"""
    plan = DayPlan(match, info, kinds)
    spots = [s["pos"] for s in match["spots"]]
    claimed: set[int] = set()

    # 巡回車: 未訪問のスポットへ順番に goto()。届く範囲で連鎖的に訪問する。
    # （本当に「最も近い」スポットを選ぶには経路距離の計算が必要。ここでは
    #   簡略化してリストの先頭から順に狙うだけ。改良は自由に）
    for i, kind in enumerate(kinds):
        if kind != 0:  # 巡回車のみ
            continue
        agent = plan.agent(i)
        while agent.remaining > 0:
            target = next((s for s in spots if s not in claimed), None)
            if target is None:
                break
            reached = agent.goto(target)
            if reached != target:
                break  # ステップ or 燃料が尽きて途中で止まった
            claimed.add(target)

    # 補給車: 最初の巡回車のところまで行って待機する（単純な例）
    patrol_indices = [i for i, k in enumerate(kinds) if k == 0]
    for i, kind in enumerate(kinds):
        if kind != 1:  # 補給車のみ
            continue
        agent = plan.agent(i)
        if patrol_indices:
            target = plan.agent(patrol_indices[0]).cell
            agent.goto(target)
        # 残りはそのまま待機（wait() を明示的に呼ばなくても to_json() が自動で埋める）

    return plan.to_json()


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--server", default="http://127.0.0.1:8000")
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--teams", type=int, default=1)
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

    kinds = [0] * (num_agents - 1) + [1]  # 最後の1台を補給車、残りを巡回車に
    api(args.server, "POST", "/api/agents", kinds)
    print(f"種別を提出: {kinds} (0=巡回車 1=補給車)")

    for day in range(num_days):
        info = api(args.server, "GET", f"/api/match/{day}")
        plans = decide(match, info, kinds)
        res = api(args.server, "POST", f"/api/actions?day={day}", plans)
        line = " / ".join(
            f"{s['rank']}位 {s['name']} {s['seriesCount']}種 {s['totalUdon']}玉"
            for s in res["standings"]
        )
        print(f"{day + 1}日目 完了: {line}")

    print("試合終了！")


if __name__ == "__main__":
    main()
