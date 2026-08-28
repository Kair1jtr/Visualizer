"""堅牢性と検証・実行の一貫性の検証。

戦略研究では性能のために `run_day(..., validate=False)` で検証を省くことがある。
そのとき**不正な計画が黙って別の意味になってはならない**（不正状態のまま
シミュレーションが進むと、戦略の評価そのものが無意味になる）。

本ファイルは次の性質を保証する:

    シミュレーションが例外なく完走した ⟺ その行動計画は有効だった

言い換えると、`validate_team_plan()` が有効と判定する計画の集合と、
実行時に例外を出さない計画の集合が一致することを確認する。
"""

from __future__ import annotations

import random
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from simulator import engine, scenarios  # noqa: E402
from simulator.actions import PlanError  # noqa: E402
from simulator.engine import InsufficientFuel  # noqa: E402
from simulator.state import SpotDef  # noqa: E402
from simulator.terrain import AgentKind, Terrain, move_cost  # noqa: E402
from simulator.validation import validate_team_plan  # noqa: E402

PLAIN, ROAD, MOUNTAIN, POND = 0, 1, 2, 3


def build(cells, starts, kinds, day_steps, *, spots=(), fuel_limits=10, num_teams=1):
    return scenarios.minimal_scenario(
        cells=cells,
        spots=list(spots),
        starts=list(starts),
        kinds_by_team=[list(kinds) for _ in range(num_teams)],
        day_steps=tuple(day_steps),
        fuel_limits=fuel_limits,
        busy_threshold=1,
        jammed_threshold=2,
    )


class InvalidPlanAlwaysRaisesTest(unittest.TestCase):
    """検証を省いて不正な計画を実行した場合、必ず例外になること。

    黙って別の挙動（燃料が負・残りを待機で補完）になってはならない。
    """

    def _run(self, cells, day_steps, plan, *, fuel_limits=10):
        state = build(cells, [0], [0], day_steps, fuel_limits=fuel_limits)
        engine.begin_day(state)
        engine.set_plans(state, {0: [plan]})
        engine.simulate_day_steps(state, None)
        engine.end_day(state)
        return state

    def test_insufficient_fuel_raises_and_never_goes_negative(self) -> None:
        """燃料不足の移動は例外になる。燃料が負のまま進行してはならない。"""
        with self.assertRaises(InsufficientFuel):
            self._run([[PLAIN, PLAIN, PLAIN]], (4,), [2, -2], fuel_limits=0)

    def test_plan_shorter_than_day_raises(self) -> None:
        """ステップ合計が足りない計画は例外になる。

        残りを黙って待機で埋めると、〔書式〕の「1日のステップ数と一致する必要がある」
        に反する計画が通ってしまう。
        """
        with self.assertRaises(PlanError) as cm:
            self._run([[PLAIN, PLAIN, PLAIN]], (6,), [2])
        self.assertIn("足りません", str(cm.exception))

    def test_plan_longer_than_day_raises(self) -> None:
        """ステップ合計が超過する計画は例外になる（日終了時に予約が残る）。"""
        with self.assertRaises(PlanError) as cm:
            self._run([[PLAIN, PLAIN, PLAIN]], (1,), [2])
        self.assertIn("超えています", str(cm.exception))

    def test_move_into_pond_raises(self) -> None:
        """池への移動は例外になる。"""
        with self.assertRaises(PlanError):
            self._run([[PLAIN, POND]], (4,), [2, -2])

    def test_move_out_of_map_raises(self) -> None:
        """マップ外への移動は例外になる。"""
        state = build([[PLAIN, PLAIN]], [1], [0], (4,))
        engine.begin_day(state)
        engine.set_plans(state, {0: [[2, -2]]})
        with self.assertRaises(PlanError):
            engine.simulate_day_steps(state, None)

    def test_valid_plan_does_not_raise(self) -> None:
        """有効な計画は例外にならない（上記の変更が正常系を壊していないこと）。"""
        state = self._run([[PLAIN, PLAIN, PLAIN, PLAIN]], (6,), [2, 2, 2])
        agent = state.teams[0].agents[0]
        self.assertEqual(agent.pos, 3)
        self.assertEqual(agent.fuel, 10 - 3)


