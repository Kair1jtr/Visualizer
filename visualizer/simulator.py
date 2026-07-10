"""ヘキサうどん サンプル試合シミュレータ。

貪欲法 AI のチーム同士を募集要項のルールに沿って対戦させ、
公式フォーマット（「競技部門『ヘキサうどん』のフォーマットについて」準拠）の
試合データ一式を生成する:

- match:  試合開始前のマップ構成フォーマット
- days[].info:  各日開始時の試合情報フォーマット（チーム0視点）
- kinds:  エージェント種別の回答フォーマット（チームごと）
- days[].plans: 行動計画の回答フォーマット（チームごと）

再現しているルール:
- 移動: 出発セルの地形で決まるステップ数・燃料を消費（巡回車のみ燃料消費）
- 燃料切れ・日内ステップ不足時は待機
- 補給車と巡回車が1ステップ以上同セルにいると燃料満タンまで補給
- スポット: 1巡回車1スポット1日1玉、チームごとに独立した在庫、毎日補充
- 道路: 前日・前々日の全チーム滞在ステップ数÷チーム数で順調/混雑/渋滞が決まる
- 勝敗: 種類数 → 日ごとの種類数の累積 → 玉数 →（回答時間は模擬対象外）
"""

import random
import time

from .hexgrid import direction_code
from .mapgen import generate_map
from .pathfinding import (
    FUEL_COST,
    STEP_COST,
    dijkstra,
    path_fuel_cost,
    reconstruct_path,
    terrain_key,
)

TEAM_NAMES = ["チームA", "チームB", "チームC", "チームD", "チームE", "チームF"]

# 公式フォーマットのコード表
TERRAIN_CODE = {"plain": 0, "road": 1, "mountain": 2, "pond": 3}
ROAD_STATUS_CODE = {"smooth": 0, "congested": 1, "jammed": 2}
KIND_CODE = {"patrol": 0, "supply": 1}


def _compress_waits(actions: list[int]) -> list[int]:
    """連続する1ステップ待機(-1)を公式形式の -N にまとめる。"""
    out: list[int] = []
    for a in actions:
        if a == -1 and out and out[-1] <= -1:
            out[-1] -= 1
        else:
            out.append(a)
    return out


class _Agent:
    def __init__(self, kind: str, cell: int, fuel: int | None):
        self.kind = kind  # "patrol" | "supply"
        self.cell = cell
        self.fuel = fuel  # supply は None
        self.move_remaining = 0
        self.move_target: int | None = None
        self.path: list[int] = []  # これから通るセル（現在地は含まない）
        self.dest: int | None = None
        self.target_spot: int | None = None
        self.acquired_today: set[int] = set()
        self.waiting_fuel = False


class _Team:
    def __init__(self, name: str, agents: list[_Agent], rng: random.Random):
        self.name = name
        self.agents = agents
        # チームごとの「作戦の個性」。これがないと全チームが同じ手を
        # 指して完全に同点・同位置になってしまう。
        self.rng = rng
        self.bonus_new_series = rng.uniform(2.2, 3.8)
        self.bonus_new_today = rng.uniform(1.3, 2.0)
        self.decision_jitter = rng.uniform(0.12, 0.35)
        self.supply_threshold = rng.uniform(0.4, 0.6)
        self.stock: dict[int, int] = {}
        self.claims: dict[int, int] = {}  # spot cell -> agent index
        self.series_overall: set[int] = set()
        self.series_today: set[int] = set()
        self.daily_series_counts: list[int] = []
        self.total = 0


