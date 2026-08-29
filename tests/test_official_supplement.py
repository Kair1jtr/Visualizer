"""〔補足〕Q&A補足資料（行動詳細）の状態遷移例を再現する統合テスト。

状態設計書 第17.2節「テストケースA」に対応する。
公式資料に数値が明記されている項目のみを期待値として使う。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from simulator import engine, scenarios, tracing  # noqa: E402
from simulator.scenarios import SUPPLEMENT_LABELS  # noqa: E402

A, B, C, D, E, F, G, SUPPLY = range(8)


class OfficialSupplementTest(unittest.TestCase):
    """〔補足〕の例をそのまま流し、記載されている期待結果と突き合わせる。"""

    def setUp(self) -> None:
        self.state, plans = scenarios.official_supplement_scenario()
        self.tracer = tracing.Tracer()
        engine.begin_day(self.state, self.tracer)
        scenarios.apply_supplement_road_status(self.state)
        engine.set_plans(self.state, {0: plans})
        engine.simulate_day_steps(self.state, self.tracer)
        self.team = self.state.teams[0]

    # ----- 前提（初期状態） -----

    def test_initial_setup_matches_supplement(self) -> None:
        """初期配置・燃料上限・在庫が〔補足〕の記述どおりであること。"""
        self.assertEqual(scenarios.SUPPLEMENT_FUEL_LIMIT, 3)
        self.assertEqual(scenarios.SUPPLEMENT_DAY_STEPS, 6)
        self.assertEqual(scenarios.SUPPLEMENT_SPOT_STOCKS, 4)
        self.assertEqual(
            scenarios.SUPPLEMENT_STARTS, [0, 0, 0, 0, 2, 3, 3, 2]
        )

    # ----- A-1: 滞在数 -----

    def test_stay_counts_match_official_table(self) -> None:
        """〔補足〕「滞在数」行の最終累積と一致すること。"""
        actual = dict(sorted(self.state.traffic.stay_today.items()))
        self.assertEqual(actual, scenarios.SUPPLEMENT_EXPECTED_STAY)

    def test_stay_total_equals_agents_times_steps(self) -> None:
        """滞在数の総和 = エージェント数 × ステップ数（毎反映で全員が1セル計上）。"""
        total = sum(self.state.traffic.stay_today.values())
        self.assertEqual(total, 8 * scenarios.SUPPLEMENT_DAY_STEPS)
        self.assertEqual(total, 48)

    def test_each_reflection_adds_exactly_agent_count(self) -> None:
        """各反映フェーズでちょうどエージェント数だけ滞在が増えること。"""
        state, plans = scenarios.official_supplement_scenario()
        engine.begin_day(state)
        scenarios.apply_supplement_road_status(state)
        engine.set_plans(state, {0: plans})

        n = state.steps_today
        prev_total = 0
        for step in range(n + 1):
            state.step = step
            if step > 0:
                engine.reflection_phase(state, None)
                total = sum(state.traffic.stay_today.values())
                self.assertEqual(
                    total - prev_total, 8, f"step {step} の滞在増分が 8 でない"
                )
                prev_total = total
            if step < n:
                engine.action_phase(state, None)

    # ----- A-2 / A-5 / A-6: うどん獲得 -----

    def _acquisitions(self) -> list[tuple[int, int]]:
        """(ステップ, エージェントID) の獲得イベント一覧。"""
        return [
            (e.step, e.agent_id)
            for e in self.tracer.events
            if e.phase.startswith("reflection.3")
        ]

    def test_agent_e_acquires_at_step_1(self) -> None:
        """〔補足〕巡回車E:「スポットからのスタートなのでステップ1でうどんを獲得する」"""
        self.assertIn((1, E), self._acquisitions())

    def test_agent_b_wins_stock_conflict_over_g(self) -> None:
        """〔補足〕巡回車G:「在庫が1のため、ID順で巡回車Bのみが獲得し、巡回車Gは獲得しない」"""
        acquisitions = self._acquisitions()
        b_steps = [s for s, a in acquisitions if a == B]
        g_steps = [s for s, a in acquisitions if a == G]
        self.assertEqual(len(b_steps), 1, "巡回車Bは1玉獲得するはず")
        self.assertEqual(g_steps, [], "巡回車Gは獲得しないはず")
        # BとGは同じステップに到着している
        self.assertEqual(self.state.teams[0].agents[G].pos, 1)

    def test_agent_c_cannot_acquire_stock_exhausted(self) -> None:
        """〔補足〕巡回車C:「スポットに到達しているが、在庫がなくなっているので獲得できない」"""
        self.assertEqual(self.team.agents[C].acquired_spots_today, set())
        self.assertEqual(self.team.spot_stocks[2], 0)

    def test_total_udon_is_four(self) -> None:
        """在庫4がちょうど尽きる（E, F, A, B の4台が獲得）。"""
        self.assertEqual(self.team.total_udon, 4)
        winners = {a for _s, a in self._acquisitions()}
        self.assertEqual(winners, {A, B, E, F})

    def test_acquisition_order_follows_arrival(self) -> None:
        """獲得は到着順（E:1, F:2, A:3, B:4）に発生する。"""
        self.assertEqual(self._acquisitions(), [(1, E), (2, F), (3, A), (4, B)])

    # ----- A-3 / A-4: 補給 -----

    def test_agent_e_stays_full_by_moving_with_supply(self) -> None:
        """〔補足〕巡回車E:「補給車と同時に移動するので燃料は常に最大積載量となる」"""
        self.assertEqual(self.team.agents[E].fuel, scenarios.SUPPLEMENT_FUEL_LIMIT)

    def test_agent_f_never_refueled(self) -> None:
        """〔補足〕巡回車F:「常に補給車の1セル後ろを移動するので、補給が行われない」"""
        refuels = [
            e for e in self.tracer.events
            if e.agent_id == F and e.phase.startswith("reflection.4")
        ]
        self.assertEqual(refuels, [])

    def test_agent_a_refueled_at_step_2(self) -> None:
        """巡回車Aは補給車Aがセル1に到着するステップ2で満タンになる。"""
        refuels = [
            (e.step, e.message) for e in self.tracer.events
            if e.agent_id == A and e.phase.startswith("reflection.4")
        ]
        self.assertEqual(len(refuels), 1)
        self.assertEqual(refuels[0][0], 2)

    # ----- 燃料消費タイミング（U-6 の実証） -----

    def test_fuel_consumed_on_arrival_not_on_reservation(self) -> None:
        """巡回車Aの燃料推移が〔補足〕の表と一致すること。

        3 →(step1) 1 →(step2 補給) 3 →(step3) 1 →(step5) 0

        これは「燃料は移動が**完了する**反映フェーズで消費される」ことを示す。
        予約直後（step2）に消費されるなら、Aは燃料1で必要量2を満たせず
        〔補足〕が有効としている行動計画が不正になってしまう。
        """
        events = [
            (e.step, e.phase, e.message) for e in self.tracer.events
            if e.agent_id == A and e.phase.startswith(("reflection.1", "reflection.4"))
        ]
        steps_and_kind = [(s, p.split(".")[1]) for s, p, _m in events]
        self.assertEqual(
            steps_and_kind,
            [(1, "1_fuel"), (2, "4_refuel"), (3, "1_fuel"), (5, "1_fuel")],
        )
        self.assertEqual(self.team.agents[A].fuel, 0)

    # ----- 最終位置 -----

    def test_final_positions(self) -> None:
        """行動計画どおりに全エージェントが移動していること。"""
        expected = {A: 3, B: 3, C: 2, D: 1, E: 0, F: 1, G: 1, SUPPLY: 0}
        actual = {a.agent_id: a.pos for a in self.team.agents}
        self.assertEqual(actual, expected)

    def test_all_plans_are_valid(self) -> None:
        """〔補足〕の行動計画はすべて有効（リジェクトされない）であること。"""
        state, plans = scenarios.official_supplement_scenario()
        engine.begin_day(state)
        scenarios.apply_supplement_road_status(state)
        from simulator.validation import validate_team_plan

        error = validate_team_plan(state, state.teams[0], plans)
        self.assertIsNone(error, f"有効なはずの計画がリジェクトされた: {error}")


class SupplementRejectionNotesTest(unittest.TestCase):
    """〔補足〕が「エラーになる」と述べているケースを再現する。"""

    def _validate(self, agent_index: int, plan: list[int]):
        from simulator.validation import validate_team_plan

        state, plans = scenarios.official_supplement_scenario()
        engine.begin_day(state)
        scenarios.apply_supplement_road_status(state)
        plans[agent_index] = plan
        return validate_team_plan(state, state.teams[0], plans)

    def test_agent_a_without_trailing_wait_is_rejected(self) -> None:
        """〔補足〕巡回車A:「最後に待機を指定しないとエラーになる」

        A の [2,2,2] は 1+2+2=5 ステップで、6ステップに一致しない。
        """
        error = self._validate(A, [2, 2, 2])
        self.assertIsNotNone(error)
        self.assertIn("ステップ", str(error))

    def test_agent_c_moving_without_enough_steps_is_rejected(self) -> None:
        """〔補足〕巡回車C:「到達までにステップ数が不足しているので、移動を指定するとエラー」

        C が [-2,2,2,2] とすると 2+1+2+2=7 > 6 ステップになる。
        """
        error = self._validate(C, [-2, 2, 2, 2])
        self.assertIsNotNone(error)

    def test_agent_d_moving_without_enough_fuel_is_rejected(self) -> None:
        """〔補足〕巡回車D:「燃料積載量が移動に必要な量を下回るので、移動を指定するとエラー」

        D は待機3のあとセル0→1へ移動し（燃料2消費、残1）、
        さらにセル1（混雑・燃料2）から移動しようとすると燃料が足りない。
        """
        error = self._validate(D, [-3, 2, 2])
        self.assertIsNotNone(error)
        self.assertIn("燃料", str(error))

    def test_agent_f_moving_without_enough_fuel_is_rejected(self) -> None:
        """〔補足〕巡回車F:「セル1まで移動した時点で燃料が移動に必要な量を下回っている」

        F は補給を受けられないので、セル1到達後（燃料1）の移動は不正。
        """
        error = self._validate(F, [5, 5, 5])
        self.assertIsNotNone(error)


if __name__ == "__main__":
    unittest.main(verbosity=2)
