"""同一初期状態から3つの行動パターンを比較する検証シナリオ。

実装指示書0828-2 第4章に対応する。

    A. 渋滞道路へ進む   … 道路を通る最短ルートで毎日スポットを往復する
    B. 渋滞道路を避ける … 平地だけの迂回ルートで毎日スポットを往復する
    C. その場で待機     … 一切動かない

同一条件で 位置 / 燃料 / うどん獲得数 / 獲得系列数 / 道路状態 / 交通量 / 最終得点
を並べて比較できることを確認する。**どの戦略が強いかは判定しない。**

表として確認したい場合:

    python tests/test_strategy_comparison.py --report
"""

from __future__ import annotations

import sys
import unittest
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from simulator import compare, engine, scenarios  # noqa: E402
from simulator.state import SpotDef  # noqa: E402
from simulator.terrain import ROAD_STATUS_LABEL, RoadStatus, Terrain  # noqa: E402

PLAIN, ROAD = int(Terrain.PLAIN), int(Terrain.ROAD)

# 2行3列（偶数行が右にずれる even-r）
#
#     [0]平地(出発) --- [1]道路 --- [2]平地◎スポット
#        \             /    \      /
#         [3]平地 --- [4]平地 --- [5]平地
#
# 出発(セル0)からスポット(セル2)へは2通りある:
#   直行 : 0 →右→ 1 →右→ 2        （セル1が道路。渋滞すると遅くなる）
#   迂回 : 0 →右下→ 4 →右→ 5 →右上→ 2  （すべて平地。常に一定）
CELLS = [[PLAIN, ROAD, PLAIN], [PLAIN, PLAIN, PLAIN]]
ROAD_CELL = 1
SPOT_CELL = 2
START = 0

# 往復の方向列（行きと帰り）
DIRECT_ROUND_TRIP = [2, 2, 5, 5]  # 0→1→2→1→0
DETOUR_ROUND_TRIP = [3, 2, 1, 4, 5, 0]  # 0→4→5→2→5→4→0

DAY_STEPS = 12  # 迂回（平地6回×2歩）と、渋滞時の直行（2+4+2+4）がどちらもちょうど収まる
NUM_DAYS = 4
NUM_AGENTS = 3
BUSY, JAMMED = 3, 8
FUEL_LIMIT = 99  # 燃料は論点から外す


@dataclass
class DayResult:
    day: int
    road_status: RoadStatus
    volume: float
    stay_on_road: int
    positions: list[int]
    fuels: list[int]
    total_udon: int
    brand_count: int
    daily_cumulative: int
    rejected: str | None = None


@dataclass
class PatternResult:
    label: str
    days: list[DayResult] = field(default_factory=list)

    @property
    def final(self) -> DayResult:
        return self.days[-1]


def make_state():
    """3パターンで共通の初期状態。"""
    return scenarios.minimal_scenario(
        cells=CELLS,
        spots=[SpotDef(pos=SPOT_CELL, brand=0, stocks=99)],
        starts=[START] * NUM_AGENTS,
        kinds_by_team=[[0] * NUM_AGENTS],
        day_steps=tuple([DAY_STEPS] * NUM_DAYS),
        fuel_limits=FUEL_LIMIT,
        busy_threshold=BUSY,
        jammed_threshold=JAMMED,
    )


def round_trip_strategy(directions: list[int]) -> compare.Strategy:
    """その日の道路状態でコストを数えつつ、指定の往復ルートを進む。"""

    def strategy(state, team_id):
        team = next(t for t in state.teams if t.id == team_id)
        return [compare.build_plan(state, a, prefix=list(directions)) for a in team.agents]

    return strategy


PATTERNS: dict[str, compare.Strategy] = {
    "A. 道路を通る（直行）": round_trip_strategy(DIRECT_ROUND_TRIP),
    "B. 道路を避ける（迂回）": round_trip_strategy(DETOUR_ROUND_TRIP),
    "C. その場で待機": compare.always_wait(),
}


def run_pattern(label: str, strategy: compare.Strategy) -> PatternResult:
    state = make_state()
    result = PatternResult(label=label)
    while not state.finished:
        engine.begin_day(state)
        status = state.traffic.traffics[ROAD_CELL]
        volume = engine.traffic_volume(state, ROAD_CELL)
        day = state.day
        plans = {0: strategy(state, 0)}
        rejections = engine.run_day_body(state, plans)
        team = state.teams[0]
        result.days.append(
            DayResult(
                day=day,
                road_status=status,
                volume=volume,
                stay_on_road=state.traffic.stay_prev1.get(ROAD_CELL, 0),
                positions=[a.pos for a in team.agents],
                fuels=[a.fuel for a in team.agents],
                total_udon=team.total_udon,
                brand_count=team.brand_count,
                daily_cumulative=team.daily_brand_cumulative,
                rejected=str(rejections[0]) if rejections.get(0) else None,
            )
        )
    return result


def run_all() -> dict[str, PatternResult]:
    return {label: run_pattern(label, s) for label, s in PATTERNS.items()}


