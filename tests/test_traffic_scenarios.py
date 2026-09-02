"""交通量の重点検証シナリオ（ケースA〜E）。

実装指示書0828-2 第3章に対応する。各ケースについて、日ごとに
    滞在ステップ数 / 前日・前々日の値 / 交通量 / 閾値 / 道路状態
を記録し、期待値と突き合わせる。

表として確認したい場合:

    python tests/test_traffic_scenarios.py --report
"""

from __future__ import annotations

import sys
import unittest
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from simulator import engine, scenarios  # noqa: E402
from simulator.terrain import ROAD_STATUS_LABEL, RoadStatus, Terrain  # noqa: E402

PLAIN, ROAD = int(Terrain.PLAIN), int(Terrain.ROAD)

# 1行2列: セル0=道路（検証対象）, セル1=平地（退避先）
CELLS = [[ROAD, PLAIN]]
ROAD_CELL = 0
REFUGE = 1

DAY_STEPS = 6
BUSY = 3
JAMMED = 8


@dataclass
class DayRecord:
    """1日分の交通量まわりの記録。"""

    day: int
    stay_prev1: int  # その日の判定に使った「前日」の滞在ステップ数
    stay_prev2: int  # 同じく「前々日」
    volume: float  # 計算された交通量
    status: RoadStatus  # その日の道路状態
    stay_today: int  # その日に発生した滞在ステップ数（翌日以降の材料）

    def row(self) -> str:
        return (
            f"  {self.day + 1}日目 | 前日 {self.stay_prev1:>3} | 前々日 {self.stay_prev2:>3} "
            f"| 交通量 {self.volume:>5.2f} | 混雑基準 {BUSY} | 渋滞基準 {JAMMED} "
            f"| {ROAD_STATUS_LABEL[self.status]:<4} | 当日の滞在 {self.stay_today:>3}"
        )


def run_scenario(
    plans_by_day: list[dict[int, list[list[int]]]],
    *,
    kinds_by_team: list[list[int]],
    num_days: int | None = None,
    busy: int = BUSY,
    jammed: int = JAMMED,
) -> list[DayRecord]:
    """指定の行動計画で試合を進め、日ごとの交通量記録を返す。

    全エージェントはセル0（道路）から開始する。
    """
    num_days = num_days or len(plans_by_day)
    num_agents = len(kinds_by_team[0])
    state = scenarios.minimal_scenario(
        cells=CELLS,
        spots=[],
        starts=[ROAD_CELL] * num_agents,
        kinds_by_team=kinds_by_team,
        day_steps=tuple([DAY_STEPS] * num_days),
        fuel_limits=99,  # 燃料は論点から外す
        busy_threshold=busy,
        jammed_threshold=jammed,
    )
    records: list[DayRecord] = []
    while not state.finished:
        engine.begin_day(state)
        prev1 = state.traffic.stay_prev1.get(ROAD_CELL, 0)
        prev2 = state.traffic.stay_prev2.get(ROAD_CELL, 0)
        volume = engine.traffic_volume(state, ROAD_CELL)
        status = state.traffic.traffics[ROAD_CELL]

        day = state.day
        plans = plans_by_day[day] if day < len(plans_by_day) else {}
        engine.run_day_body(state, plans)

        records.append(
            DayRecord(
                day=day,
                stay_prev1=prev1,
                stay_prev2=prev2,
                volume=volume,
                status=status,
                stay_today=state.traffic.stay_prev1.get(ROAD_CELL, 0),  # 直前に shift 済み
            )
        )
    return records


# 行動計画の部品（1チーム1体を前提にした素片）
STAY_ON_ROAD = [-DAY_STEPS]  # 道路セルに1日中とどまる
LEAVE_ROAD = [2, -(DAY_STEPS - 2)]  # 平地へ退避（道路→平地は道路の状態で歩数が変わる）


def leave_road_plan(status: RoadStatus) -> list[int]:
    """その日の道路状態に応じて、退避してから残りを待機する計画を作る。"""
    cost = {RoadStatus.SMOOTH: 1, RoadStatus.CONGESTED: 2, RoadStatus.JAMMED: 4}[status]
    return [2, -(DAY_STEPS - cost)]