class ValidationMatchesExecutionTest(unittest.TestCase):
    """`validate_team_plan()` の判定と、実行時に例外を出すかが一致すること。

    片方だけが通ると、検証を省いた探索と省かない本番で挙動が食い違う。
    """

    MAPS = [
        [[PLAIN, PLAIN, PLAIN]],
        [[PLAIN, ROAD, PLAIN]],
        [[MOUNTAIN, PLAIN, POND]],
        [[PLAIN, PLAIN], [PLAIN, PLAIN]],
    ]

    def _consistent(self, cells, day_steps, plan, fuel_limits):
        """検証結果と実行結果が一致するか。(検証がNG, 実行が例外) を返す。"""
        state = build(cells, [0], [0], day_steps, fuel_limits=fuel_limits)
        engine.begin_day(state)
        rejected = validate_team_plan(state, state.teams[0], [plan]) is not None

        probe = build(cells, [0], [0], day_steps, fuel_limits=fuel_limits)
        engine.begin_day(probe)
        engine.set_plans(probe, {0: [plan]})
        try:
            engine.simulate_day_steps(probe, None)
            engine.end_day(probe)
            raised = False
        except PlanError:
            raised = True
        return rejected, raised

    def test_randomized_plans_agree(self) -> None:
        """ランダムな計画300件について、検証と実行の判定が一致すること。"""
        rng = random.Random(20260828)
        mismatches = []
        for _ in range(300):
            cells = rng.choice(self.MAPS)
            day_steps = (rng.randint(1, 8),)
            fuel_limits = rng.randint(0, 4)
            length = rng.randint(1, 4)
            plan = [
                rng.choice([-1, -2, -3, 0, 1, 2, 3, 4, 5]) for _ in range(length)
            ]
            rejected, raised = self._consistent(cells, day_steps, plan, fuel_limits)
            if rejected != raised:
                mismatches.append((cells, day_steps, fuel_limits, plan, rejected, raised))
        self.assertEqual(
            mismatches[:3], [], f"検証と実行の判定が食い違う計画が {len(mismatches)} 件"
        )


class SimulationInvariantTest(unittest.TestCase):
    """有効な計画を大量に流し、公式ルール由来の不変条件が破れないこと。"""

    def _build_valid_plan(self, state, agent, rng):
        """その日の道路状態と燃料を見ながら、実行可能な行動だけを積む。"""
        grid, road, n = state.grid, state.traffic.road_status, state.steps_today
        pos, fuel, used, plan = agent.pos, agent.fuel, 0, []
        is_patrol = agent.kind == AgentKind.PATROL
        while used < n:
            options = []
            steps, cost = move_cost(grid.terrain_at(pos), road.get(pos))
            need = cost if is_patrol else 0
            if used + steps <= n and (not is_patrol or fuel >= need):
                for d in range(6):
                    t = grid.neighbor(pos, d)
                    if t is not None and grid.terrain_at(t) != Terrain.POND:
                        options.append((d, steps, need, t))
            if options and rng.random() < 0.7:
                d, steps, need, t = rng.choice(options)
                plan.append(d)
                used += steps
                fuel -= need
                pos = t
            else:
                k = rng.randint(1, n - used)
                plan.append(-k)
                used += k
        return plan

    def test_invariants_hold_over_many_matches(self) -> None:
        """200試合分の不変条件を確認する。"""
        rng = random.Random(20260829)
        for seed in range(200):
            cells = [[rng.choice([PLAIN, PLAIN, ROAD, MOUNTAIN]) for _ in range(4)] for _ in range(2)]
            n_agents = rng.randint(1, 3)
            starts = rng.sample(range(8), n_agents)
            spots = [SpotDef(pos=7, brand=0, stocks=2)]
            kinds = [rng.choice([0, 0, 1]) for _ in range(n_agents)]
            day_steps = tuple(rng.randint(2, 8) for _ in range(rng.randint(2, 3)))
            state = build(
                cells, starts, kinds, day_steps, spots=spots, fuel_limits=rng.randint(3, 10)
            )

            while not state.finished:
                engine.begin_day(state)
                n = state.steps_today
                before_fuel = {a.agent_id: a.fuel for a in state.teams[0].agents}
                stay_before = sum(state.traffic.stay_today.values())
                plans = [
                    self._build_valid_plan(state, a, rng) for a in state.teams[0].agents
                ]
                self.assertIsNone(
                    validate_team_plan(state, state.teams[0], plans),
                    f"構成した有効計画がリジェクトされた (seed={seed})",
                )
                engine.set_plans(state, {0: plans})
                engine.simulate_day_steps(state, None)

                team = state.teams[0]
                added = sum(state.traffic.stay_today.values()) - stay_before
                self.assertEqual(
                    added, len(team.agents) * n,
                    f"滞在数の増分がエージェント数×ステップ数と不一致 (seed={seed})",
                )
                for a in team.agents:
                    if a.kind == AgentKind.PATROL:
                        self.assertGreaterEqual(a.fuel, 0, f"燃料が負 (seed={seed})")
                        self.assertLessEqual(a.fuel, state.config.fuel_limits)
                    else:
                        self.assertEqual(
                            a.fuel, before_fuel[a.agent_id], "補給車の燃料が変化した"
                        )
                    self.assertNotEqual(
                        state.grid.terrain_at(a.pos), Terrain.POND, "池の上にいる"
                    )
                for s in state.spots:
                    self.assertTrue(0 <= team.spot_stocks[s.pos] <= s.stocks, "在庫が範囲外")
                engine.end_day(state)

            self.assertEqual(
                len(state.teams[0].daily_brand_counts), len(day_steps),
                "日別記録数が日数と一致しない",
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