class StrategyComparisonTest(unittest.TestCase):
    """3パターンが同一条件で完走し、結果を比較できること。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.results = run_all()

    def test_all_patterns_complete_without_rejection(self) -> None:
        """3パターンとも全日程をリジェクトなしで完走すること。"""
        for label, result in self.results.items():
            with self.subTest(pattern=label):
                self.assertEqual(len(result.days), NUM_DAYS)
                for d in result.days:
                    self.assertIsNone(d.rejected, f"{label} の{d.day + 1}日目がリジェクト: {d.rejected}")

    def test_same_initial_state_for_all_patterns(self) -> None:
        """比較の前提として、3パターンの初期条件が同一であること。"""
        for result in self.results.values():
            first = result.days[0]
            self.assertEqual(first.road_status, RoadStatus.SMOOTH, "1日目は必ず順調")
            self.assertEqual(first.volume, 0.0)

    def test_direct_route_congests_the_road(self) -> None:
        """道路を通り続けると、その道路の交通量が積み上がり状態が悪化する。"""
        direct = self.results["A. 道路を通る（直行）"]
        volumes = [d.volume for d in direct.days]
        self.assertEqual(volumes[0], 0.0)
        self.assertGreater(volumes[1], 0.0, "2日目以降は交通量が発生する")
        self.assertNotEqual(
            direct.days[-1].road_status, RoadStatus.SMOOTH,
            "道路を使い続けたのに順調のままなのはおかしい",
        )

    def test_detour_keeps_the_road_smooth(self) -> None:
        """迂回し続ければ、その道路は順調のままである。"""
        detour = self.results["B. 道路を避ける（迂回）"]
        for d in detour.days:
            self.assertEqual(d.stay_on_road, 0, "迂回中は道路に滞在しない")
            self.assertEqual(d.road_status, RoadStatus.SMOOTH)

    def test_waiting_scores_nothing(self) -> None:
        """待機し続けるとスポットに到達しないので得点が入らない。"""
        stay = self.results["C. その場で待機"]
        self.assertEqual(stay.final.total_udon, 0)
        self.assertEqual(stay.final.brand_count, 0)
        self.assertEqual(stay.final.positions, [START] * NUM_AGENTS, "位置が動かない")

    def test_moving_patterns_score(self) -> None:
        """移動する2パターンはうどんを獲得できる。"""
        for label in ("A. 道路を通る（直行）", "B. 道路を避ける（迂回）"):
            with self.subTest(pattern=label):
                self.assertGreater(self.results[label].final.total_udon, 0)
                self.assertEqual(self.results[label].final.brand_count, 1)

    def test_all_comparison_fields_are_available(self) -> None:
        """比較に必要な項目がすべて取得できること（指示書 第4章）。"""
        d = self.results["A. 道路を通る（直行）"].final
        for name in (
            "positions", "fuels", "total_udon", "brand_count",
            "daily_cumulative", "road_status", "volume",
        ):
            self.assertTrue(hasattr(d, name), f"{name} が取得できない")


def print_report() -> None:
    results = run_all()
    print("=" * 104)
    print("待機・渋滞道路への進入・回避の比較（実装指示書0828-2 第4章）")
    print("  マップ: [0]平地(出発) --[1]道路-- [2]平地◎スポット   /   迂回路: 0-4-5-2（すべて平地）")
    print(f"  1日{DAY_STEPS}ステップ × {NUM_DAYS}日 / 巡回車{NUM_AGENTS}体 / "
          f"混雑基準={BUSY} 渋滞基準={JAMMED} / スポット在庫99")
    print("=" * 104)

    for label, result in results.items():
        print(f"\n【{label}】")
        print("   日   道路状態  交通量  道路滞在 |         位置          |   燃料    | 玉数 系列 累積")
        print("  " + "-" * 96)
        for d in result.days:
            print(
                f"  {d.day + 1}日目   {ROAD_STATUS_LABEL[d.road_status]:<5} "
                f"{d.volume:>6.2f}   {d.stay_on_road:>4}   | {str(d.positions):<20} "
                f"| {str(d.fuels):<9} | {d.total_udon:>3} {d.brand_count:>3} {d.daily_cumulative:>4}"
            )

    print("\n" + "=" * 104)
    print("最終得点の比較（①種類数 → ②日ごと種類数の累積 → ③玉数 の順で優劣が決まる）")
    print("=" * 104)
    print(f"  {'パターン':<26}{'①種類数':>10}{'②累積':>10}{'③玉数':>10}{'最終の道路状態':>16}")
    for label, result in results.items():
        f = result.final
        print(f"  {label:<26}{f.brand_count:>10}{f.daily_cumulative:>10}{f.total_udon:>10}"
              f"{ROAD_STATUS_LABEL[f.road_status]:>16}")
    print("\n  ※ どの戦略が強いかは判定しない。同一条件で比較できることの確認のみ。")


if __name__ == "__main__":
    if "--report" in sys.argv:
        print_report()
    else:
        unittest.main(verbosity=2)
