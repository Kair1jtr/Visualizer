"""未確定仕様（U-1 / U-3 / U-4 / U-5 / U-8 / U-9）の既定値と切り替えの確認。

実装指示書0828-2 第7章に対応する。各項目について

    1. 現在どの解釈を既定にしているか
    2. どこを変えれば切り替えられるか
    3. 切り替えると結果が変わりうるか

を実行で確かめる。**どの解釈が正しいかを決めることは目的ではない。**
公式に確定していない事項を「確定した」と扱わないための回帰テストである。

一覧として確認したい場合:

    python tests/test_uncertain_policies.py --report
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from simulator import engine, scenarios  # noqa: E402
from simulator.grid import build_grid  # noqa: E402
from simulator.policies import (  # noqa: E402
    DEFAULT_POLICIES,
    AgentOrder,
    CellIndexing,
    FuelTiming,
    Policies,
    RowOffset,
    SecondDayDivisor,
    TrafficDivision,
)
from simulator.state import SpotDef  # noqa: E402
from simulator.terrain import AgentKind, RoadStatus, Terrain  # noqa: E402

PLAIN, ROAD = int(Terrain.PLAIN), int(Terrain.ROAD)


# ---------------------------------------------------------------------------
# U-1: セル番号の割り当て規則
# ---------------------------------------------------------------------------


class U1CellIndexingTest(unittest.TestCase):
    """U-1【未確認】セル番号の割り当て規則。

    〔要項〕は「図1のように」と図に委ねており、本文に規則の記述がない。
    行優先を採るのは〔設定〕（簡易サーバー付属の補助資料）のみ。
    **公式に確定した仕様として扱ってはならない。**
    """

    def test_default_is_row_major(self) -> None:
        """既定は行優先 `pos = y*width + x`〔設定〕【補助資料】。"""
        self.assertIs(DEFAULT_POLICIES.cell_indexing, CellIndexing.ROW_MAJOR)

    def test_row_major_index_formula(self) -> None:
        """既定では 3行×4列 の (行1, 列2) が pos = 1*4+2 = 6 になる。"""
        grid = build_grid(3, 4, [[PLAIN] * 4 for _ in range(3)], DEFAULT_POLICIES)
        self.assertEqual(grid.to_cell(1, 2), 1 * 4 + 2)
        self.assertEqual(grid.to_rc(6), (1, 2))

    def test_column_major_can_be_selected_and_changes_numbering(self) -> None:
        """policies.cell_indexing を切り替えると番号付けが実際に変わる。"""
        policies = Policies(cell_indexing=CellIndexing.COLUMN_MAJOR)
        grid = build_grid(3, 4, [[PLAIN] * 4 for _ in range(3)], policies)
        self.assertEqual(grid.to_cell(1, 2), 2 * 3 + 1)
        self.assertNotEqual(grid.to_cell(1, 2), 6, "既定と同じでは切り替えの意味がない")
        self.assertEqual(grid.to_rc(7), (1, 2))

    def test_row_offset_is_even_right_and_is_confirmed(self) -> None:
        """六角オフセットは〔Q1〕で偶数行が右にずれると**確定**している。

        U-1（番号付け）とは別の軸であり、こちらは未確認ではない。
        """
        self.assertIs(DEFAULT_POLICIES.row_offset, RowOffset.EVEN_RIGHT)


# ---------------------------------------------------------------------------
# U-3 / U-4: 交通量の除算と2日目の除数
# ---------------------------------------------------------------------------


def _traffic_state(*, num_teams: int, stay_prev1: int, stay_prev2: int, day: int,
                   policies: Policies = DEFAULT_POLICIES, busy: int = 3, jammed: int = 8):
    """道路1本だけの盤面に、任意の滞在履歴を直接書き込んだ状態を作る。"""
    state = scenarios.minimal_scenario(
        cells=[[ROAD, PLAIN]],
        spots=[SpotDef(pos=1, brand=0, stocks=1)],
        starts=[1],
        kinds_by_team=[[0] for _ in range(num_teams)],
        day_steps=(6, 6, 6, 6),
        fuel_limits=99,
        busy_threshold=busy,
        jammed_threshold=jammed,
        policies=policies,
    )
    state.day = day
    state.traffic.stay_prev1 = {0: stay_prev1}
    state.traffic.stay_prev2 = {0: stay_prev2}
    return state


class U3TrafficDivisionTest(unittest.TestCase):
    """U-3【未確認】交通量の「チーム数で割った値」が整数除算か実数か。"""

    def test_default_is_exact(self) -> None:
        self.assertIs(DEFAULT_POLICIES.traffic_division, TrafficDivision.EXACT)

    def test_exact_and_floor_are_equivalent_for_integer_thresholds(self) -> None:
        """閾値が正の整数〔Q30〕【確定】である限り EXACT と FLOOR は完全に一致する。

        整数 t について `floor(x) >= t ⟺ x >= t` であるため。
        したがって U-3 が結果を変えうるのは CEIL / ROUND_HALF_UP のみ。
        """
        for teams in (1, 2, 3, 4):
            for stay in range(0, 40):
                a = _traffic_state(num_teams=teams, stay_prev1=stay, stay_prev2=0, day=2,
                                   policies=Policies(traffic_division=TrafficDivision.EXACT))
                b = _traffic_state(num_teams=teams, stay_prev1=stay, stay_prev2=0, day=2,
                                   policies=Policies(traffic_division=TrafficDivision.FLOOR))
                self.assertEqual(
                    engine.compute_road_status(a)[0],
                    engine.compute_road_status(b)[0],
                    f"teams={teams} stay={stay} で EXACT と FLOOR がずれた",
                )

    def test_ceil_can_change_the_result(self) -> None:
        """CEIL を選ぶと境界で判定が変わりうる（＝U-3 は結果に影響する）。"""
        # 3チーム・滞在合計7 → 7/3 = 2.33。EXACT では混雑基準3に届かないが CEIL では 3。
        exact = _traffic_state(num_teams=3, stay_prev1=7, stay_prev2=0, day=2,
                               policies=Policies(traffic_division=TrafficDivision.EXACT))
        ceil = _traffic_state(num_teams=3, stay_prev1=7, stay_prev2=0, day=2,
                              policies=Policies(traffic_division=TrafficDivision.CEIL))
        self.assertEqual(engine.compute_road_status(exact)[0], RoadStatus.SMOOTH)
        self.assertEqual(engine.compute_road_status(ceil)[0], RoadStatus.CONGESTED)


class U4SecondDayDivisorTest(unittest.TestCase):
    """U-4【未確認】2日目の交通量で除数を調整するかどうか。"""

    def test_default_is_teams(self) -> None:
        """既定は常にチーム数〔要項〕の定義式をそのまま適用する。"""
        self.assertIs(DEFAULT_POLICIES.second_day_divisor, SecondDayDivisor.TEAMS)

    def test_divisor_is_team_count_on_every_day_by_default(self) -> None:
        for day in (1, 2, 3):
            state = _traffic_state(num_teams=2, stay_prev1=6, stay_prev2=6, day=day)
            self.assertEqual(engine.traffic_divisor(state), 2, f"{day + 1}日目の除数")

    def test_alternative_only_differs_on_day3_onward(self) -> None:
        """対抗仮説（チーム数×参照日数）では3日目以降だけ除数が倍になる。

        2日目は参照日数が1なので、どちらの仮説でも除数は同じ。
        つまり U-4 が結果を変えるのは **3日目以降** である。
        """
        alt = Policies(second_day_divisor=SecondDayDivisor.TEAMS_TIMES_DAYS)
        day2 = _traffic_state(num_teams=2, stay_prev1=6, stay_prev2=0, day=1, policies=alt)
        self.assertEqual(engine.traffic_divisor(day2), 2, "2日目は差が出ない")
        day3 = _traffic_state(num_teams=2, stay_prev1=6, stay_prev2=6, day=2, policies=alt)
        self.assertEqual(engine.traffic_divisor(day3), 4, "3日目以降で差が出る")

    def test_alternative_can_change_the_road_status(self) -> None:
        """除数の仮説を変えると道路状態が実際に変わりうる。"""
        base = dict(num_teams=2, stay_prev1=8, stay_prev2=8, day=2)  # 合計16
        default = _traffic_state(**base)  # 16/2 = 8.0 → 渋滞
        alt = _traffic_state(**base, policies=Policies(
            second_day_divisor=SecondDayDivisor.TEAMS_TIMES_DAYS))  # 16/4 = 4.0 → 混雑
        self.assertEqual(engine.compute_road_status(default)[0], RoadStatus.JAMMED)
        self.assertEqual(engine.compute_road_status(alt)[0], RoadStatus.CONGESTED)


# ---------------------------------------------------------------------------
# U-5: 反映フェーズのエージェント処理順序
# ---------------------------------------------------------------------------


class U5AgentOrderTest(unittest.TestCase):
    """U-5【未確認】反映フェーズでエージェントを処理する順序の規則。

    〔Q22〕は順序の存在に言及するだけで、規則そのものは明記されていない。
    〔Q26〕がうどん獲得の競合を「リスト内の順番が若い方が先」と定めている【確定】
    ので、既定をそれに合わせている。
    """

    def _competition_state(self, policies: Policies = DEFAULT_POLICIES):
        """在庫1のスポットに2体が同時到着する盤面。獲得できるのは片方だけ。〔Q26〕"""
        return scenarios.minimal_scenario(
            cells=[[PLAIN, PLAIN, PLAIN]],
            spots=[SpotDef(pos=1, brand=0, stocks=1)],
            starts=[0, 2],
            kinds_by_team=[[0, 0]],
            day_steps=(4,),
            fuel_limits=99,
            policies=policies,
        )

    def test_default_is_agent_id_order(self) -> None:
        self.assertIs(DEFAULT_POLICIES.agent_order, AgentOrder.AGENT_ID)

    def test_ordered_agents_follows_the_policy(self) -> None:
        asc = engine.ordered_agents(self._competition_state())
        self.assertEqual([a.agent_id for _t, a in asc], [0, 1])
        desc = engine.ordered_agents(
            self._competition_state(Policies(agent_order=AgentOrder.REVERSED_ID)))
        self.assertEqual([a.agent_id for _t, a in desc], [1, 0])

    def test_stock_contention_follows_q26_not_the_policy(self) -> None:
        """在庫の奪い合いは U-5 ではなく〔Q26〕【確定】のID順で決まる。

        獲得（反映フェーズ3）だけは `ordered_agents` を使わず ID 昇順を直に使う。
        〔Q26〕が「リスト内の順番が若いエージェントが先」と**確定**させているため、
        未確認事項の切り替えに巻き込んではならない。
        """
        for order in (AgentOrder.AGENT_ID, AgentOrder.REVERSED_ID):
            with self.subTest(order=order.value):
                state = self._competition_state(Policies(agent_order=order))
                engine.begin_day(state)
                # 0番は右へ1歩、1番は左へ1歩。平地は2ステップなので同時到着する。
                rejections = engine.run_day_body(state, {0: [[2, -2], [5, -2]]})
                self.assertIsNone(rejections.get(0))
                team = state.teams[0]
                self.assertEqual(team.total_udon, 1, "在庫1なので獲得は1つだけ")
                self.assertEqual(team.agents[0].acquired_spots_today, {1},
                                 "順序ポリシーに関わらず 0 番が獲得する〔Q26〕")
                self.assertEqual(team.agents[1].acquired_spots_today, set())

    def test_order_has_no_observable_effect(self) -> None:
        """U-5 を切り替えても試合結果は一切変わらない。

        `ordered_agents` を使うのは反映フェーズ1（燃料消費）・2（移動）・5（交通量）
        だけで、いずれもエージェント間で独立した処理か総和である。順序に依存する
        唯一の処理（獲得競合）は〔Q26〕で確定しているため、
        **U-5 が未確認のままでも戦略研究の結論は変わらない。**
        """
        finals = []
        for order in (AgentOrder.AGENT_ID, AgentOrder.REVERSED_ID):
            state = scenarios.minimal_scenario(
                cells=[[PLAIN, ROAD, PLAIN], [PLAIN, PLAIN, PLAIN]],
                spots=[SpotDef(pos=2, brand=0, stocks=3), SpotDef(pos=5, brand=1, stocks=1)],
                starts=[0, 3, 4],
                kinds_by_team=[[0, 0, 1], [0, 0, 1]],
                day_steps=(8, 8, 8, 8),
                fuel_limits=20,
                busy_threshold=3,
                jammed_threshold=8,
                policies=Policies(agent_order=order),
            )
            plan = [[2, 2, -4], [1, -6], [-8]]
            while not state.finished:
                engine.begin_day(state)
                engine.run_day_body(state, {t.team_id: [list(p) for p in plan]
                                            for t in state.teams})
            finals.append([
                (t.team_id, t.total_udon, t.brand_count, t.daily_brand_cumulative,
                 tuple((a.pos, a.fuel) for a in t.agents))
                for t in state.teams
            ])
        self.assertEqual(finals[0], finals[1], "処理順序で試合結果が変わってはならない")

    def test_order_does_not_affect_refueling(self) -> None:
        """補給の判定は全移動を反映し終えた後に行うため、順序に依存しない。〔Q22〕"""
        results = []
        for order in (AgentOrder.AGENT_ID, AgentOrder.REVERSED_ID):
            state = scenarios.minimal_scenario(
                cells=[[PLAIN, PLAIN, PLAIN]],
                spots=[SpotDef(pos=0, brand=0, stocks=1)],
                starts=[0, 2],
                kinds_by_team=[[0, 1]],  # 0番=巡回車, 1番=補給車
                day_steps=(4,),
                fuel_limits=10,
                policies=Policies(agent_order=order),
            )
            state.teams[0].agents[0].fuel = 4
            engine.begin_day(state)
            # 巡回車は右へ、補給車は左へ。セル1で合流する。
            rejections = engine.run_day_body(state, {0: [[2, -2], [5, -2]]})
            self.assertIsNone(rejections.get(0))
            results.append(state.teams[0].agents[0].fuel)
        self.assertEqual(results[0], results[1], "処理順序で補給結果が変わってはならない")


# ---------------------------------------------------------------------------
# U-8: 補給車の fuel 値の意味
# ---------------------------------------------------------------------------


class U8SupplyFuelTest(unittest.TestCase):
    """U-8【未確認】補給車の `fuel` 値の意味。

    〔書式〕の例では補給車にも `fuel` の値が入っているが、補給車には
    燃料上限が設定されていない〔要項〕。現実装は **補給車の fuel を一切参照しない**
    （消費も補給もしない）ことで、この未確認事項を挙動から切り離している。
    """

    def _state(self, supply_fuel: int):
        state = scenarios.minimal_scenario(
            cells=[[PLAIN, PLAIN, PLAIN, PLAIN]],
            spots=[SpotDef(pos=3, brand=0, stocks=1)],
            starts=[0],
            kinds_by_team=[[1]],  # 補給車1体のみ
            day_steps=(8,),
            fuel_limits=10,
        )
        state.teams[0].agents[0].fuel = supply_fuel
        return state

    def test_supply_vehicle_is_not_a_patrol(self) -> None:
        state = self._state(0)
        self.assertIs(state.teams[0].agents[0].kind, AgentKind.SUPPLY)
        self.assertFalse(state.teams[0].agents[0].is_patrol)

    def test_supply_vehicle_moves_regardless_of_its_fuel_value(self) -> None:
        """fuel が 0 でも 20 でも、補給車の移動結果は同じ（＝値に意味が無い）。"""
        positions = []
        for fuel in (0, 20):
            state = self._state(fuel)
            engine.begin_day(state)
            engine.run_day_body(state, {0: [[2, 2, 2, -2]]})
            positions.append(state.teams[0].agents[0].pos)
            self.assertEqual(state.teams[0].agents[0].fuel, fuel, "補給車の燃料は減らない")
        self.assertEqual(positions[0], positions[1])
        self.assertEqual(positions[0], 3, "燃料に関わらず目的地に着く")

    def test_zero_fuel_supply_vehicle_is_a_valid_plan(self) -> None:
        """燃料0の補給車に移動を命じても不正回答にはならない〔要項〕燃料上限なし。"""
        state = self._state(0)
        engine.begin_day(state)
        rejections = engine.run_day_body(state, {0: [[2, 2, 2, -2]]})
        self.assertIsNone(rejections.get(0))


# ---------------------------------------------------------------------------
# U-9: 全チームのエージェント数が同一か
# ---------------------------------------------------------------------------


class U9TeamAgentCountTest(unittest.TestCase):
    """U-9【未確認】全チームのエージェント数が必ず同一かどうか。

    公式資料に「全チーム同数」と明記した箇所は無い。しかし
    〔Q38〕「エージェントの初期位置は全チーム共通」【確定】から、
    現実装は初期位置リスト `agent_starts` を全チームで共有しており、
    結果として **エージェント数も全チーム同数になる**。
    U-9 が「同数でない場合がある」と確定した場合は、`create_game` の
    `agent_starts` をチームごとの指定に変える必要がある（1か所）。

    一方、**種別（巡回車／補給車）の内訳はチームごとに自由**であり、
    こちらは既に対応している〔書式〕種別提出【確定】。
    """

    def _state(self, kinds_by_team: list[list[int]], starts: list[int] | None = None):
        num = len(kinds_by_team[0])
        return scenarios.minimal_scenario(
            cells=[[PLAIN, ROAD, PLAIN], [PLAIN, PLAIN, PLAIN]],
            spots=[SpotDef(pos=2, brand=0, stocks=99)],
            starts=starts if starts is not None else [0, 3, 4, 5][:num],
            kinds_by_team=kinds_by_team,
            day_steps=(6, 6, 6),
            fuel_limits=99,
            busy_threshold=3,
            jammed_threshold=8,
        )

    def test_agent_counts_are_equal_across_teams(self) -> None:
        """現実装では全チームのエージェント数が一致する（〔Q38〕の共通初期位置に由来）。"""
        state = self._state([[0, 0, 1], [0, 1, 1], [0, 0, 0]])
        self.assertEqual([len(t.agents) for t in state.teams], [3, 3, 3])

    def test_mismatched_agent_count_is_rejected_loudly(self) -> None:
        """チーム間で数が食い違う入力は黙って通さず、必ず例外にする。"""
        with self.assertRaises(ValueError) as cm:
            self._state([[0, 0, 0], [0, 1]])
        self.assertIn("エージェント数", str(cm.exception))

    def test_kind_composition_may_differ_per_team(self) -> None:
        """種別の内訳はチームごとに違ってよい。〔書式〕種別提出【確定】"""
        state = self._state([[0, 0, 1], [0, 1, 1], [0, 0, 0]])
        kinds = [[int(a.kind) for a in t.agents] for t in state.teams]
        self.assertEqual(kinds, [[0, 0, 1], [0, 1, 1], [0, 0, 0]])

    def test_all_teams_start_at_the_same_cells(self) -> None:
        """初期位置は全チーム共通。〔Q38〕【確定】"""
        state = self._state([[0, 0, 1], [0, 1, 1], [0, 0, 0]])
        positions = [[a.pos for a in t.agents] for t in state.teams]
        self.assertEqual(positions[0], positions[1])
        self.assertEqual(positions[0], positions[2])

    def test_different_kind_compositions_run_to_completion(self) -> None:
        state = self._state([[0, 0, 1], [0, 1, 1], [0, 0, 0]])
        while not state.finished:
            engine.begin_day(state)
            rejections = engine.run_day_body(state, engine.all_wait_plans(state))
            self.assertEqual({k: v for k, v in rejections.items() if v}, {})
        self.assertTrue(state.finished)

    def test_traffic_divisor_is_team_count_not_agent_count(self) -> None:
        """交通量の除数はチーム数であり、エージェント数ではない。〔要項〕【確定】"""
        state = self._state([[0, 0, 1], [0, 1, 1], [0, 0, 0]])
        self.assertEqual(engine.traffic_divisor(state), 3, "チーム数=3（エージェント数9ではない）")

    def test_plan_length_is_checked_per_team(self) -> None:
        """行動計画の要素数の検証は、そのチーム自身のエージェント数に対して行われる。"""
        state = self._state([[0, 0, 1], [0, 1, 1]])
        engine.begin_day(state)
        plans = engine.all_wait_plans(state)
        plans[1] = [[-6], [-6]]  # 3体いるのに2体分しか無い
        rejections = engine.run_day_body(state, plans)
        self.assertIsNone(rejections.get(0))
        self.assertIsNotNone(rejections.get(1))


# ---------------------------------------------------------------------------
# 未確定事項が1か所に集約されていることの確認
# ---------------------------------------------------------------------------


class PolicyIsolationTest(unittest.TestCase):
    """実装指示書 第7章: 未確定の判断を他の場所で再実装していないこと。"""

    def test_all_policies_are_listed_in_describe(self) -> None:
        text = "\n".join(DEFAULT_POLICIES.describe())
        for key in ("U-1", "U-3", "U-4", "U-5", "U-6"):
            self.assertIn(key, text, f"{key} が describe() に出てこない")

    def test_fuel_timing_default_is_the_documented_finding(self) -> None:
        """U-6 は〔補足〕の遷移表から ON_ARRIVAL と判別済み（docs/実装ノート.md）。"""
        self.assertIs(DEFAULT_POLICIES.fuel_timing, FuelTiming.ON_ARRIVAL)

    def test_policies_are_immutable(self) -> None:
        """試合中に未確定仕様の解釈が変わらないこと。"""
        with self.assertRaises(Exception):
            DEFAULT_POLICIES.traffic_division = TrafficDivision.CEIL  # type: ignore[misc]


ROWS = [
    ("U-1", "セル番号の割り当て", "row_major（pos = y*width + x）",
     "〔設定〕のみ【補助資料】", "policies.cell_indexing",
     "盤面全体。誤ると全移動処理が破綻する"),
    ("U-3", "交通量の除算", "exact（実数のまま比較）",
     "〔要項〕に明記なし【未確認】", "policies.traffic_division",
     "整数閾値では floor と同値。ceil / 四捨五入を採る場合のみ境界で変わる"),
    ("U-4", "2日目の除数", "teams（常にチーム数）",
     "〔要項〕に明記なし【未確認】", "policies.second_day_divisor",
     "対抗仮説との差は3日目以降のみ。道路状態が変わりうる"),
    ("U-5", "反映の処理順序", "agent_id（ID昇順）",
     "〔Q26〕の獲得競合に整合【推定】", "policies.agent_order",
     "**観測可能な差は無い**。順序が効く唯一の処理（獲得競合）は〔Q26〕で確定済み"),
    ("U-6", "燃料消費タイミング", "on_arrival（移動完了時）",
     "〔補足〕遷移表から判別【確定扱い】", "policies.fuel_timing",
     "巡回車の燃料推移と回答の有効性"),
    ("U-8", "補給車の fuel の意味", "参照しない（消費も補給もしない）",
     "〔書式〕の例に値はあるが上限なし【未確認】", "（切り替え不要）",
     "挙動に影響しない。値は入力のまま保持する"),
    ("U-9", "チーム間のエージェント数", "全チーム同数（初期位置を共有するため）",
     "〔Q38〕共通初期位置【確定】から従属。明記自体は無い【未確認】",
     "create_game の agent_starts をチーム別にする",
     "種別の内訳は既にチームごとに自由。数が異なる場合のみ変更が必要"),
]


def print_report() -> None:
    print("=" * 118)
    print("未確定仕様の現状（実装指示書0828-2 第7章）")
    print("=" * 118)
    for tag, name, default, basis, where, effect in ROWS:
        print(f"\n[{tag}] {name}")
        print(f"    既定値      : {default}")
        print(f"    根拠        : {basis}")
        print(f"    変更箇所    : {where}")
        print(f"    変わりうる点: {effect}")
    print("\n" + "-" * 118)
    print("採用中の設定（Policies.describe）:")
    for line in DEFAULT_POLICIES.describe():
        print("    " + line)
    print("\n  ※ U-1 は補助資料のみを根拠とする暫定採用であり、"
          "公式に確定した仕様として扱ってはならない。")


if __name__ == "__main__":
    if "--report" in sys.argv:
        print_report()
    else:
        unittest.main(verbosity=2)
