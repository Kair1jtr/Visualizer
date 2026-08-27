"""ヘキサうどん の試合を表す `HexaUdon` クラス。

公式資料「競技部門『ヘキサうどん』のフォーマットについて」の
**試合開始前のマップ構成フォーマット**のキーと同名のフィールドを持つ:

    startsAt / daySeconds / daySteps / map（height, width, cells）/
    spots（brand, pos, stocks）/ agents / fuelLimits / players /
    busyThreshold / jammedThreshold

このクラス1つで以下の両方を動かす:
- 模擬試合（AI同士の貪欲法対戦。`run()` で全日程を一括生成）
- ライブ対戦（`submit_kinds()` → `submit_actions()` を日ごとに呼んで
  1日ずつ進める。チーム0が外部クライアント＝プレイヤー）

再現しているルール（公式Q&A・補足資料で確定した反映フェーズ順序に準拠:
燃料消費→移動反映→うどん獲得→燃料補給→交通量更新）:
- 移動: 出発セルの地形で決まるステップ数・燃料を消費（巡回車のみ燃料消費）
- 燃料切れ・日内ステップ不足時は待機
- 補給車と巡回車が1ステップ以上同セルにいると燃料満タンまで補給
- スポット: 1巡回車1スポット1日1玉、チームごとに独立した在庫、毎日補充。
  到着時に限らず、日をまたいでスポット上に留まっている場合も
  1ステップ目以降に獲得できる（Q&Aその1 Q7/A7・Q8/A8）
- 道路: 前日・前々日の全チーム滞在ステップ数÷チーム数で順調/混雑/渋滞が決まる。
  滞在数は移動反映後のセルをカウント（Q&Aその2 Q27/A27）
- 勝敗: 種類数 → 日ごとの種類数の累積 → 玉数 →（回答時間は模擬対象外）
"""

import random
import time

from .hexgrid import apply_direction, direction_code
from .mapgen import generate_map
from .pathfinding import (
    FUEL_COST,
    STEP_COST,
    dijkstra,
    path_fuel_cost,
    reconstruct_path,
    terrain_key,
)

TEAM_NAMES = ["チームA", "チームB", "チームC", "チームD", "チームE", "チームF", "チームG", "チームH"]
PLAYER_TEAM = 0
PLAYER_NAME = "プレイヤー"

# 公式フォーマットのコード表
TERRAIN_CODE = {"plain": 0, "road": 1, "mountain": 2, "pond": 3}
ROAD_STATUS_CODE = {"smooth": 0, "congested": 1, "jammed": 2}
KIND_CODE = {"patrol": 0, "supply": 1}


class HexaUdonError(Exception):
    """ライブ対戦の手順誤り（HTTP 409 相当）。"""

    def __init__(self, detail: str):
        super().__init__(detail)
        self.detail = detail


def _compress_waits(actions: list[int]) -> list[int]:
    """連続する1ステップ待機(-1)を公式形式の -N にまとめる。"""
    out: list[int] = []
    for a in actions:
        if a == -1 and out and out[-1] <= -1:
            out[-1] -= 1
        else:
            out.append(a)
    return out


class _PlanCursor:
    """公式の行動計画（-N=待機N / 0〜5=方向）を1コマンドずつ取り出す。"""

    def __init__(self, plan: list[int]):
        self.queue: list[int] = []
        for value in plan:
            if value <= -1:
                self.queue.extend([-1] * (-value))
            else:
                self.queue.append(value)
        self.index = 0

    def peek(self) -> int | None:
        return self.queue[self.index] if self.index < len(self.queue) else None

    def take(self) -> int | None:
        value = self.peek()
        if value is not None:
            self.index += 1
        return value


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