class TrafficScenarioTest(unittest.TestCase):
    """ケースA〜E。期待値はすべて〔要項〕の交通量の定義から手計算したもの。"""

    # ---- ケースA: 1日目だけ長時間滞在する ----

    def test_case_a_single_day_occupancy(self) -> None:
        """1日目だけ道路に居座ると、2日目の道路状態がどうなるか。

        1日目: 1体が6ステップ滞在 → 滞在6
        2日目: (前日6 + 前々日0) ÷ 1チーム = 6.0 → 混雑基準3 <= 6 < 渋滞基準8 → 混雑
        """
        records = self.case_a()
        self.assertEqual(records[0].status, RoadStatus.SMOOTH, "1日目は必ず順調")
        self.assertEqual(records[0].stay_today, 6)
        self.assertEqual(records[1].stay_prev1, 6)
        self.assertEqual(records[1].stay_prev2, 0)
        self.assertEqual(records[1].volume, 6.0)
        self.assertEqual(records[1].status, RoadStatus.CONGESTED)

    def case_a(self) -> list[DayRecord]:
        return run_scenario(
            [
                {0: [STAY_ON_ROAD]},  # 1日目: 道路に居座る
                {0: [STAY_ON_ROAD]},  # 2日目以降は結果を見るだけ
            ],
            kinds_by_team=[[0]],
        )

    # ---- ケースB: 2日目・3日目にその道路を避ける ----

    def test_case_b_avoiding_recovers(self) -> None:
        """2日目・3日目に避けると、4日目の道路状態が回復する。

        1日目: 滞在6                      → 2日目 (6+0)/1 = 6.0  → 混雑
        2日目: 混雑(2歩)で退避 → 滞在1     → 3日目 (1+6)/1 = 7.0  → 混雑
        3日目: 平地で待機      → 滞在0     → 4日目 (0+1)/1 = 1.0  → 順調（回復）
        """
        records = self.case_b()
        self.assertEqual([r.stay_today for r in records[:3]], [6, 1, 0])
        self.assertEqual(records[1].status, RoadStatus.CONGESTED)
        self.assertEqual(records[2].volume, 7.0)
        self.assertEqual(records[2].status, RoadStatus.CONGESTED)
        self.assertEqual(records[3].volume, 1.0)
        self.assertEqual(records[3].status, RoadStatus.SMOOTH, "避ければ回復する")

    def case_b(self) -> list[DayRecord]:
        return run_scenario(
            [
                {0: [STAY_ON_ROAD]},
                {0: [leave_road_plan(RoadStatus.CONGESTED)]},  # 2日目: 混雑した道路を渡って退避
                {0: [[-DAY_STEPS]]},  # 3日目: 平地で待機（道路を使わない）
                {0: [[-DAY_STEPS]]},  # 4日目: 結果を見るだけ
            ],
            kinds_by_team=[[0]],
        )

    # ---- ケースC: 2日目・3日目にも少数が利用する ----

    def test_case_c_light_continued_use(self) -> None:
        """2日目・3日目も一部のエージェントが道路に残ると、4日目も混雑が続く。

        3体編成。1日目は3体とも滞在（18）、2日目以降は1体だけ道路に残す。
        1日目: 滞在18                     → 2日目 (18+0)/1 = 18.0 → 渋滞
        2日目: 1体残り2体退避 → 滞在 6+2   → 3日目 (8+18)/1 = 26.0 → 渋滞
        3日目: 同じく        → 滞在 6+0   → 4日目 (6+8)/1  = 14.0 → 渋滞（続く）
        """
        records = self.case_c()
        self.assertEqual(records[0].stay_today, 18, "3体×6ステップ")
        self.assertEqual(records[1].status, RoadStatus.JAMMED)
        self.assertGreaterEqual(records[3].volume, JAMMED)
        self.assertEqual(records[3].status, RoadStatus.JAMMED, "少数でも使い続けると混雑が続く")

    def case_c(self) -> list[DayRecord]:
        stay = [-DAY_STEPS]
        # 2日目は渋滞(4歩)、3日目も渋滞なので退避コストは同じ
        leave_jam = leave_road_plan(RoadStatus.JAMMED)
        return run_scenario(
            [
                {0: [stay, stay, stay]},  # 1日目: 3体とも道路
                {0: [stay, leave_jam, leave_jam]},  # 2日目: 1体だけ道路に残る
                {0: [stay, [-DAY_STEPS], [-DAY_STEPS]]},  # 3日目: 同上
                {0: [stay, [-DAY_STEPS], [-DAY_STEPS]]},  # 4日目: 結果を見る
            ],
            kinds_by_team=[[0, 0, 0]],
        )

    # ---- ケースD: 複数チームが同じ道路を利用する ----

    def test_case_d_divisor_is_team_count(self) -> None:
        """交通量は全チーム分を合算し、チーム数で割る。〔要項〕【確定】

        1チーム1体が6ステップ滞在   → 合計 6、÷1 = 6.0
        2チーム各1体が6ステップ滞在 → 合計12、÷2 = 6.0（同じ交通量になる）
        4チーム各1体が6ステップ滞在 → 合計24、÷4 = 6.0（同じ）
        """
        volumes = {}
        stays = {}
        for n_teams in (1, 2, 4):
            records = self.case_d(n_teams)
            stays[n_teams] = records[0].stay_today
            volumes[n_teams] = records[1].volume
        self.assertEqual(stays, {1: 6, 2: 12, 4: 24}, "滞在数は全チーム合算で積み上がる")
        self.assertEqual(
            volumes, {1: 6.0, 2: 6.0, 4: 6.0}, "チーム数で割るので交通量は等しくなる"
        )

    def case_d(self, num_teams: int) -> list[DayRecord]:
        return run_scenario(
            [
                {t: [STAY_ON_ROAD] for t in range(num_teams)},
                {t: [STAY_ON_ROAD] for t in range(num_teams)},
            ],
            kinds_by_team=[[0] for _ in range(num_teams)],
        )

    def test_case_d_more_teams_needs_more_total_stay(self) -> None:
        """同じ道路状態にするには、チーム数が増えるほど総滞在が必要になる。

        2チームのうち1チームだけが滞在した場合、合計6 ÷ 2 = 3.0 で
        混雑基準3 ちょうど → 混雑（1チームだけなら 6.0 で同じ混雑だが、
        総滞在が同じでもチーム数が違えば交通量が変わることを示す）。
        """
        records = run_scenario(
            [
                {0: [STAY_ON_ROAD], 1: [leave_road_plan(RoadStatus.SMOOTH)]},
                {0: [STAY_ON_ROAD], 1: [[-DAY_STEPS]]},
            ],
            kinds_by_team=[[0], [0]],
        )
        # チーム0が6、チーム1は順調な道路(1歩)で抜けるので滞在0 → 合計6
        self.assertEqual(records[0].stay_today, 6)
        self.assertEqual(records[1].volume, 3.0, "6 ÷ 2チーム = 3.0")
        self.assertEqual(records[1].status, RoadStatus.CONGESTED, "混雑基準3ちょうどで混雑")

    # ---- ケースE: 補給車も交通量に含まれるか ----

    def test_case_e_supply_vehicle_counts_in_traffic(self) -> None:
        """補給車も滞在ステップ数のカウント対象に含まれる。〔補足〕滞在数【確定】

        巡回車2体 と 巡回車1体＋補給車1体 で、同じ行動なら滞在数は同じになる。
        """
        both_patrol = run_scenario(
            [{0: [STAY_ON_ROAD, STAY_ON_ROAD]}, {0: [STAY_ON_ROAD, STAY_ON_ROAD]}],
            kinds_by_team=[[0, 0]],
        )
        with_supply = run_scenario(
            [{0: [STAY_ON_ROAD, STAY_ON_ROAD]}, {0: [STAY_ON_ROAD, STAY_ON_ROAD]}],
            kinds_by_team=[[0, 1]],  # 2体目を補給車に
        )
        self.assertEqual(both_patrol[0].stay_today, 12, "巡回車2体 × 6ステップ")
        self.assertEqual(
            with_supply[0].stay_today, 12, "補給車も同じようにカウントされる"
        )
        self.assertEqual(both_patrol[1].volume, with_supply[1].volume)
        self.assertEqual(both_patrol[1].status, with_supply[1].status)

    def test_case_e_supply_only_team_still_generates_traffic(self) -> None:
        """全員が補給車のチームでも交通量は発生する。"""
        records = run_scenario(
            [{0: [STAY_ON_ROAD, STAY_ON_ROAD]}, {0: [STAY_ON_ROAD, STAY_ON_ROAD]}],
            kinds_by_team=[[1, 1]],
        )
        self.assertEqual(records[0].stay_today, 12)
        self.assertEqual(records[1].volume, 12.0)
        self.assertEqual(records[1].status, RoadStatus.JAMMED)


