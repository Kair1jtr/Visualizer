"""交通量と道路状態の検証。実装指示書 第6章／状態設計書 第9章。

「今日この道路を使う／避ける」判断が翌日・翌々日の道路状態にどう波及するかを
再現できることを確認する。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from simulator import engine, scenarios  # noqa: E402
from simulator.policies import Policies, TrafficDivision  # noqa: E402
from simulator.terrain import RoadStatus, Terrain  # noqa: E402

PLAIN, ROAD = int(Terrain.PLAIN), int(Terrain.ROAD)


def build_road_map(day_steps, *, busy, jammed, num_teams=1, num_agents=1, policies=None):
    """1行×2列（道路・平地）の最小マップ。全エージェントがセル0から開始する。"""
    kwargs = {} if policies is None else {"policies": policies}
    return scenarios.minimal_scenario(
        cells=[[ROAD, PLAIN]],
        spots=[],
        starts=[0] * num_agents,
        kinds_by_team=[[0] * num_agents for _ in range(num_teams)],
        day_steps=tuple(day_steps),
        fuel_limits=50,
        busy_threshold=busy,
        jammed_threshold=jammed,
        **kwargs,
    )


def play_day(state, plans_by_team):
    engine.begin_day(state)
    status = dict(state.traffic.road_status)
    engine.set_plans(state, plans_by_team)
    engine.simulate_day_steps(state, None)
    engine.end_day(state)
    return status


class RoadStatusByDayTest(unittest.TestCase):
    """C-12 / C-13: 日ごとの道路状態の決まり方。〔要項〕【確定】"""

    def test_day1_is_always_smooth(self) -> None:
        """C-12 1日目の道路状態は全て順調。〔要項〕【確定】"""
        state = build_road_map((4,), busy=1, jammed=2)
        engine.begin_day(state)
        self.assertEqual(state.traffic.road_status, {0: RoadStatus.SMOOTH})

    def test_day2_uses_only_day1_traffic(self) -> None:
        """C-13 2日目は1日目の交通量のみで決まる。〔要項〕【確定】"""
        state = build_road_map((4, 4), busy=3, jammed=5)
        play_day(state, {0: [[-4]]})  # 1日目: セル0(道路)に4ステップ滞在
        self.assertEqual(state.traffic.stay_prev1, {0: 4})
        self.assertEqual(state.traffic.stay_prev2, {})

        engine.begin_day(state)
        # 交通量 = (4 + 0) / 1 = 4 → busy(3) <= 4 < jammed(5) → 混雑
        self.assertEqual(state.traffic.road_status, {0: RoadStatus.CONGESTED})

    def test_day3_uses_previous_two_days(self) -> None:
        """3日目以降は前日＋前々日の交通量で決まる。〔要項〕【確定】"""
        state = build_road_map((4, 4, 4), busy=3, jammed=5)
        play_day(state, {0: [[-4]]})  # 1日目: セル0 に 4
        play_day(state, {0: [[-4]]})  # 2日目: セル0 に 4
        engine.begin_day(state)
        # 交通量 = (4 + 4) / 1 = 8 >= jammed(5) → 渋滞
        self.assertEqual(state.traffic.road_status, {0: RoadStatus.JAMMED})

    def test_threshold_boundaries(self) -> None:
        """判定式の境界。交通量 < busy → 順調、busy <= 交通量 < jammed → 混雑、
        jammed <= 交通量 → 渋滞。〔要項〕【確定】"""
        cases = [(2, RoadStatus.SMOOTH), (3, RoadStatus.CONGESTED), (5, RoadStatus.JAMMED)]
        for stay, expected in cases:
            with self.subTest(stay=stay):
                state = build_road_map((stay, 1), busy=3, jammed=5)
                play_day(state, {0: [[-stay]]})
                engine.begin_day(state)
                self.assertEqual(state.traffic.road_status[0], expected)

    def test_divisor_is_team_count(self) -> None:
        """交通量は全チーム分を合算し、チーム数で割る。〔要項〕【確定】

        2チームが同じ道路に4ステップずつ滞在 → 合算8 ÷ 2チーム = 4。
        1チームが4ステップ滞在した場合と同じ交通量になる。
        """
        state = build_road_map((4, 4), busy=3, jammed=5, num_teams=2)
        engine.begin_day(state)
        engine.set_plans(state, {0: [[-4]], 1: [[-4]]})
        engine.simulate_day_steps(state, None)
        engine.end_day(state)
        self.assertEqual(state.traffic.stay_prev1, {0: 8}, "全チーム分を合算して保持する")

        engine.begin_day(state)
        self.assertEqual(engine.traffic_volume(state, 0), 4.0)
        self.assertEqual(state.traffic.road_status[0], RoadStatus.CONGESTED)


class TrafficPropagationTest(unittest.TestCase):
    """実装指示書 第6章: 道路の使用／回避が翌日・翌々日へ波及すること。"""

    def test_congestion_builds_up_and_recovers(self) -> None:
        """道路に留まると混雑→渋滞へ進み、離れると2日後に回復する。

        1日目: 道路に4ステップ滞在        → 道路状態は順調（初日は必ず順調）
        2日目: 前日4 ÷ 1 = 4              → 混雑（busy=3 <= 4 < jammed=5）
        3日目: 前日1 + 前々日4 = 5        → 渋滞（5 >= jammed=5）
        4日目: 前日0 + 前々日1 = 1        → 順調（1 < busy=3）
        """
        state = build_road_map((4, 4, 4, 4), busy=3, jammed=5)

        d1 = play_day(state, {0: [[-4]]})  # 道路に滞在
        self.assertEqual(d1[0], RoadStatus.SMOOTH, "初日は必ず順調")

        # 2日目は混雑。混雑した道路(2ステップ)を1回使ってセル1へ退避する
        d2 = play_day(state, {0: [[2, -2]]})
        self.assertEqual(d2[0], RoadStatus.CONGESTED)
        self.assertEqual(state.traffic.stay_prev1, {0: 1, 1: 3}, "移動中は出発セルに計上")

        d3 = play_day(state, {0: [[-4]]})  # セル1(平地)で待機。道路を使わない
        self.assertEqual(d3[0], RoadStatus.JAMMED, "前日1+前々日4=5 で渋滞")

        engine.begin_day(state)
        self.assertEqual(
            state.traffic.road_status[0], RoadStatus.SMOOTH, "道路を避けたので回復する"
        )

    def test_avoiding_road_keeps_it_smooth(self) -> None:
        """一度も道路に滞在しなければ、道路は順調のままである。

        初日に順調な道路(1ステップ)を通ってセル1へ抜けると、
        到着は1ステップ目なので道路セルの滞在は 0 になる。
        """
        state = build_road_map((4, 4, 4), busy=1, jammed=2)
        play_day(state, {0: [[2, -3]]})  # 1日目: セル1(平地)へ退避
        self.assertEqual(state.traffic.stay_prev1.get(0, 0), 0, "道路に滞在していない")

        play_day(state, {0: [[-4]]})  # 2日目: セル1で待機
        engine.begin_day(state)
        self.assertEqual(state.traffic.road_status[0], RoadStatus.SMOOTH)

    def test_in_transit_agent_counted_at_origin(self) -> None:
        """移動中のエージェントは出発セルに計上される。〔補足〕滞在数【確定】

        2ステップ以上かかる移動でないと「移動中」の期間が生じないため、
        平地(2ステップ)を出発地にする。
        """
        state = scenarios.minimal_scenario(
            cells=[[PLAIN, ROAD]],
            spots=[],
            starts=[0],
            kinds_by_team=[[0]],
            day_steps=(4,),
            fuel_limits=50,
            busy_threshold=1,
            jammed_threshold=2,
        )
        engine.begin_day(state)
        engine.set_plans(state, {0: [[2, -2]]})  # 平地(2ステップ)でセル1へ
        engine.simulate_day_steps(state, None)
        # step1 は移動中なので出発セル0、step2〜4 は到着後のセル1
        self.assertEqual(state.traffic.stay_today, {0: 1, 1: 3})


class TrafficDivisionPolicyTest(unittest.TestCase):
    """U-3: 交通量の除算方式を差し替えられること。"""

    def _status_with(self, division: TrafficDivision, stay: int, num_teams: int):
        policies = Policies(traffic_division=division)
        state = build_road_map(
            (stay, 1), busy=3, jammed=5, num_teams=num_teams, policies=policies
        )
        engine.begin_day(state)
        engine.set_plans(state, {t: [[-stay]] for t in range(num_teams)})
        engine.simulate_day_steps(state, None)
        engine.end_day(state)
        engine.begin_day(state)
        return state.traffic.road_status[0]

    def test_exact_and_floor_agree_for_integer_thresholds(self) -> None:
        """閾値が正の整数なので EXACT と FLOOR は必ず一致する。〔Q30〕【確定】

        policies.py の TrafficDivision の説明に書いた性質を実際に確認する。
        """
        # stay=0 は day_steps=0（公式範囲外）かつ [-0] が方向コード0の移動になるため除く
        for num_teams in (1, 2, 3):
            for stay in range(1, 13):
                with self.subTest(num_teams=num_teams, stay=stay):
                    self.assertEqual(
                        self._status_with(TrafficDivision.EXACT, stay, num_teams),
                        self._status_with(TrafficDivision.FLOOR, stay, num_teams),
                    )

    def test_ceil_can_differ_from_exact(self) -> None:
        """CEIL は EXACT と結果が変わりうる（U-3 が実際に影響する場合）。

        2チームが合計5ステップ滞在 → 交通量 2.5。
        EXACT/FLOOR なら busy=3 未満で順調、CEIL なら 3 となり混雑。
        """
        policies_exact = Policies(traffic_division=TrafficDivision.EXACT)
        policies_ceil = Policies(traffic_division=TrafficDivision.CEIL)

        def status(policies):
            state = build_road_map((3, 1), busy=3, jammed=5, num_teams=2, policies=policies)
            engine.begin_day(state)
            # チーム0が3ステップ、チーム1が2ステップ滞在してから移動 → 合計5
            engine.set_plans(state, {0: [[-3]], 1: [[-2, 2]]})
            engine.simulate_day_steps(state, None)
            engine.end_day(state)
            engine.begin_day(state)
            return state.traffic.stay_prev1.get(0), state.traffic.road_status[0]

        total_exact, st_exact = status(policies_exact)
        total_ceil, st_ceil = status(policies_ceil)
        self.assertEqual(total_exact, 5, "合算滞在数は 5（チーム0が3、チーム1が2）")
        self.assertEqual(total_ceil, 5)
        self.assertEqual(st_exact, RoadStatus.SMOOTH, "2.5 < 3 なので順調")
        self.assertEqual(st_ceil, RoadStatus.CONGESTED, "切り上げると 3 で混雑")


if __name__ == "__main__":
    unittest.main(verbosity=2)