class MatchSimulator:
    def __init__(self, seed, num_teams, num_days, num_agents, width, height):
        self.rng = random.Random(seed)
        self.seed = seed
        self.width = width
        self.height = height

        num_spots = max(6, min(14, (width * height) // 10))
        num_series = self.rng.randint(3, 5)
        self.map = generate_map(
            self.rng, width, height, num_spots, num_series, num_agents
        )
        self.spots = {s["cell"]: s for s in self.map["spots"]}

        self.fuel_capacity = 14
        self.congest_threshold = self.rng.randint(3, 4)
        self.jam_threshold = self.rng.randint(7, 9)

        self.num_supply = 2 if num_agents >= 6 else 1
        self.teams = []
        for t in range(num_teams):
            agents = []
            for i, start in enumerate(self.map["starts"]):
                is_supply = i >= num_agents - self.num_supply
                agents.append(
                    _Agent(
                        "supply" if is_supply else "patrol",
                        start,
                        None if is_supply else self.fuel_capacity,
                    )
                )
            self.teams.append(
                _Team(TEAM_NAMES[t], agents, random.Random(seed * 1000 + t))
            )

        self.day_steps = [self.rng.randint(36, 46) for _ in range(num_days)]
        self.day_seconds = [self.rng.choice([5, 7, 10]) for _ in range(num_days)]
        self.starts_at = int(time.time())
        # 各日の回答受付終了時刻: 開始時刻から回答時間を積み上げ（日間に30秒の間隔）
        self.ends_at = []
        t = self.starts_at
        for sec in self.day_seconds:
            t += sec
            self.ends_at.append(t)
            t += 30
        self.traffic_history: list[dict[int, int]] = []
        self.days = []

    # ----- 地形・コスト -----

    def _key_of(self, road_states):
        terrain = self.map["terrain"]

        def key(cell):
            return terrain_key(terrain[cell], road_states.get(cell, "smooth"))

        return key

    def _road_states_for_day(self, day_index: int) -> dict[int, str]:
        terrain = self.map["terrain"]
        states = {}
        if day_index == 0:
            recent = {}
        else:
            recent = dict(self.traffic_history[-1])
            if day_index >= 2:
                for cell, cnt in self.traffic_history[-2].items():
                    recent[cell] = recent.get(cell, 0) + cnt
        for cell, t in enumerate(terrain):
            if t != "road":
                continue
            volume = recent.get(cell, 0) / len(self.teams)
            if volume >= self.jam_threshold:
                states[cell] = "jammed"
            elif volume >= self.congest_threshold:
                states[cell] = "congested"
            else:
                states[cell] = "smooth"
        return states

    # ----- AI -----

    def _plan_patrol(self, team: _Team, agent_idx: int, agent: _Agent, key_of, steps_left: int):
        dist, prev = dijkstra(agent.cell, self.width, self.height, key_of)
        best = None
        best_score = -1.0
        unaffordable = False
        for cell, spot in self.spots.items():
            if team.stock.get(cell, 0) <= 0 or cell in agent.acquired_today:
                continue
            if cell == agent.cell:
                # うどん獲得は「到着」時のみ発火するため、現在いるセルは
                # 一度離れて戻る必要がある。目的地としては除外する。
                continue
            claimed = team.claims.get(cell)
            if claimed is not None and claimed != agent_idx and team.stock[cell] <= 1:
                continue
            if cell not in dist:
                continue
            path = reconstruct_path(prev, agent.cell, cell)
            fuel_need = path_fuel_cost(path, key_of)
            if fuel_need > agent.fuel:
                unaffordable = True
                continue
            series = spot["series"]
            if series not in team.series_overall:
                bonus = team.bonus_new_series
            elif series not in team.series_today:
                bonus = team.bonus_new_today
            else:
                bonus = 1.0
            score = bonus / (dist[cell] + 1)
            score *= 1.0 + team.rng.uniform(-team.decision_jitter, team.decision_jitter)
            if score > best_score:
                best_score = score
                best = (cell, path)
        if best:
            cell, path = best
            if agent.target_spot is not None:
                team.claims.pop(agent.target_spot, None)
            team.claims[cell] = agent_idx
            agent.target_spot = cell
            agent.path = path[1:]
            agent.dest = cell
        elif unaffordable:
            agent.waiting_fuel = True

    def _plan_supply(self, team: _Team, agent: _Agent, key_of):
        patrols = [a for a in team.agents if a.kind == "patrol"]
        waiting = [a for a in patrols if a.waiting_fuel]
        if waiting:
            target = min(waiting, key=lambda a: a.fuel)
        else:
            low = [a for a in patrols if a.fuel <= self.fuel_capacity * team.supply_threshold]
            if not low:
                agent.path = []
                agent.dest = None
                return
            target = min(low, key=lambda a: a.fuel)
        if target.cell == agent.cell:
            agent.path = []
            agent.dest = agent.cell
            return
        if agent.dest != target.cell or not agent.path:
            dist, prev = dijkstra(agent.cell, self.width, self.height, key_of)
            path = reconstruct_path(prev, agent.cell, target.cell)
            if path:
                agent.path = path[1:]
                agent.dest = target.cell

    # ----- 1日のシミュレーション -----

    def _agents_info(self, team: _Team) -> list[dict]:
        """公式「各日開始時の試合情報」の agents 配列を作る。"""
        return [
            {
                "kind": KIND_CODE[a.kind],
                "pos": a.cell,
                # 補給車に燃料の概念はないが、公式サンプル同様に上限値を入れる
                "fuel": a.fuel if a.fuel is not None else self.fuel_capacity,
            }
            for a in team.agents
        ]

    def _day_info(self, day_index: int, road_states: dict[int, str]) -> dict:
        """各日開始時の試合情報フォーマット（チーム0視点）。"""
        return {
            "endsAt": self.ends_at[day_index],
            "day": day_index,  # 初日は 0
            "agents": self._agents_info(self.teams[0]),
            "others": [
                {"id": ti, "agents": self._agents_info(team)}
                for ti, team in enumerate(self.teams)
                if ti != 0
            ],
            "traffics": [
                {"pos": cell, "status": ROAD_STATUS_CODE[state]}
                for cell, state in sorted(road_states.items())
            ],
        }

    def _run_day(self, day_index: int):
        steps = self.day_steps[day_index]
        road_states = self._road_states_for_day(day_index)
        key_of = self._key_of(road_states)

        # 日初期化: 在庫補充・獲得履歴/計画リセット
        for team in self.teams:
            team.stock = {c: s["maxStock"] for c, s in self.spots.items()}
            team.claims = {}
            team.series_today = set()
            for agent in team.agents:
                agent.acquired_today = set()
                agent.path = []
                agent.dest = None
                agent.target_spot = None
                agent.waiting_fuel = False

        # 日開始時点の試合情報（公式フォーマット）をここで確定
        info = self._day_info(day_index, road_states)

        # 各エージェントの行動記録（公式の行動計画フォーマットに変換する）
        actions = [[[] for _ in team.agents] for team in self.teams]
        traffic: dict[int, int] = {}
        terrain = self.map["terrain"]

        for k in range(steps):
            steps_left = steps - k
            cells_at_start = [
                [a.cell for a in team.agents] for team in self.teams
            ]

            # 行動決定・移動命令
            for ti, team in enumerate(self.teams):
                for ai, agent in enumerate(team.agents):
                    if agent.move_remaining > 0:
                        continue
                    if agent.kind == "patrol":
                        if agent.waiting_fuel:
                            continue
                        if agent.target_spot is not None and (
                            team.stock.get(agent.target_spot, 0) <= 0
                            or agent.target_spot in agent.acquired_today
                        ):
                            agent.path = []  # 目的地が無効化されたので再計画
                        if not agent.path:
                            self._plan_patrol(team, ai, agent, key_of, steps_left)
                        if agent.waiting_fuel or not agent.path:
                            continue
                        key = key_of(agent.cell)
                        if agent.fuel < FUEL_COST[key]:
                            agent.waiting_fuel = True
                            continue
                        if steps_left < STEP_COST[key]:
                            continue  # 日内に完了しない移動は無効 → 待機
                        agent.fuel -= FUEL_COST[key]
                        agent.move_remaining = STEP_COST[key]
                        agent.move_target = agent.path.pop(0)
                        actions[ti][ai].append(
                            direction_code(agent.cell, agent.move_target, self.width)
                        )
                    else:  # supply
                        self._plan_supply(team, agent, key_of)
                        if not agent.path:
                            continue
                        key = key_of(agent.cell)
                        if steps_left < STEP_COST[key]:
                            continue
                        agent.move_remaining = STEP_COST[key]
                        agent.move_target = agent.path.pop(0)
                        actions[ti][ai].append(
                            direction_code(agent.cell, agent.move_target, self.width)
                        )

            # 移動していないエージェントはこのステップを待機として記録
            for ti, team in enumerate(self.teams):
                for ai, agent in enumerate(team.agents):
                    if agent.move_remaining == 0:
                        actions[ti][ai].append(-1)

            # 交通量（このステップ中に道路セルへ滞在したエージェント数）
            for team in self.teams:
                for agent in team.agents:
                    if terrain[agent.cell] == "road":
                        traffic[agent.cell] = traffic.get(agent.cell, 0) + 1

            # 移動の進行と到着処理
            for ti, team in enumerate(self.teams):
                for ai, agent in enumerate(team.agents):
                    if agent.move_remaining <= 0:
                        continue
                    agent.move_remaining -= 1
                    if agent.move_remaining > 0:
                        continue
                    agent.cell = agent.move_target
                    agent.move_target = None
                    if agent.kind != "patrol":
                        continue
                    spot = self.spots.get(agent.cell)
                    if (
                        spot
                        and team.stock.get(agent.cell, 0) > 0
                        and agent.cell not in agent.acquired_today
                    ):
                        team.stock[agent.cell] -= 1
                        agent.acquired_today.add(agent.cell)
                        team.total += 1
                        team.series_overall.add(spot["series"])
                        team.series_today.add(spot["series"])
                        team.claims.pop(agent.cell, None)
                        if agent.target_spot == agent.cell:
                            agent.target_spot = None

            # 補給: 1ステップの間 同セルに居続けた巡回車×補給車
            for ti, team in enumerate(self.teams):
                supplies = [
                    (ai, a) for ai, a in enumerate(team.agents) if a.kind == "supply"
                ]
                for ai, agent in enumerate(team.agents):
                    if agent.kind != "patrol" or agent.fuel >= self.fuel_capacity:
                        continue
                    for si, supply in supplies:
                        stayed_together = (
                            supply.cell == agent.cell
                            and cells_at_start[ti][ai] == agent.cell
                            and cells_at_start[ti][si] == supply.cell
                        )
                        if stayed_together:
                            agent.fuel = self.fuel_capacity
                            agent.waiting_fuel = False
                            break

        for team in self.teams:
            team.daily_series_counts.append(len(team.series_today))
        self.traffic_history.append(traffic)

        self.days.append(
            {
                "info": info,
                "plans": [
                    [_compress_waits(agent_actions) for agent_actions in team_actions]
                    for team_actions in actions
                ],
            }
        )

    # ----- 実行と出力 -----

    def run(self) -> dict:
        for d in range(len(self.day_steps)):
            self._run_day(d)

        per_team = []
        for team in self.teams:
            per_team.append(
                {
                    "name": team.name,
                    "seriesCount": len(team.series_overall),
                    "dailySeriesCum": sum(team.daily_series_counts),
                    "totalUdon": team.total,
                }
            )
        ranking = sorted(
            range(len(self.teams)),
            key=lambda i: (
                -per_team[i]["seriesCount"],
                -per_team[i]["dailySeriesCum"],
                -per_team[i]["totalUdon"],
                i,
            ),
        )

        width = self.map["width"]
        height = self.map["height"]
        cells = [
            [TERRAIN_CODE[self.map["terrain"][r * width + c]] for c in range(width)]
            for r in range(height)
        ]

        return {
            "format": "hexaudon-official-v1",
            # meta はビジュアライザ用の補助情報（公式フォーマット外）
            "meta": {
                "title": f"サンプル試合 (seed={self.seed})",
                "seed": self.seed,
                "generator": "sample-simulator",
                "teamNames": [team.name for team in self.teams],
                "seriesNames": self.map["series"],
                "expected": {"perTeam": per_team, "ranking": ranking},
            },
            # 試合開始前のマップ構成フォーマット（公式）
            "match": {
                "startsAt": self.starts_at,
                "daySeconds": self.day_seconds,
                "daySteps": self.day_steps,
                "map": {"height": height, "width": width, "cells": cells},
                "spots": [
                    {"brand": s["series"], "pos": s["cell"], "stocks": s["maxStock"]}
                    for s in self.map["spots"]
                ],
                "agents": self.map["starts"],
                "fuelLimits": self.fuel_capacity,
                "players": len(self.teams),
                "busyThreshold": self.congest_threshold,
                "jammedThreshold": self.jam_threshold,
            },
            # エージェント種別の回答フォーマット（公式・チームごと）
            "kinds": [
                [KIND_CODE[a.kind] for a in team.agents] for team in self.teams
            ],
            # days[].info: 各日開始時の試合情報フォーマット（公式・チーム0視点）
            # days[].plans: 行動計画の回答フォーマット（公式・チームごと）
            "days": self.days,
        }


def generate_sample_match(
    seed: int | None = None,
    num_teams: int = 3,
    num_days: int = 5,
    num_agents: int = 4,
    width: int = 12,
    height: int = 10,
) -> dict:
    if seed is None:
        seed = random.randrange(1_000_000_000)
    sim = MatchSimulator(seed, num_teams, num_days, num_agents, width, height)
    return sim.run()


if __name__ == "__main__":
    import json
    import sys

    match = generate_sample_match(seed=42)
    json.dump(match, sys.stdout, ensure_ascii=False)
