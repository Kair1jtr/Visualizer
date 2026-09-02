"""エージェント構成とマップサイズを変えた比較が行えることの確認。

実装指示書0828-2 第5章・第6章に対応する。

    第5章: エージェント数3〜8体 × 巡回車／補給車の比率を入力して比較できるか
    第6章: 8×8 〜 32×32 のマップを入力として扱えるか

**最適な比率やサイズを決めることは目的ではない。** 入力として受け付けられ、
同一条件で結果を比較できることの確認のみを行う。

表として確認したい場合:

    python tests/test_configuration_matrix.py --report
"""

from __future__ import annotations

import sys
import time
import unittest
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from simulator import compare, engine, mapgen, strategy  # noqa: E402
from simulator.terrain import Terrain  # noqa: E402

PLAIN, ROAD, MOUNTAIN = int(Terrain.PLAIN), int(Terrain.ROAD), int(Terrain.MOUNTAIN)

# 実装指示書 第5章で指定された構成（エージェント数, 巡回車数, 補給車数）
AGENT_CONFIGS: list[tuple[int, int, int]] = [
    (3, 2, 1),
    (4, 3, 1), (4, 2, 2),
    (5, 4, 1), (5, 3, 2),
    (6, 5, 1), (6, 4, 2),
    (7, 6, 1), (7, 5, 2),
    (8, 7, 1), (8, 6, 2),
]

# 実装指示書 第6章で指定されたマップサイズ
MAP_SIZES = [8, 12, 16, 20, 24, 32]

NUM_DAYS = 4


def make_state(
    size: int,
    num_patrol: int,
    num_supply: int,
    *,
    num_teams: int = 1,
    num_days: int = NUM_DAYS,
):
    """指定のマップサイズ・エージェント構成で初期状態を作る（`mapgen`に委譲）。"""
    return mapgen.square_scenario_factory(
        size, num_patrol, num_supply, num_teams=num_teams, num_days=num_days
    )()


@dataclass
class RunSummary:
    label: str
    brand_count: int
    daily_cumulative: int
    total_udon: int
    rejected_days: int
    elapsed_ms: float


def run_once(state, strategy_name: str = "greedy") -> RunSummary:
    """1試合を最後まで進めて結果をまとめる。"""
    fn = strategy.create(strategy_name)
    start = time.perf_counter()
    rejected = 0
    while not state.finished:
        engine.begin_day(state)
        plans = {t.id: fn(state, t.id) for t in state.teams}
        results = engine.run_day_body(state, plans)
        rejected += sum(1 for e in results.values() if e is not None)
    elapsed = (time.perf_counter() - start) * 1000
    team = state.teams[0]
    return RunSummary(
        label="",
        brand_count=team.brand_count,
        daily_cumulative=team.daily_brand_cumulative,
        total_udon=team.total_udon,
        rejected_days=rejected,
        elapsed_ms=elapsed,
    )


class AgentConfigurationTest(unittest.TestCase):
    """第5章: エージェント数と巡回車／補給車の比率を変えて比較できること。"""

    def test_all_configurations_run(self) -> None:
        """指定された11通りの構成すべてが、リジェクトなしで完走すること。"""
        for total, patrol, supply in AGENT_CONFIGS:
            with self.subTest(agents=total, patrol=patrol, supply=supply):
                self.assertEqual(patrol + supply, total)
                state = make_state(12, patrol, supply)
                self.assertEqual(len(state.teams[0].agents), total)
                summary = run_once(state)
                self.assertEqual(
                    summary.rejected_days, 0,
                    f"{total}体（巡回{patrol}/補給{supply}）でリジェクトが発生した",
                )

    def test_agent_count_range_matches_official(self) -> None:
        """検証する構成が公式のエージェント数範囲（3〜8体）に収まること。〔Q50〕【確定】"""
        for total, _p, _s in AGENT_CONFIGS:
            self.assertTrue(3 <= total <= 8, f"エージェント数が公式範囲外: {total}")

    def test_supply_only_and_patrol_only_edge_cases(self) -> None:
        """全員巡回車・全員補給車の極端な構成も入力として扱えること。"""
        for patrol, supply in [(8, 0), (0, 8)]:
            with self.subTest(patrol=patrol, supply=supply):
                summary = run_once(make_state(12, patrol, supply))
                self.assertEqual(summary.rejected_days, 0)
                if patrol == 0:
                    self.assertEqual(summary.total_udon, 0, "補給車だけでは獲得できない")

    def test_configurations_are_comparable(self) -> None:
        """同一マップで構成違いの結果を並べて比較できること。"""
        results = {}
        for total, patrol, supply in AGENT_CONFIGS:
            results[(total, patrol, supply)] = run_once(make_state(12, patrol, supply))
        self.assertEqual(len(results), len(AGENT_CONFIGS))
        # 比較に必要な指標がすべて揃っている
        for key, s in results.items():
            self.assertIsInstance(s.brand_count, int)
            self.assertIsInstance(s.daily_cumulative, int)
            self.assertIsInstance(s.total_udon, int)