class HexaUdon:
    """1試合分の状態とロジックすべてを保持するクラス。

    公式フォーマットのキーと同名のフィールド（下記）は試合開始前に確定し、
    以後不変。試合中に変化する状態（各エージェントの位置・燃料など）は
    アンダースコア始まりの内部フィールドに保持し、`bundle()`/`day_info()`
    などのメソッドを通じて公式フォーマットのスナップショットとして取り出す。
    """

    def __init__(
        self,
        seed: int | None = None,
        num_teams: int = 3,
        num_days: int = 5,
        num_agents: int = 4,
        width: int = 12,
        height: int = 10,
        team0_name: str | None = None,
    ):
        if seed is None:
            seed = random.randrange(1_000_000_000)
        self.seed = seed
        self.rng = random.Random(seed)

        num_spots = max(6, min(14, (width * height) // 10))
        num_series = self.rng.randint(3, 5)
        raw = generate_map(self.rng, width, height, num_spots, num_series, num_agents)

        self._terrain: list[str] = raw["terrain"]
        self._width = width
        self._height = height
        self.seriesNames: list[str] = raw["series"]

        # ----- 公式フォーマット「試合開始前のマップ構成フォーマット」のキー -----
        cells = [
            [TERRAIN_CODE[self._terrain[r * width + c]] for c in range(width)]
            for r in range(height)
        ]
        self.map = {"height": height, "width": width, "cells": cells}
        self.spots = [
            {"brand": s["series"], "pos": s["cell"], "stocks": s["maxStock"]}
            for s in raw["spots"]
        ]
        self.agents: list[int] = raw["starts"]  # エージェント初期位置
        self.fuelLimits = 14
        self.players = num_teams
        self.busyThreshold = self.rng.randint(3, 4)
        self.jammedThreshold = self.rng.randint(7, 9)
        self.daySteps = [self.rng.randint(36, 46) for _ in range(num_days)]
        self.daySeconds = [self.rng.choice([5, 7, 10]) for _ in range(num_days)]
        self.startsAt = int(time.time())
        # 各日の回答受付終了時刻: 開始時刻から回答時間を積み上げ（日間に30秒の間隔）
        self.endsAt: list[int] = []
        t = self.startsAt
        for sec in self.daySeconds:
            t += sec
            self.endsAt.append(t)
            t += 30

        # ----- 内部状態 -----
        self._spots_by_cell = {s["pos"]: s for s in self.spots}
        num_supply = 2 if num_agents >= 6 else 1
        self._teams: list[_Team] = []
        for ti in range(num_teams):
            agents = []
            for i, start in enumerate(self.agents):
                is_supply = i >= num_agents - num_supply
                agents.append(
                    _Agent(
                        "supply" if is_supply else "patrol",
                        start,
                        None if is_supply else self.fuelLimits,
                    )
                )
            name = team0_name if (ti == 0 and team0_name) else TEAM_NAMES[ti]
            self._teams.append(_Team(name, agents, random.Random(seed * 1000 + ti)))
        self._traffic_history: list[dict[int, int]] = []
        self.days: list[dict] = []
        # begin_day で確定し execute_day で消費する「進行中の日」の状態
        self._pending: tuple[int, dict[int, str], dict] | None = None

        # ----- ライブ対戦（Play GUI）の進行状態 -----
        self._kinds_submitted = False
        self.current_day = 0

    # ----- 地形・コスト -----

    def _key_of(self, road_states):
        terrain = self._terrain

        def key(cell):
            return terrain_key(terrain[cell], road_states.get(cell, "smooth"))

        return key

    def _road_states_for_day(self, day_index: int) -> dict[int, str]:
        states = {}
        if day_index == 0:
            recent = {}
        else:
            recent = dict(self._traffic_history[-1])
            if day_index >= 2:
                for cell, cnt in self._traffic_history[-2].items():
                    recent[cell] = recent.get(cell, 0) + cnt
        for cell, t in enumerate(self._terrain):
            if t != "road":
                continue
            volume = recent.get(cell, 0) / len(self._teams)
            if volume >= self.jammedThreshold:
                states[cell] = "jammed"
            elif volume >= self.busyThreshold:
                states[cell] = "congested"
            else:
                states[cell] = "smooth"
        return states

    # ----- AI -----

    def _plan_patrol(self, team: _Team, agent_idx: int, agent: _Agent, key_of, steps_left: int):
        dist, prev = dijkstra(agent.cell, self._width, self._height, key_of)
        best = None
        best_score = -1.0
        unaffordable = False
        for cell, spot in self._spots_by_cell.items():
            if team.stock.get(cell, 0) <= 0 or cell in agent.acquired_today:
                continue
            if cell == agent.cell:
                # 現在いるセルへの移動経路は存在しない（既にそこにいる）ため
                # 移動先の候補からは除外する。獲得自体は execute_day 側で
                # 到着有無に関わらず毎ステップ判定される（日をまたいで
                # スポット上に留まっている場合も1ステップ目以降に獲得できる。
                # Q&Aその1 Q7/A7・Q8/A8で確定）。
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
            series = spot["brand"]
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
            low = [a for a in patrols if a.fuel <= self.fuelLimits * team.supply_threshold]
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
            dist, prev = dijkstra(agent.cell, self._width, self._height, key_of)
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
                "fuel": a.fuel if a.fuel is not None else self.fuelLimits,
            }
            for a in team.agents
        ]

    def _day_info(self, day_index: int, road_states: dict[int, str]) -> dict:
        """各日開始時の試合情報フォーマット（チーム0視点）。"""
        return {
            "endsAt": self.endsAt[day_index],
            "day": day_index,  # 初日は 0
            "agents": self._agents_info(self._teams[0]),
            "others": [
                {"id": ti, "agents": self._agents_info(team)}
                for ti, team in enumerate(self._teams)
                if ti != 0
            ],
            "traffics": [
                {"pos": cell, "status": ROAD_STATUS_CODE[state]}
                for cell, state in sorted(road_states.items())
            ],
        }

    def set_team_kinds(self, team_index: int, kinds: list[int]):
        """チームのエージェント種別を差し替える（試合開始前=初日開始前のみ）。"""
        if self.days or self._pending is not None:
            raise ValueError("種別の変更は試合開始前のみ可能です")
        team = self._teams[team_index]
        team.agents = [
            _Agent(
                "supply" if k == KIND_CODE["supply"] else "patrol",
                start,
                None if k == KIND_CODE["supply"] else self.fuelLimits,
            )
            for k, start in zip(kinds, self.agents)
        ]

    def begin_day(self, day_index: int) -> dict:
        """日の開始処理（在庫補充・計画リセット）を行い、公式の試合情報を返す。"""
        if day_index != len(self.days):
            raise ValueError("日は完了した日の次から順番に開始する必要があります")
        road_states = self._road_states_for_day(day_index)

        # 日初期化: 在庫補充・獲得履歴/計画リセット
        for team in self._teams:
            team.stock = {c: s["stocks"] for c, s in self._spots_by_cell.items()}
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
        self._pending = (day_index, road_states, info)
        return info

    def _step_external(self, cursor, agent, key_of, steps_left, terrain) -> int | None:
        """外部提出プランの1コマンドを処理し、移動を開始したら方向コードを返す。

        燃料不足・日内に完了しない移動は待機し、プランは進めない
        （補給後・翌ステップに再試行される）。不正な移動先は読み飛ばす。
        """
        value = cursor.peek()
        if value is None:
            return None  # プラン消化済み → 待機
        if value == -1:
            cursor.take()
            return None  # 計画どおりの待機
        key = key_of(agent.cell)
        if agent.kind == "patrol" and agent.fuel < FUEL_COST[key]:
            return None  # 燃料不足 → 補給されるまで待機（コマンドは保留）
        if steps_left < STEP_COST[key]:
            return None  # 日内に完了しない移動 → 待機
        target = apply_direction(agent.cell, value, self._width, self._height)
        if target is None or terrain[target] == "pond":
            cursor.take()
            return None  # 不正な移動（受付時の検証で通常は弾かれる）→ 無視
        cursor.take()
        if agent.kind == "patrol":
            agent.fuel -= FUEL_COST[key]
        agent.move_remaining = STEP_COST[key]
        agent.move_target = target
        return value

    def execute_day(self, external_plans: dict[int, list[list[int]]] | None = None):
        """begin_day 済みの日を実行する。

        external_plans: チーム番号 → 公式行動計画（エージェントごと）。
        指定されたチームは AI ではなく提出プランに従って行動する。
        """
        if self._pending is None:
            raise ValueError("begin_day が呼ばれていません")
        day_index, road_states, info = self._pending
        self._pending = None
        steps = self.daySteps[day_index]
        key_of = self._key_of(road_states)
        cursors = {
            ti: [_PlanCursor(plan) for plan in plans]
            for ti, plans in (external_plans or {}).items()
        }

        # 各エージェントの行動記録（公式の行動計画フォーマットに変換する）
        actions = [[[] for _ in team.agents] for team in self._teams]
        traffic: dict[int, int] = {}
        terrain = self._terrain

        for k in range(steps):
            steps_left = steps - k
            cells_at_start = [
                [a.cell for a in team.agents] for team in self._teams
            ]

            # 行動決定・移動命令
            for ti, team in enumerate(self._teams):
                team_cursors = cursors.get(ti)
                for ai, agent in enumerate(team.agents):
                    if agent.move_remaining > 0:
                        continue
                    if team_cursors is not None:
                        code = self._step_external(
                            team_cursors[ai], agent, key_of, steps_left, terrain
                        )
                        if code is not None:
                            actions[ti][ai].append(code)
                    elif agent.kind == "patrol":
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
                            direction_code(agent.cell, agent.move_target, self._width)
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
                            direction_code(agent.cell, agent.move_target, self._width)
                        )

            # 移動していないエージェントはこのステップを待機として記録
            for ti, team in enumerate(self._teams):
                for ai, agent in enumerate(team.agents):
                    if agent.move_remaining == 0:
                        actions[ti][ai].append(-1)

            # 移動の反映（公式の反映フェーズ順序: 燃料消費[行動決定時に済]→移動反映→
            # うどん獲得→燃料補給→交通量更新。Q&Aその1 Q6/A6・補足資料で確定）
            for ti, team in enumerate(self._teams):
                for ai, agent in enumerate(team.agents):
                    if agent.move_remaining <= 0:
                        continue
                    agent.move_remaining -= 1
                    if agent.move_remaining > 0:
                        continue
                    agent.cell = agent.move_target
                    agent.move_target = None

            # うどん獲得: 移動で到着したか否かに関わらず、現在地がスポットなら判定する
            # （日をまたいでスポット上に留まっている場合も1ステップ目以降に獲得できる。
            # Q&Aその1 Q7/A7・Q8/A8で確定）
            for ti, team in enumerate(self._teams):
                for agent in team.agents:
                    if agent.kind != "patrol":
                        continue
                    spot = self._spots_by_cell.get(agent.cell)
                    if (
                        spot
                        and team.stock.get(agent.cell, 0) > 0
                        and agent.cell not in agent.acquired_today
                    ):
                        team.stock[agent.cell] -= 1
                        agent.acquired_today.add(agent.cell)
                        team.total += 1
                        team.series_overall.add(spot["brand"])
                        team.series_today.add(spot["brand"])
                        team.claims.pop(agent.cell, None)
                        if agent.target_spot == agent.cell:
                            agent.target_spot = None

            # 補給: 1ステップの間 同セルに居続けた巡回車×補給車
            for ti, team in enumerate(self._teams):
                supplies = [
                    (ai, a) for ai, a in enumerate(team.agents) if a.kind == "supply"
                ]
                for ai, agent in enumerate(team.agents):
                    if agent.kind != "patrol" or agent.fuel >= self.fuelLimits:
                        continue
                    for si, supply in supplies:
                        stayed_together = (
                            supply.cell == agent.cell
                            and cells_at_start[ti][ai] == agent.cell
                            and cells_at_start[ti][si] == supply.cell
                        )
                        if stayed_together:
                            agent.fuel = self.fuelLimits
                            agent.waiting_fuel = False
                            break

            # 交通量（移動反映後のセルへの滞在をカウント。Q&Aその2 Q27/A27で確定）
            for team in self._teams:
                for agent in team.agents:
                    if terrain[agent.cell] == "road":
                        traffic[agent.cell] = traffic.get(agent.cell, 0) + 1

        for team in self._teams:
            team.daily_series_counts.append(len(team.series_today))
        self._traffic_history.append(traffic)

        self.days.append(
            {
                "info": info,
                "plans": [
                    [_compress_waits(agent_actions) for agent_actions in team_actions]
                    for team_actions in actions
                ],
            }
        )

    # ----- 模擬試合（AI対戦）としての実行 -----

    def run(self) -> dict:
        """全日程を AI 同士でシミュレーションし、試合データ一式を返す。"""
        for d in range(len(self.daySteps)):
            self.begin_day(d)
            self.execute_day()
        return self.bundle()

    def bundle(self) -> dict:
        """現在までの試合データ一式（公式フォーマットの集合）を返す。

        ライブ試合では日が進むごとに days が伸びる（途中経過でも呼べる）。
        """
        per_team = []
        for team in self._teams:
            per_team.append(
                {
                    "name": team.name,
                    "seriesCount": len(team.series_overall),
                    "dailySeriesCum": sum(team.daily_series_counts),
                    "totalUdon": team.total,
                }
            )
        ranking = sorted(
            range(len(self._teams)),
            key=lambda i: (
                -per_team[i]["seriesCount"],
                -per_team[i]["dailySeriesCum"],
                -per_team[i]["totalUdon"],
                i,
            ),
        )

        return {
            "format": "hexaudon-official-v1",
            # meta はビジュアライザ用の補助情報（公式フォーマット外）
            "meta": {
                "title": f"サンプル試合 (seed={self.seed})",
                "seed": self.seed,
                "generator": "sample-simulator",
                "teamNames": [team.name for team in self._teams],
                "seriesNames": self.seriesNames,
                "expected": {"perTeam": per_team, "ranking": ranking},
            },
            # 試合開始前のマップ構成フォーマット（公式）
            "match": {
                "startsAt": self.startsAt,
                "daySeconds": self.daySeconds,
                "daySteps": self.daySteps,
                "map": self.map,
                "spots": self.spots,
                "agents": self.agents,
                "fuelLimits": self.fuelLimits,
                "players": self.players,
                "busyThreshold": self.busyThreshold,
                "jammedThreshold": self.jammedThreshold,
            },
            # エージェント種別の回答フォーマット（公式・チームごと）
            "kinds": [
                [KIND_CODE[a.kind] for a in team.agents] for team in self._teams
            ],
            # days[].info: 各日開始時の試合情報フォーマット（公式・チーム0視点）
            # days[].plans: 行動計画の回答フォーマット（公式・チームごと）
            "days": self.days,
        }

    # ----- ライブ対戦（Play GUI）としての実行 -----
    #
    # チーム0（PLAYER_TEAM）を外部クライアントが操作する想定で、
    # submit_kinds() → submit_actions() を日ごとに呼んで1日ずつ進める。

    @property
    def numDays(self) -> int:
        return len(self.daySteps)

    @property
    def finished(self) -> bool:
        return self.current_day >= self.numDays

    @property
    def status(self) -> str:
        if not self._kinds_submitted:
            return "waiting_agents"
        if self.finished:
            return "finished"
        return "waiting_actions"

    @property
    def solo(self) -> bool:
        return len(self._teams) == 1

    @property
    def pending_info(self) -> dict:
        """受付中の日の試合情報（begin_day 済み）。"""
        return self._pending[2]

    def submit_kinds(self, kinds: list[int]):
        if self._kinds_submitted:
            raise HexaUdonError("エージェント種別は提出済みです（試合は開始しています）")
        self.set_team_kinds(PLAYER_TEAM, kinds)
        self._kinds_submitted = True
        self.begin_day(0)

    def submit_actions(self, day: int, plans: list[list[int]]):
        if not self._kinds_submitted:
            raise HexaUdonError("先に POST /api/agents でエージェント種別を提出してください")
        if self.finished:
            raise HexaUdonError("試合は終了しています（POST /api/live/new で新しい試合を開始）")
        if day != self.current_day:
            raise HexaUdonError(f"現在回答を受付中の日は {self.current_day} です")
        self.execute_day({PLAYER_TEAM: plans})
        self.current_day += 1
        if not self.finished:
            self.begin_day(self.current_day)

    def day_info(self, day: int) -> dict:
        if not self._kinds_submitted:
            raise HexaUdonError("先に POST /api/agents でエージェント種別を提出してください")
        if day < self.current_day:
            return self.days[day]["info"]
        if day == self.current_day and not self.finished:
            return self.pending_info
        raise HexaUdonError(f"day {day} はまだ開始していません（現在 day {self.current_day}）")

    def standings(self) -> list[dict]:
        """現時点の順位表（順位順）。"""
        expected = self.bundle()["meta"]["expected"]
        return [
            {"rank": rank + 1, "team": ti, **expected["perTeam"][ti]}
            for rank, ti in enumerate(expected["ranking"])
        ]

    def summary(self) -> dict:
        """GET /api/live 用の進行状況。"""
        out = {
            "live": True,
            "status": self.status,
            "seed": self.seed,
            "day": min(self.current_day, self.numDays - 1),
            "numDays": self.numDays,
            "playerTeam": PLAYER_TEAM,
            "solo": self.solo,
            "standings": self.standings(),
        }
        if self.status == "waiting_agents":
            out["message"] = "POST /api/agents で種別を提出すると試合が始まります"
        elif self.status == "waiting_actions":
            out["message"] = f"POST /api/actions?day={self.current_day} で行動計画を提出してください"
        else:
            out["message"] = "試合終了。GET /api/replay で全記録を取得できます"
        return out


if __name__ == "__main__":
    import json
    import sys

    match = HexaUdon(seed=42).run()
    json.dump(match, sys.stdout, ensure_ascii=False)
