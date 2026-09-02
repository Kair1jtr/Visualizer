"""マップ生成（`simulator/mapgen.py`）の検証。

`tests/test_configuration_matrix.py`から切り出した実装が、元のテストと
同じ性質を保っていること、および`experiment.py`のマップサイズ×構成の実験
（指示書#3・#6）にそのまま使えることを確認する。
"""

from __future__ import annotations

import unittest

from simulator import experiment, mapgen
from simulator.terrain import Terrain

PLAIN, ROAD, MOUNTAIN = int(Terrain.PLAIN), int(Terrain.ROAD), int(Terrain.MOUNTAIN)


class TestSquareMap(unittest.TestCase):
    def test_shape_matches_size(self):
        cells = mapgen.square_map(10)
        self.assertEqual(len(cells), 10)
        self.assertTrue(all(len(row) == 10 for row in cells))

    def test_has_a_vertical_road_through_the_middle(self):
        size = 9
        cells = mapgen.square_map(size)
        mid = size // 2
        self.assertTrue(all(row[mid] == ROAD for row in cells))

    def test_is_deterministic(self):
        self.assertEqual(mapgen.square_map(12), mapgen.square_map(12))

    def test_rejects_nonpositive_size(self):
        with self.assertRaises(ValueError):
            mapgen.square_map(0)


class TestPlainCells(unittest.TestCase):
    def test_row_major_indexing(self):
        # 1行2列: セル0=道路, セル1=平地
        cells = [[ROAD, PLAIN]]
        self.assertEqual(mapgen.plain_cells(cells), [1])

    def test_matches_square_map_road_column(self):
        size = 8
        cells = mapgen.square_map(size)
        plains = mapgen.plain_cells(cells)
        mid = size // 2
        for pos in plains:
            row, col = divmod(pos, size)
            self.assertNotEqual(cells[row][col], ROAD)
            if cells[row][col] != MOUNTAIN:
                self.assertNotEqual(col, mid, "道路の列は平地に含まれない")


class TestSquareScenarioFactory(unittest.TestCase):
    def test_produces_a_valid_state_with_requested_shape(self):
        factory = mapgen.square_scenario_factory(12, num_patrol=3, num_supply=1)
        state = factory()
        self.assertEqual(state.map.height, 12)
        self.assertEqual(state.map.width, 12)
        self.assertEqual(len(state.teams[0].agents), 4)

    def test_day_steps_within_official_range(self):
        """`daySteps`が公式範囲`W+H 〜 (W+H)×4`に収まること。〔Q20〕【確定】"""
        state = mapgen.square_scenario_factory(16, num_patrol=3, num_supply=1)()
        lo = state.map.width + state.map.height
        for ds in state.config.daySteps:
            self.assertTrue(lo <= ds <= lo * 4)

    def test_each_call_returns_a_fresh_independent_state(self):
        factory = mapgen.square_scenario_factory(8, num_patrol=1, num_supply=0)
        first = factory()
        second = factory()
        self.assertIsNot(first, second)
        self.assertEqual(first.teams[0].agents[0].pos, second.teams[0].agents[0].pos)

    def test_too_many_agents_for_map_size_raises(self):
        # 3x3から道路・山地を除いた残りの平地よりエージェント数が多い状況を作る
        factory = mapgen.square_scenario_factory(3, num_patrol=20, num_supply=0)
        with self.assertRaises(ValueError):
            factory()

    def test_multi_team_configuration(self):
        state = mapgen.square_scenario_factory(12, num_patrol=3, num_supply=1, num_teams=3)()
        self.assertEqual(len(state.teams), 3)
        for team in state.teams:
            self.assertEqual(len(team.agents), 4)


class TestSquareMapRandom(unittest.TestCase):
    def test_same_seed_is_deterministic(self):
        self.assertEqual(
            mapgen.square_map_random(12, seed=1), mapgen.square_map_random(12, seed=1)
        )

    def test_different_seeds_usually_differ(self):
        self.assertNotEqual(
            mapgen.square_map_random(12, seed=1), mapgen.square_map_random(12, seed=2)
        )

    def test_keeps_the_central_road(self):
        size = 10
        cells = mapgen.square_map_random(size, seed=7)
        mid = size // 2
        self.assertTrue(all(row[mid] == ROAD for row in cells))

    def test_mountain_ratio_zero_yields_all_plain_off_road(self):
        cells = mapgen.square_map_random(8, seed=3, mountain_ratio=0.0)
        mid = 8 // 2
        for row in cells:
            for c, v in enumerate(row):
                self.assertEqual(v, ROAD if c == mid else PLAIN)


class TestSquareScenarioFactoryRandom(unittest.TestCase):
    def test_same_seed_is_reproducible(self):
        factory = mapgen.square_scenario_factory_random(12, 3, 1, seed=5)
        first = factory()
        second = factory()
        self.assertEqual(
            [a.pos for a in first.teams[0].agents], [a.pos for a in second.teams[0].agents]
        )

    def test_different_seeds_can_place_agents_differently(self):
        starts = {
            seed: tuple(a.pos for a in mapgen.square_scenario_factory_random(12, 3, 1, seed=seed)().teams[0].agents)
            for seed in range(5)
        }
        self.assertGreater(len(set(starts.values())), 1, "5個のシードで配置が全く同じなのは不自然")

    def test_produces_a_valid_state(self):
        state = mapgen.square_scenario_factory_random(16, 4, 2, seed=42)()
        self.assertEqual(state.map.height, 16)
        self.assertEqual(len(state.teams[0].agents), 6)


class TestIntegratesWithExperiment(unittest.TestCase):
    """指示書#3・#6: マップサイズ×構成の実験を`experiment.py`経由で回せること。"""

    def test_map_size_by_config_matrix_runs_and_is_comparable(self):
        conditions = []
        for size in (8, 16):
            for patrol, supply in ((3, 1), (2, 2)):
                conditions.append(
                    experiment.ExperimentCondition(
                        label=f"size={size} patrol={patrol} supply={supply}",
                        state_factory=mapgen.square_scenario_factory(
                            size, patrol, supply
                        ),
                        strategies={0: "greedy"},
                        metadata={"map_size": size, "patrol": patrol, "supply": supply},
                    )
                )
        trials = experiment.run_matrix(conditions)
        self.assertEqual(len(trials), 4)
        rows = experiment.summary_rows(trials)
        self.assertEqual(len(rows), 4)
        for row in rows:
            self.assertEqual(row["rejected"], "", "リジェクトなしで完走すること")


if __name__ == "__main__":
    unittest.main()