class MapSizeTest(unittest.TestCase):
    """第6章: マップサイズを変えても正常に動作すること。"""

    def test_all_sizes_run(self) -> None:
        """8×8 〜 32×32 のすべてでリジェクトなしに完走すること。"""
        for size in MAP_SIZES:
            with self.subTest(size=size):
                state = make_state(size, 3, 1)
                self.assertEqual(state.map.height, size)
                self.assertEqual(state.map.width, size)
                summary = run_once(state)
                self.assertEqual(summary.rejected_days, 0, f"{size}×{size} でリジェクト")

    def test_sizes_are_within_official_range(self) -> None:
        """検証するサイズが公式範囲（8〜32）に収まること。〔要項〕【確定】"""
        for size in MAP_SIZES:
            self.assertTrue(8 <= size <= 32, f"マップサイズが公式範囲外: {size}")

    def test_day_steps_within_official_range(self) -> None:
        """`daySteps` が公式範囲 `W+H 〜 (W+H)×4` に収まること。〔Q20〕【確定】"""
        for size in MAP_SIZES:
            with self.subTest(size=size):
                state = make_state(size, 3, 1)
                lo = state.map.width + state.map.height
                for ds in state.config.daySteps:
                    self.assertTrue(lo <= ds <= lo * 4, f"daySteps={ds} が範囲外（{lo}〜{lo*4}）")

    def test_only_map_size_changes(self) -> None:
        """マップサイズだけを変えても、他の条件が同じなら動作すること。"""
        summaries = {size: run_once(make_state(size, 3, 1)) for size in MAP_SIZES}
        self.assertEqual(len(summaries), len(MAP_SIZES))
        for size, s in summaries.items():
            self.assertEqual(s.rejected_days, 0)

    def test_multi_team_on_large_map(self) -> None:
        """複数チーム × 大きいマップでも動作すること。"""
        state = make_state(32, 7, 1, num_teams=4)
        summary = run_once(state)
        self.assertEqual(summary.rejected_days, 0)
        self.assertEqual(len(state.teams), 4)


def print_report() -> None:
    print("=" * 92)
    print("エージェント構成の比較（実装指示書0828-2 第5章）  マップ12×12・4日・戦略=greedy")
    print("=" * 92)
    print(f"  {'構成':<26}{'①種類数':>9}{'②累積':>8}{'③玉数':>8}{'リジェクト':>10}{'所要':>10}")
    print("  " + "-" * 88)
    for total, patrol, supply in AGENT_CONFIGS:
        s = run_once(make_state(12, patrol, supply))
        label = f"{total}体: 巡回{patrol} + 補給{supply}"
        print(f"  {label:<26}{s.brand_count:>9}{s.daily_cumulative:>8}{s.total_udon:>8}"
              f"{s.rejected_days:>10}{s.elapsed_ms:>8.1f}ms")

    print()
    print("=" * 92)
    print("マップサイズの比較（実装指示書0828-2 第6章）  巡回3+補給1・4日・戦略=greedy")
    print("=" * 92)
    print(f"  {'サイズ':<12}{'daySteps':>10}{'①種類数':>9}{'②累積':>8}{'③玉数':>8}"
          f"{'リジェクト':>10}{'所要':>10}")
    print("  " + "-" * 88)
    for size in MAP_SIZES:
        state = make_state(size, 3, 1)
        ds = state.config.daySteps[0]
        s = run_once(state)
        print(f"  {f'{size}×{size}':<12}{ds:>10}{s.brand_count:>9}{s.daily_cumulative:>8}"
              f"{s.total_udon:>8}{s.rejected_days:>10}{s.elapsed_ms:>8.1f}ms")
    print("\n  ※ 最適な構成・サイズの決定は目的ではない。入力として扱えることの確認のみ。")


if __name__ == "__main__":
    if "--report" in sys.argv:
        print_report()
    else:
        unittest.main(verbosity=2)
