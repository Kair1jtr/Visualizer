"""ルール単体の検証。状態設計書 第17.4節「テストケースC」に対応する。

各テストの docstring に、根拠となる公式資料の項目を記す。
盤面は検証用に構成した最小構成であり、公式のマップサイズ範囲を下回る
（本番設定ではない）。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from simulator import engine, scenarios  # noqa: E402
from simulator.state import SpotDef  # noqa: E402
from simulator.terrain import RoadStatus, Terrain  # noqa: E402
from simulator.tracing import Tracer  # noqa: E402
from simulator.validation import validate_team_plan  # noqa: E402

PLAIN, ROAD, MOUNTAIN, POND = (
    int(Terrain.PLAIN),
    int(Terrain.ROAD),
    int(Terrain.MOUNTAIN),
    int(Terrain.POND),
)


def build(
    cells,
    starts,
    kinds,
    day_steps,
    *,
    spots=(),
    fuel_limits=10,
    busy=1,
    jammed=2,
    num_teams=1,
):
    kinds_by_team = [list(kinds) for _ in range(num_teams)]
    return scenarios.minimal_scenario(
        cells=cells,
        spots=list(spots),
        starts=list(starts),
        kinds_by_team=kinds_by_team,
        day_steps=tuple(day_steps),
        fuel_limits=fuel_limits,
        busy_threshold=busy,
        jammed_threshold=jammed,
    )


def run_one_day(state, plans, *, road_status=None, tracer=None):
    """1日を進める（検証済み前提。road_status を与えると道路状態を固定する）。"""
    engine.begin_day(state, tracer)
    if road_status is not None:
        state.traffic.traffics = dict(road_status)
    engine.set_plans(state, {0: plans})
    engine.simulate_day_steps(state, tracer)
    engine.end_day(state, tracer)
    return state


def arrival_step(tracer: Tracer, agent_id: int) -> int | None:
    """そのエージェントが最初に移動を完了したステップ。"""
    for e in tracer.events:
        if e.agent_id == agent_id and e.phase.startswith("reflection.2") and "移動" in e.message:
            return e.step
    return None


class MoveCostTest(unittest.TestCase):
    """C-1〜C-3: 地形ごとの移動ステップ数と消費燃料。〔要項〕表1【確定】"""

    def _move_once(self, terrain_code, road_status=None, fuel_limits=10):
        cells = [[terrain_code, PLAIN]]
        state = build(cells, [0], [0], (8,), fuel_limits=fuel_limits)
        tracer = Tracer()
        status = {0: road_status} if road_status is not None else None
        # 移動1回 + 残りは待機で合計8ステップにする
        cost = {PLAIN: 2, MOUNTAIN: 3}.get(terrain_code)
        if cost is None:
            cost = {RoadStatus.SMOOTH: 1, RoadStatus.CONGESTED: 2, RoadStatus.JAMMED: 4}[road_status]
        run_one_day(state, [[2, -(8 - cost)]], road_status=status, tracer=tracer)
        agent = state.teams[0].agents[0]
        return arrival_step(tracer, 0), fuel_limits - agent.fuel

    def test_plain_costs_2_steps_and_1_fuel(self) -> None:
        """C-1 平地: 2ステップ・燃料1。〔要項〕表1【確定】"""
        self.assertEqual(self._move_once(PLAIN), (2, 1))

    def test_mountain_costs_3_steps_and_2_fuel(self) -> None:
        """C-2 山地: 3ステップ・燃料2。〔要項〕表1【確定】"""
        self.assertEqual(self._move_once(MOUNTAIN), (3, 2))

    def test_road_smooth_costs_1_step_and_2_fuel(self) -> None:
        """C-3 道路(順調): 1ステップ・燃料2。〔要項〕表1【確定】"""
        self.assertEqual(self._move_once(ROAD, RoadStatus.SMOOTH), (1, 2))

    def test_road_congested_costs_2_steps_and_2_fuel(self) -> None:
        """C-3 道路(混雑): 2ステップ・燃料2。〔要項〕表1【確定】"""
        self.assertEqual(self._move_once(ROAD, RoadStatus.CONGESTED), (2, 2))

    def test_road_jammed_costs_4_steps_and_2_fuel(self) -> None:
        """C-3 道路(渋滞): 4ステップ・燃料2。〔要項〕表1【確定】"""
        self.assertEqual(self._move_once(ROAD, RoadStatus.JAMMED), (4, 2))

    def test_cost_uses_origin_terrain_not_destination(self) -> None:
        """移動コストは**移動元**の地形で決まる。〔要項〕〔Q10〕〔Q25〕【確定】

        平地→山地 の移動は 2ステップ・燃料1（山地の 3/2 ではない）。
        """
        cells = [[PLAIN, MOUNTAIN]]
        state = build(cells, [0], [0], (6,))
        tracer = Tracer()
        run_one_day(state, [[2, -4]], tracer=tracer)
        self.assertEqual(arrival_step(tracer, 0), 2)
        self.assertEqual(state.teams[0].agents[0].fuel, 10 - 1)


class WaitTest(unittest.TestCase):
    """C-4: 待機。〔Q16〕〔Q49〕【確定】"""

    def test_wait_consumes_given_steps_and_no_fuel(self) -> None:
        """待機は指定ステップを消費し、燃料を消費しない。地形にも影響されない。"""
        cells = [[MOUNTAIN, PLAIN]]
        state = build(cells, [0], [0], (5,))
        run_one_day(state, [[-5]])
        agent = state.teams[0].agents[0]
        self.assertEqual(agent.pos, 0)
        self.assertEqual(agent.fuel, 10, "待機で燃料が減ってはいけない〔Q16〕")

    def test_wait_is_independent_of_terrain(self) -> None:
        """-3 は地形によらず3ステップ。〔Q49〕【確定】"""
        for terrain in (PLAIN, MOUNTAIN, ROAD):
            with self.subTest(terrain=terrain):
                cells = [[terrain, PLAIN]]
                state = build(cells, [0], [0], (5,))
                tracer = Tracer()
                status = {0: RoadStatus.JAMMED} if terrain == ROAD else None
                run_one_day(state, [[-3, -2]], road_status=status, tracer=tracer)
                self.assertEqual(state.teams[0].agents[0].pos, 0)


class PhaseBoundaryTest(unittest.TestCase):
    """C-5 / C-6: 0ステップ目と最終ステップの扱い。〔Q6〕〔Q7〕〔Q27〕【確定】"""

    def test_step0_has_no_acquisition_and_no_traffic(self) -> None:
        """0ステップ目はアクションのみ。獲得も交通量カウントも起きない。"""
        cells = [[ROAD, PLAIN]]
        spots = [SpotDef(pos=0, brand=0, stocks=2)]
        state = build(cells, [0], [0], (4,), spots=spots)
        engine.begin_day(state)
        state.traffic.traffics = {0: RoadStatus.SMOOTH}
        engine.set_plans(state, {0: [[-4]]})

        state.step = 0
        engine.action_phase(state, None)
        self.assertEqual(state.teams[0].total_udon, 0, "0ステップ目に獲得してはいけない〔Q7〕")
        self.assertEqual(state.traffic.stay_today, {}, "0ステップ目に交通量を数えてはいけない〔Q27〕")

        state.step = 1
        engine.reflection_phase(state, None)
        self.assertEqual(state.teams[0].total_udon, 1, "1ステップ目以降は獲得される〔Q7〕")
        self.assertEqual(state.traffic.stay_today, {0: 1})

    def test_reflection_count_equals_day_steps(self) -> None:
        """反映は N 回（ステップ1〜N）、アクションは N 回（ステップ0〜N-1）。"""
        cells = [[ROAD, PLAIN]]
        state = build(cells, [0], [0], (5,))
        engine.begin_day(state)
        state.traffic.traffics = {0: RoadStatus.SMOOTH}
        engine.set_plans(state, {0: [[-5]]})
        engine.simulate_day_steps(state, None)
        # 全滞在数 = エージェント1体 × 5反映
        self.assertEqual(sum(state.traffic.stay_today.values()), 5)


class SpotAcquisitionTest(unittest.TestCase):
    """C-7〜C-9: うどん獲得と在庫。〔要項〕〔Q7〕〔Q8〕〔Q17〕【確定】"""

    def test_same_patrol_same_spot_same_day_only_once(self) -> None:
        """C-7 同一巡回車・同一スポット・同一日は1玉まで。〔要項〕【確定】

        セル1のスポットへ行き、離れて、戻る（合計6ステップ）。獲得は1回だけ。
        """
        cells = [[PLAIN, PLAIN, PLAIN]]
        spots = [SpotDef(pos=1, brand=0, stocks=5)]
        state = build(cells, [0], [0], (6,), spots=spots)
        run_one_day(state, [[2, 5, 2]])
        team = state.teams[0]
        self.assertEqual(team.agents[0].pos, 1)
        self.assertEqual(team.total_udon, 1, "同じ日に同じスポットからは1玉まで")
        self.assertEqual(team.spot_stocks[1], 4)

    def test_next_day_allows_reacquisition_while_staying(self) -> None:
        """C-8 日が変われば、同じスポットに留まったままでも再度獲得できる。

        〔Q7〕「1ステップ目以降にスポットに滞在していれば獲得される」
        〔Q8〕〔Q17〕「日が変わった後の待機行動であれば獲得できます」
        """
        cells = [[PLAIN, PLAIN]]
        spots = [SpotDef(pos=1, brand=0, stocks=5)]
        state = build(cells, [0], [0], (4, 4), spots=spots)

        run_one_day(state, [[2, -2]])  # 1日目: セル1へ移動して獲得
        self.assertEqual(state.teams[0].total_udon, 1)

        run_one_day(state, [[-4]])  # 2日目: セル1で待機し続ける
        self.assertEqual(state.teams[0].total_udon, 2, "日が変われば再度獲得できる〔Q8〕")

    def test_stock_refilled_each_day(self) -> None:
        """C-9 スポット在庫は各日の開始時に最大在庫数まで補充される。〔要項〕【確定】"""
        cells = [[PLAIN, PLAIN]]
        spots = [SpotDef(pos=1, brand=0, stocks=2)]
        state = build(cells, [0], [0], (4, 4), spots=spots)

        run_one_day(state, [[2, -2]])
        self.assertEqual(state.teams[0].spot_stocks[1], 1)

        engine.begin_day(state)
        self.assertEqual(state.teams[0].spot_stocks[1], 2, "翌日は最大在庫まで補充される")

    def test_stock_zero_blocks_acquisition(self) -> None:
        """在庫が0なら到着しても獲得できない。〔要項〕【確定】"""
        cells = [[PLAIN, PLAIN]]
        spots = [SpotDef(pos=1, brand=0, stocks=1)]
        state = build(cells, [0, 0], [0, 0], (4,), spots=spots)
        run_one_day(state, [[2, -2], [2, -2]])
        team = state.teams[0]
        self.assertEqual(team.total_udon, 1, "在庫1なので1台しか獲得できない")
        self.assertEqual(team.spot_stocks[1], 0)

    def test_simultaneous_arrival_resolved_by_agent_id(self) -> None:
        """同時到着は ID の若い順に獲得する。〔Q26〕【確定】"""
        cells = [[PLAIN, PLAIN]]
        spots = [SpotDef(pos=1, brand=0, stocks=1)]
        state = build(cells, [0, 0], [0, 0], (4,), spots=spots)
        run_one_day(state, [[2, -2], [2, -2]])
        team = state.teams[0]
        self.assertEqual(team.agents[0].acquired_spots_today, {1}, "ID 0 が獲得する")
        self.assertEqual(team.agents[1].acquired_spots_today, set(), "ID 1 は獲得しない")

    def test_stock_is_independent_per_team(self) -> None:
        """在庫はチームごとに独立。他チームの獲得で自チーム在庫は減らない。〔要項〕【確定】"""
        cells = [[PLAIN, PLAIN]]
        spots = [SpotDef(pos=1, brand=0, stocks=1)]
        state = build(cells, [0], [0], (4,), spots=spots, num_teams=2)
        engine.begin_day(state)
        engine.set_plans(state, {0: [[2, -2]], 1: [[2, -2]]})
        engine.simulate_day_steps(state, None)
        for team in state.teams:
            self.assertEqual(team.total_udon, 1, f"チーム{team.id} も獲得できる")
            self.assertEqual(team.spot_stocks[1], 0)


class RefuelTest(unittest.TestCase):
    """燃料補給。〔要項〕〔Q22〕〔Q48〕【確定】"""

    def test_refuel_when_sharing_cell_with_supply(self) -> None:
        """補給車と同じセルにいる巡回車は上限まで回復する。〔要項〕【確定】"""
        cells = [[PLAIN, PLAIN]]
        # 巡回車(id0)と補給車(id1)が同じセルから同じセルへ一緒に移動する
        state = build(cells, [0, 0], [0, 1], (4,), fuel_limits=5)
        run_one_day(state, [[2, -2], [2, -2]])
        patrol = state.teams[0].agents[0]
        self.assertEqual(patrol.pos, 1)
        self.assertEqual(patrol.fuel, 5, "同時移動なら補給される〔Q22〕〔Q48〕")

    def test_supply_vehicle_consumes_no_fuel(self) -> None:
        """補給車は燃料を使わずに移動する。〔要項〕【確定】"""
        cells = [[MOUNTAIN, PLAIN]]
        state = build(cells, [0], [1], (5,), fuel_limits=5)
        run_one_day(state, [[2, -2]])
        supply = state.teams[0].agents[0]
        self.assertEqual(supply.pos, 1)
        self.assertEqual(supply.fuel, 5)

    def test_refuel_on_the_final_step(self) -> None:
        """その日の最後のステップで同じセルに移動した場合も補給される。〔Q22〕【確定】

        「移動の後に補給が行われるので、最後のステップで補給されます」
        （〔補足〕巡回車E と補給車A の関係）
        """
        cells = [[PLAIN, PLAIN]]
        state = build(cells, [0, 0], [0, 1], (2,), fuel_limits=5)
        run_one_day(state, [[2], [2]])  # 平地2ステップ＝最終ステップで到着
        patrol = state.teams[0].agents[0]
        self.assertEqual(patrol.pos, 1)
        self.assertEqual(patrol.fuel, 5, "最終ステップでも補給される〔Q22〕")

    def test_one_supply_refuels_multiple_patrols(self) -> None:
        """1台の補給車が同じセルの複数の巡回車に補給できる。〔要項〕【確定】"""
        cells = [[PLAIN, PLAIN]]
        state = build(cells, [0, 0, 0], [0, 0, 1], (2,), fuel_limits=5)
        run_one_day(state, [[2], [2], [2]])
        fuels = [a.fuel for a in state.teams[0].agents[:2]]
        self.assertEqual(fuels, [5, 5])

    def test_multiple_supply_vehicles(self) -> None:
        """補給車が複数いても問題なく補給される。〔要項〕【確定】"""
        cells = [[PLAIN, PLAIN]]
        state = build(cells, [0, 0, 0], [0, 1, 1], (2,), fuel_limits=5)
        run_one_day(state, [[2], [2], [-2]])
        self.assertEqual(state.teams[0].agents[0].fuel, 5)

    def test_no_refuel_when_supply_has_moved_away(self) -> None:
        """補給車を1セル後ろから追走しても補給されない。〔Q22〕【確定】

        〔補足〕巡回車F と補給車A の関係を最小構成で再現する。
        """
        cells = [[PLAIN, PLAIN, PLAIN]]
        # 巡回車(id0)はセル0、補給車(id1)はセル1。両者が同時に右へ移動する。
        state = build(cells, [0, 1], [0, 1], (4,), fuel_limits=5)
        run_one_day(state, [[2, -2], [2, -2]])
        patrol, supply = state.teams[0].agents
        self.assertEqual(patrol.pos, 1)
        self.assertEqual(supply.pos, 2)
        self.assertEqual(patrol.fuel, 4, "補給車は先に進んでいるので補給されない")


class MovementRuleTest(unittest.TestCase):
    """移動の基本ルール。〔要項〕【確定】"""

    def test_agents_can_share_a_cell(self) -> None:
        """他のエージェントが滞在しているセルにも移動できる。〔要項〕【確定】"""
        cells = [[PLAIN, PLAIN]]
        state = build(cells, [0, 1], [0, 0], (2,))
        run_one_day(state, [[2], [-2]])
        a0, a1 = state.teams[0].agents
        self.assertEqual(a0.pos, 1)
        self.assertEqual(a1.pos, 1, "同じセルに複数のエージェントが滞在できる")

    def test_agents_of_different_teams_share_a_cell(self) -> None:
        """他チームのエージェントとも同じセルに滞在できる（初期位置は全チーム共通〔Q38〕）。"""
        cells = [[PLAIN, PLAIN]]
        state = build(cells, [0], [0], (2,), num_teams=2)
        engine.begin_day(state)
        engine.set_plans(state, {0: [[2]], 1: [[2]]})
        engine.simulate_day_steps(state, None)
        self.assertEqual(state.teams[0].agents[0].pos, state.teams[1].agents[0].pos)


class InputGuardTest(unittest.TestCase):
    """公式仕様上あり得ない入力を黙って受け入れないこと。"""

    def test_duplicate_spot_on_one_cell_is_rejected(self) -> None:
        """1セルに複数スポットは存在しない。〔Q18〕〔Q34〕【確定】

        黙って片方を捨てると結果が静かに狂うため、入力段階で弾く。
        """
        with self.assertRaises(ValueError) as cm:
            build(
                [[PLAIN, PLAIN]], [0], [0], (2,),
                spots=[SpotDef(pos=1, brand=0, stocks=3), SpotDef(pos=1, brand=9, stocks=3)],
            )
        self.assertIn("1セルに複数のスポット", str(cm.exception))


class RejectionTest(unittest.TestCase):
    """C-10 / C-11: リジェクト条件。〔要項〕〔書式〕〔Q6〕【確定】"""

    def _validate(self, state, plans):
        engine.begin_day(state)
        if state.map.road_cells():
            state.traffic.traffics = {c: RoadStatus.SMOOTH for c in state.map.road_cells()}
        return validate_team_plan(state, state.teams[0], plans)

    def test_move_into_pond_is_rejected(self) -> None:
        """池への移動は不正。〔要項〕【確定】"""
        state = build([[PLAIN, POND]], [0], [0], (2,))
        error = self._validate(state, [[2]])
        self.assertIsNotNone(error)
        self.assertIn("池", str(error))

    def test_move_out_of_map_is_rejected(self) -> None:
        """マップ外への移動は不正。トーラスではない。〔Q2〕【確定】"""
        state = build([[PLAIN, PLAIN]], [1], [0], (2,))
        error = self._validate(state, [[2]])
        self.assertIsNotNone(error)
        self.assertIn("マップ外", str(error))

    def test_step_total_mismatch_is_rejected(self) -> None:
        """ステップ合計が一致しない回答は不正。〔書式〕【確定】"""
        state = build([[PLAIN, PLAIN]], [0], [0], (5,))
        error = self._validate(state, [[2]])  # 2ステップしかない
        self.assertIsNotNone(error)
        self.assertIn("ステップ", str(error))

    def test_insufficient_fuel_is_rejected(self) -> None:
        """燃料が不足する移動は不正。〔要項〕〔Q6〕【確定】"""
        state = build([[PLAIN, PLAIN, PLAIN]], [0], [0], (4,), fuel_limits=1)
        error = self._validate(state, [[2, 2]])  # 燃料1で2回移動（各1消費）
        self.assertIsNotNone(error)
        self.assertIn("燃料", str(error))

    def test_move_not_completing_within_day_is_rejected(self) -> None:
        """残りステップで完了できない移動は不正。〔要項〕〔Q6〕【確定】"""
        state = build([[PLAIN, PLAIN, PLAIN]], [0], [0], (3,))
        error = self._validate(state, [[2, 2]])  # 2+2=4 > 3
        self.assertIsNotNone(error)

    def test_out_of_range_value_is_rejected(self) -> None:
        """0〜5 でも -1 以下でもない値は不正。〔書式〕【確定】"""
        state = build([[PLAIN, PLAIN]], [0], [0], (2,))
        error = self._validate(state, [[6]])
        self.assertIsNotNone(error)

    def test_wrong_agent_count_is_rejected(self) -> None:
        """要素数がエージェント数と一致しない回答は不正。〔書式〕【確定】"""
        state = build([[PLAIN, PLAIN]], [0, 1], [0, 0], (2,))
        error = self._validate(state, [[-2]])
        self.assertIsNotNone(error)

    def test_one_invalid_agent_rejects_whole_answer(self) -> None:
        """C-11 1エージェントでも不正なら全体をリジェクトする。〔書式〕【確定】"""
        state = build([[PLAIN, POND]], [0, 0], [0, 0], (2,))
        error = self._validate(state, [[-2], [2]])  # 2体目だけ不正
        self.assertIsNotNone(error, "1体でも不正なら全体が不正")

    def test_rejected_team_waits_whole_day(self) -> None:
        """リジェクト時は全エージェントが最終ステップまで待機。〔書式〕〔Q55〕【確定】"""
        state = build([[PLAIN, PLAIN]], [0], [0], (4,))
        results = engine.run_day(state, {0: [[2]]})  # ステップ数不一致
        self.assertIsNotNone(results[0])
        self.assertEqual(state.teams[0].agents[0].pos, 0, "移動せずその場で待機する")

    def test_missing_answer_waits_whole_day(self) -> None:
        """回答未提出でも同じ扱い。〔書式〕〔Q55〕【確定】"""
        state = build([[PLAIN, PLAIN]], [0], [0], (4,))
        results = engine.run_day(state, {})
        self.assertIsNotNone(results[0])
        self.assertEqual(state.teams[0].agents[0].pos, 0)


class DayTransitionTest(unittest.TestCase):
    """日の切り替わり。〔要項〕【確定】 状態設計書 第11.2節。"""

    def test_position_and_fuel_carry_over(self) -> None:
        """位置と燃料は翌日へ引き継がれる。〔要項〕【確定】"""
        cells = [[PLAIN, PLAIN, PLAIN]]
        state = build(cells, [0], [0], (2, 2), fuel_limits=5)
        run_one_day(state, [[2]])
        agent = state.teams[0].agents[0]
        self.assertEqual((agent.pos, agent.fuel), (1, 4))

        engine.begin_day(state)
        self.assertEqual((agent.pos, agent.fuel), (1, 4), "位置・燃料は引き継ぐ")

    def test_today_sets_are_reset(self) -> None:
        """当日取得済み集合・当日獲得系列は日開始時にリセットされる。"""
        cells = [[PLAIN, PLAIN]]
        spots = [SpotDef(pos=1, brand=7, stocks=3)]
        state = build(cells, [0], [0], (4, 4), spots=spots)
        run_one_day(state, [[2, -2]])
        team = state.teams[0]
        self.assertEqual(team.brands_today, {7})
        self.assertEqual(team.agents[0].acquired_spots_today, {1})

        engine.begin_day(state)
        self.assertEqual(team.brands_today, set())
        self.assertEqual(team.agents[0].acquired_spots_today, set())
        self.assertEqual(team.brands_all, {7}, "全期間の系列は引き継ぐ")

    def test_daily_brand_counts_recorded(self) -> None:
        """日ごとの獲得種類数が記録される（勝敗②の材料）。〔要項〕【確定】

        1日目の終了時にスポット上に留まらない配置にしている
        （留まると翌日1ステップ目に再獲得するため。
        その挙動は test_day_starting_on_spot_reacquires_it で別途検証する）。
        """
        cells = [[PLAIN, PLAIN, PLAIN, PLAIN]]
        spots = [SpotDef(pos=1, brand=0, stocks=3), SpotDef(pos=3, brand=1, stocks=3)]
        state = build(cells, [0], [0], (4, 4), spots=spots)
        run_one_day(state, [[2, 2]])  # 1日目: セル1(系列0)を通ってセル2で終了
        run_one_day(state, [[2, -2]])  # 2日目: セル3(系列1)へ
        team = state.teams[0]
        self.assertEqual(team.daily_brand_counts, [1, 1])
        self.assertEqual(team.brand_count, 2)
        self.assertEqual(team.daily_brand_cumulative, 2)

    def test_day_starting_on_spot_reacquires_it(self) -> None:
        """日開始時にスポット上にいると、翌日の1ステップ目に再度獲得する。

        〔Q7〕「各日の0ステップ目では獲得は行われませんが、1ステップ目以降に
        スポットに滞在していればうどんは獲得されます」【確定】
        〔補足〕巡回車E と同じ状況。移動中（未到着）でも出発セルに滞在している
        扱いになるため、前日にスポットへ到着してそのまま次の日を迎えると獲得できる。
        """
        cells = [[PLAIN, PLAIN, PLAIN]]
        spots = [SpotDef(pos=1, brand=0, stocks=3), SpotDef(pos=2, brand=1, stocks=3)]
        state = build(cells, [0], [0], (4, 4), spots=spots)
        run_one_day(state, [[2, -2]])  # 1日目: セル1(系列0)で終了
        self.assertEqual(state.teams[0].daily_brand_counts, [1])

        tracer = Tracer()
        run_one_day(state, [[2, -2]], tracer=tracer)  # 2日目: セル1から出発しセル2へ
        acquisitions = [
            (e.step, e.message) for e in tracer.events if e.phase.startswith("reflection.3")
        ]
        self.assertEqual(len(acquisitions), 2, "セル1の再獲得とセル2の獲得で2件")
        self.assertEqual(acquisitions[0][0], 1, "セル1の再獲得は1ステップ目〔Q7〕")
        self.assertEqual(state.teams[0].daily_brand_counts, [1, 2])

    def test_match_finishes_after_all_days(self) -> None:
        """指定日数を終えると試合終了になる。"""
        state = build([[PLAIN, PLAIN]], [0], [0], (2, 2))
        self.assertFalse(state.finished)
        run_one_day(state, [[-2]])
        self.assertFalse(state.finished)
        run_one_day(state, [[-2]])
        self.assertTrue(state.finished)


class ScoringTest(unittest.TestCase):
    """勝敗判定。〔要項〕【確定】"""

    def test_ranking_priority_brand_count_first(self) -> None:
        """① 種類数が多いチームが上位。〔要項〕【確定】"""
        state = build([[PLAIN, PLAIN]], [0], [0], (2,), num_teams=2)
        state.teams[0].brands_all = {0}
        state.teams[0].total_udon = 100
        state.teams[1].brands_all = {0, 1}
        state.teams[1].total_udon = 1
        self.assertEqual([t.id for t in state.ranking()], [1, 0])

    def test_ranking_uses_daily_cumulative_second(self) -> None:
        """② 種類数が同じなら日ごとの累積が多い方が上位。〔要項〕【確定】"""
        state = build([[PLAIN, PLAIN]], [0], [0], (2,), num_teams=2)
        for t in state.teams:
            t.brands_all = {0, 1}
            t.total_udon = 10
        state.teams[0].daily_brand_counts = [1, 1]
        state.teams[1].daily_brand_counts = [2, 2]
        self.assertEqual([t.id for t in state.ranking()], [1, 0])

    def test_ranking_uses_total_udon_third(self) -> None:
        """③ ①②が同じなら玉数が多い方が上位。〔要項〕【確定】"""
        state = build([[PLAIN, PLAIN]], [0], [0], (2,), num_teams=2)
        for t in state.teams:
            t.brands_all = {0}
            t.daily_brand_counts = [1]
        state.teams[0].total_udon = 3
        state.teams[1].total_udon = 9
        self.assertEqual([t.id for t in state.ranking()], [1, 0])

    def test_ranking_uses_response_time_last(self) -> None:
        """④ ①〜③が同じなら回答時間の累積が少ない方が上位。〔要項〕〔Q56〕【確定】

        回答時間の計測基準は【未確認】U-7 のため、値は外部から与える。
        """
        state = build([[PLAIN, PLAIN]], [0], [0], (2,), num_teams=2)
        for t in state.teams:
            t.brands_all = {0}
            t.daily_brand_counts = [1]
            t.total_udon = 5
        state.teams[0].response_time_total = 30.0
        state.teams[1].response_time_total = 10.0
        self.assertEqual([t.id for t in state.ranking()], [1, 0])


class KindsValidationTest(unittest.TestCase):
    """エージェント種別の検証。〔書式〕【確定】"""

    def test_valid_kinds(self) -> None:
        from simulator.validation import validate_kinds

        self.assertIsNone(validate_kinds([0, 1, 0], 3))

    def test_wrong_length_rejected(self) -> None:
        from simulator.validation import validate_kinds

        self.assertIsNotNone(validate_kinds([0, 1], 3))

    def test_out_of_range_value_rejected(self) -> None:
        from simulator.validation import validate_kinds

        self.assertIsNotNone(validate_kinds([0, 2, 1], 3))

    def test_missing_kinds_defaults_to_all_patrol(self) -> None:
        """種別未提出なら全エージェントが巡回車。〔書式〕〔Q53〕【確定】"""
        from simulator.terrain import AgentKind

        state = scenarios.minimal_scenario(
            cells=[[PLAIN, PLAIN]],
            spots=[],
            starts=[0, 1],
            kinds_by_team=[None],
            day_steps=(2,),
        )
        self.assertTrue(all(a.kind == AgentKind.PATROL for a in state.teams[0].agents))


if __name__ == "__main__":
    unittest.main(verbosity=2)