class TrafficShiftAuditTest(unittest.TestCase):
    """交通量の2日分シフトが、常に「前日」「前々日」を正しく指していること。

    実装指示書0828-2 第8章の自己監査項目。
    """

    def test_shift_always_points_to_previous_two_days(self) -> None:
        """複数日にわたって、参照する前日・前々日が実際の履歴と一致すること。"""
        num_days = 6
        state = scenarios.minimal_scenario(
            cells=CELLS,
            spots=[],
            starts=[ROAD_CELL],
            kinds_by_team=[[0]],
            day_steps=tuple([4] * num_days),
            fuel_limits=99,
            busy_threshold=99,  # 状態は順調に固定し、滞在数の追跡だけを見る
            jammed_threshold=100,
        )
        history: list[int] = []  # history[d] = d日目に発生した滞在数
        while not state.finished:
            day = state.day
            engine.begin_day(state)
            prev1 = state.traffic.stay_prev1.get(ROAD_CELL, 0)
            prev2 = state.traffic.stay_prev2.get(ROAD_CELL, 0)

            self.assertEqual(prev1, history[day - 1] if day >= 1 else 0,
                             f"{day}日目が参照した「前日」が履歴と一致しない")
            self.assertEqual(prev2, history[day - 2] if day >= 2 else 0,
                             f"{day}日目が参照した「前々日」が履歴と一致しない")

            # 日ごとに滞在数を変える（道路に留まる / 平地へ出て戻る）
            plan = [[-4]] if day % 2 == 0 else [[2, 5, -1]]
            engine.run_day_body(state, {0: plan})
            history.append(state.traffic.stay_prev1.get(ROAD_CELL, 0))

        self.assertEqual(len(history), num_days)

    def test_first_two_days_have_no_history(self) -> None:
        """1日目は前日も前々日も無し、2日目は前日のみ。〔要項〕【確定】"""
        records = run_scenario(
            [{0: [STAY_ON_ROAD]}, {0: [STAY_ON_ROAD]}, {0: [STAY_ON_ROAD]}],
            kinds_by_team=[[0]],
        )
        self.assertEqual((records[0].stay_prev1, records[0].stay_prev2), (0, 0))
        self.assertEqual(records[1].stay_prev2, 0, "2日目に前々日は存在しない")
        self.assertGreater(records[2].stay_prev2, 0, "3日目以降は前々日を参照する")


