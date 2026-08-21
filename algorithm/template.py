#!/usr/bin/env python3
"""ヘキサうどん 行動計画テンプレート: 試合状況(JSON) → 行動計画(移動JSON)。

このファイルをコピーして、自分のアルゴリズムのベースにしてください。
今入っているのは単純な貪欲法（examples/client.py と同じ戦略）です。
`plan_patrol()` / `plan_supply()` の中身を書き換えれば戦略を差し替えられます。

入力（公式フォーマット）:
  - match: 試合開始前のマップ構成フォーマット（GET /api/match）
  - info:  各日開始時の試合情報フォーマット（GET /api/match/{day}）
  - kinds: エージェント種別の回答（自分が POST /api/agents で提出したもの）

出力（公式フォーマット）:
  - plans: 行動計画の回答フォーマット（POST /api/actions の body）

使い方:
  # match.json / info.json（それぞれ公式フォーマットのJSON）を用意して:
  python algorithm/template.py --match match.json --info info.json --kinds 0,0,0,1

  # {"match":..., "info":..., "kinds":[...]} をまとめた1つのJSONを標準入力から:
  cat combined.json | python algorithm/template.py

  # 実行中のモックサーバーから直接取得して計算（--kinds は自分が提出した種別）:
  python algorithm/template.py --server http://127.0.0.1:8000 --day 0 --kinds 0,0,0,1
"""

import argparse
import json
import sys
import urllib.request
from pathlib import Path

# リポジトリ直下から visualizer パッケージ（六角形座標・経路探索）を借りる。
# 自分のプログラムに組み込む場合は、このブロックと import 部分を
# 好きな実装（あるいは他言語への移植）に置き換えて構わない。
# （python algorithm/template.py のように直接実行された場合、
#  algorithm/__init__.py は実行されないためここでも sys.path を通す）
_repo_root = str(Path(__file__).resolve().parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

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


# ----- 補助関数（地形コスト・経路） -----


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


# ----- ここから下がアルゴリズム本体。自由に書き換えてよい -----


def plan_patrol(start, fuel, spots, claimed, key_of, day_steps, width, height):
    """巡回車1台の1日分の行動計画。既定は最寄りの未予約スポットへ
    Dijkstra 経路で向かい、届く限り連鎖訪問する貪欲法。
    """
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
    """補給車1台の1日分の行動計画。既定は燃料が最も少ない巡回車の
    到達予定地点へ向かうだけ（燃料消費なし）。
    """
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
    """試合状況（match + その日の info）→ 行動計画の回答フォーマット。

    ここが「試合状況から移動JSONを吐く」処理の入口。
    """
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


# ----- CLI: ファイル/標準入力/サーバーから試合状況を読み、移動JSONを出力する -----


def _load_json(path: str | None) -> dict:
    text = Path(path).read_text(encoding="utf-8") if path else sys.stdin.read()
    return json.loads(text)


def _fetch(server: str, path: str) -> dict:
    with urllib.request.urlopen(f"{server}{path}") as res:
        return json.loads(res.read())


def main():
    ap = argparse.ArgumentParser(
        description="試合状況(JSON) → 行動計画(移動JSON) を計算して標準出力に書く。"
    )
    ap.add_argument("--match", help="match.json のパス（省略時 --server と併用）")
    ap.add_argument("--info", help="info.json のパス（省略時 --server と併用）")
    ap.add_argument(
        "--combined",
        help="{'match':..,'info':..,'kinds':..} をまとめた1つのJSONファイル"
        "（省略・'-' 指定時は標準入力から読む）",
    )
    ap.add_argument("--kinds", help="エージェント種別。例: 0,0,0,1（--combined 未使用時は必須）")
    ap.add_argument("--server", help="モックサーバーURL。指定時は --match/--info の代わりに取得する")
    ap.add_argument("--day", type=int, default=0, help="--server 使用時に取得する日（0始まり）")
    ap.add_argument("--compact", action="store_true", help="出力JSONを1行に圧縮する")
    args = ap.parse_args()

    if args.server:
        match = _fetch(args.server, "/api/match")
        info = _fetch(args.server, f"/api/match/{args.day}")
        if not args.kinds:
            ap.error("--server 使用時は --kinds も指定してください")
        kinds = [int(k) for k in args.kinds.split(",")]
    elif args.match or args.info:
        if not (args.match and args.info and args.kinds):
            ap.error("--match/--info を使う場合は --kinds も指定してください")
        match = _load_json(args.match)
        info = _load_json(args.info)
        kinds = [int(k) for k in args.kinds.split(",")]
    else:
        data = _load_json(args.combined if args.combined != "-" else None)
        match, info, kinds = data["match"], data["info"], data["kinds"]

    plans = build_plans(match, info, kinds)
    if args.compact:
        print(json.dumps(plans, ensure_ascii=False))
    else:
        print(json.dumps(plans, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
