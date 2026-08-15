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

# リポジトリ直下から visualizer パッケージ（六角形座標・経路探索）を借りる
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from visualizer.hexgrid import direction_code
from visualizer.pathfinding import (
    FUEL_COST,
    STEP_COST,
    dijkstra,
    reconstruct_path,
    terrain_key,
)

TERRAIN = {0: "plain", 1: "road", 2: "mountain", 3: "pond"}
ROAD = {0: "smooth", 1: "congested", 2: "jammed"}


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


# ----- 行動計画の組み立て -----


def make_key_of(match: dict, info: dict):
    """セル番号 → STEP_COST/FUEL_COST のキー（池は None）。"""
    width = match["map"]["width"]
    cells = match["map"]["cells"]
    road = {t["pos"]: ROAD[t["status"]] for t in info["traffics"]}

    def key_of(cell: int):
        r, c = divmod(cell, width)
        return terrain_key(TERRAIN[cells[r][c]], road.get(cell, "smooth"))

    return key_of


def walk_prefix(path, key_of, step_budget, fuel_budget, width):
    """経路の先頭から、ステップ・燃料の予算内で進める分だけ方向コード化する。"""
    plan, steps, fuel = [], 0, 0
    cell = path[0]
    for nxt in path[1:]:
        key = key_of(cell)
        if steps + STEP_COST[key] > step_budget:
            break
        if fuel_budget is not None and fuel + FUEL_COST[key] > fuel_budget:
            break
        plan.append(direction_code(cell, nxt, width))
        steps += STEP_COST[key]
        fuel += FUEL_COST[key]
        cell = nxt
    return plan, steps, fuel, cell


def plan_patrol(start, fuel, spots, claimed, key_of, day_steps, width, height):
    """最寄りスポットを連鎖訪問する巡回車の1日分の行動計画。"""
    plan, used, cell = [], 0, start
    visited = set()
    while True:
        dist, prev = dijkstra(cell, width, height, key_of)
        cands = sorted(
            (s for s in spots if s != cell and s not in visited
             and s not in claimed and s in dist),
            key=lambda s: dist[s],
        )
        progressed = False
        for target in cands:
            path = reconstruct_path(prev, cell, target)
            part, steps, spent, reached = walk_prefix(
                path, key_of, day_steps - used, fuel, width
            )
            if not part:
                continue
            plan += part
            used += steps
            fuel -= spent
            cell = reached
            if reached == target:  # スポット到着（翌スポットへ連鎖）
                visited.add(target)
                claimed.add(target)
            progressed = reached == target
            break
        if not progressed:
            break
    if used < day_steps:
        plan.append(-(day_steps - used))
    return plan, cell


def plan_supply(start, goal, key_of, day_steps, width, height):
    """目標セルへ向かう補給車の1日分の行動計画（燃料消費なし）。"""
    plan, used, cell = [], 0, start
    if goal is not None and goal != start:
        dist, prev = dijkstra(start, width, height, key_of)
        if goal in dist:
            path = reconstruct_path(prev, start, goal)
            plan, used, _, cell = walk_prefix(path, key_of, day_steps, None, width)
    if used < day_steps:
        plan.append(-(day_steps - used))
    return plan, cell


def build_plans(match: dict, info: dict, kinds: list[int]) -> list[list[int]]:
    width = match["map"]["width"]
    height = match["map"]["height"]
    day_steps = match["daySteps"][info["day"]]
    key_of = make_key_of(match, info)
    spots = [s["pos"] for s in match["spots"]]

    claimed: set[int] = set()
    plans: list = [None] * len(kinds)
    ends: list[tuple[int, int, int]] = []  # (fuel, agent_idx, end_cell)

    # 先に巡回車の計画を立て、到達予定地点を補給車の目標にする
    for ai, agent in enumerate(info["agents"]):
        if kinds[ai] == 0:
            plan, end = plan_patrol(
                agent["pos"], agent["fuel"], spots, claimed,
                key_of, day_steps, width, height,
            )
            plans[ai] = plan
            ends.append((agent["fuel"], ai, end))
    goal = min(ends)[2] if ends else None  # 燃料最少の巡回車の到達予定地点
    for ai, agent in enumerate(info["agents"]):
        if kinds[ai] == 1:
            plans[ai], _ = plan_supply(
                agent["pos"], goal, key_of, day_steps, width, height
            )
    return plans


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