# ---------------------------------------------------------------------------
# 表形式のレポート出力
# ---------------------------------------------------------------------------


def print_report() -> None:
    t = TrafficScenarioTest()
    print("=" * 100)
    print("交通量の重点検証（実装指示書0828-2 第3章 ケースA〜E）")
    print(f"マップ: [0]道路 --- [1]平地 / 1日{DAY_STEPS}ステップ / "
          f"混雑基準={BUSY} 渋滞基準={JAMMED}")
    print("=" * 100)

    cases = [
        ("ケースA: 1日目だけ道路に長時間滞在（1体）", t.case_a()),
        ("ケースB: 2・3日目はその道路を避ける（1体）", t.case_b()),
        ("ケースC: 2・3日目も1体だけ道路を使い続ける（3体）", t.case_c()),
        ("ケースD-1: 1チームが滞在", t.case_d(1)),
        ("ケースD-2: 2チームが滞在（合計は倍・交通量は同じ）", t.case_d(2)),
        ("ケースD-4: 4チームが滞在（合計は4倍・交通量は同じ）", t.case_d(4)),
    ]
    for title, records in cases:
        print(f"\n{title}")
        for r in records:
            print(r.row())

    print("\nケースE: 補給車が交通量に含まれるか")
    for label, kinds in [("巡回車2体", [0, 0]), ("巡回車1体+補給車1体", [0, 1]), ("補給車2体", [1, 1])]:
        recs = run_scenario(
            [{0: [STAY_ON_ROAD, STAY_ON_ROAD]}, {0: [STAY_ON_ROAD, STAY_ON_ROAD]}],
            kinds_by_team=[kinds],
        )
        print(f"  {label:<22} 1日目の滞在={recs[0].stay_today:>3}  "
              f"2日目の交通量={recs[1].volume:>5.2f}  "
              f"2日目の道路状態={ROAD_STATUS_LABEL[recs[1].status]}")


if __name__ == "__main__":
    if "--report" in sys.argv:
        print_report()
    else:
        unittest.main(verbosity=2)
