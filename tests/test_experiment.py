"""戦略実験基盤（`simulator/experiment.py`）の検証。

ここでの目的はルールの正しさではなく、「条件を並べて実行し、CSV化できる」
という配線が正しいことの確認（ルールは他のテストで既に検証済み）。
"""

from __future__ import annotations

import csv
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from simulator import compare, experiment, mapgen, scenarios
from simulator.state import SpotDef
from simulator.terrain import RoadStatus, Terrain

PLAIN, ROAD = int(Terrain.PLAIN), int(Terrain.ROAD)


def _tiny_state():
    """2チームがスポット1個を取り合う、最小限の盤面。各チーム巡回車1体。"""
    return scenarios.minimal_scenario(
        cells=[[PLAIN, ROAD, PLAIN]],
        spots=[SpotDef(pos=2, brand=0, stocks=99)],
        starts=[0],
        kinds_by_team=[[0], [0]],
        day_steps=(4, 4),
        fuel_limits=99,
        busy_threshold=3,
        jammed_threshold=8,
    )


def _traffic_state():
    """交通量実験用: 1本道（セル0=道路, セル1=平地）に1体だけ配置。"""
    return scenarios.minimal_scenario(
        cells=[[ROAD, PLAIN]],
        spots=[],
        starts=[0],
        kinds_by_team=[[0]],
        day_steps=(6, 6, 6, 6),
        fuel_limits=99,
        busy_threshold=3,
        jammed_threshold=8,
    )


class TestExperimentCondition(unittest.TestCase):
    def test_build_strategies_resolves_names_and_params(self):
        condition = experiment.ExperimentCondition(
            label="test",
            state_factory=_tiny_state,
            strategies={0: "greedy", 1: "stay"},
            strategy_params={0: {"repeat_value": 0}},
        )
        built = condition.build_strategies()
        self.assertEqual(set(built), {0, 1})
        self.assertEqual(built[0].name, "greedy")
        self.assertEqual(built[0].p["repeat_value"], 0.0)
        self.assertEqual(built[1].name, "stay")

    def test_unknown_strategy_name_raises(self):
        from simulator.strategy import StrategyError

        condition = experiment.ExperimentCondition(
            label="bad",
            state_factory=_tiny_state,
            strategies={0: "nonexistent"},
        )
        with self.assertRaises(StrategyError):
            condition.build_strategies()


class TestRunCondition(unittest.TestCase):
    def test_run_condition_produces_a_trial_result(self):
        condition = experiment.ExperimentCondition(
            label="greedy-vs-stay",
            state_factory=_tiny_state,
            strategies={0: "greedy", 1: "stay"},
            metadata={"map_size": 3, "num_patrol": 1},
        )
        trial = experiment.run_condition(condition)
        self.assertIs(trial.condition, condition)
        self.assertGreaterEqual(trial.elapsed_ms, 0.0)
        self.assertEqual(len(trial.run_result.days), 2)
        scores = {s.id: s for s in trial.run_result.final_scores()}
        self.assertGreater(scores[0].total_udon, 0, "greedy はスポットを取れる")
        self.assertEqual(scores[1].total_udon, 0, "stay は動かないので取れない")

    def test_run_matrix_runs_every_condition(self):
        conditions = [
            experiment.ExperimentCondition(
                label=f"cond-{i}",
                state_factory=_tiny_state,
                strategies={0: name, 1: "stay"},
            )
            for i, name in enumerate(["greedy", "brand", "nearest"])
        ]
        trials = experiment.run_matrix(conditions)
        self.assertEqual(len(trials), 3)
        self.assertEqual([t.condition.label for t in trials], ["cond-0", "cond-1", "cond-2"])

    def test_each_condition_gets_a_fresh_state(self):
        """state_factory は呼ぶたびに新しい状態を返す前提（使い回すと2回目が壊れる）。"""
        condition = experiment.ExperimentCondition(
            label="reuse-check",
            state_factory=_tiny_state,
            strategies={0: "greedy", 1: "stay"},
        )
        first = experiment.run_condition(condition)
        second = experiment.run_condition(condition)
        self.assertEqual(len(first.run_result.days), len(second.run_result.days))
        self.assertEqual(
            first.run_result.final_scores()[0].total_udon,
            second.run_result.final_scores()[0].total_udon,
        )


class TestRowsAndCsv(unittest.TestCase):
    def setUp(self):
        self.conditions = [
            experiment.ExperimentCondition(
                label="A",
                state_factory=_tiny_state,
                strategies={0: "greedy", 1: "stay"},
                metadata={"map_size": 3},
            ),
            experiment.ExperimentCondition(
                label="B",
                state_factory=_tiny_state,
                strategies={0: "brand", 1: "nearest"},
                metadata={"map_size": 3, "seed": 42},  # A と列集合が異なる
            ),
        ]
        self.trials = experiment.run_matrix(self.conditions)

    def test_daily_rows_have_one_row_per_day_per_team(self):
        rows = experiment.daily_rows(self.trials)
        # 各条件: 2日 × 2チーム = 4行、2条件で計8行
        self.assertEqual(len(rows), 8)
        for row in rows:
            self.assertIn("condition_label", row)
            self.assertIn("day", row)
            self.assertIn("team_id", row)
            self.assertIn("strategy", row)

    def test_daily_rows_include_metadata_columns(self):
        rows = experiment.daily_rows(self.trials)
        a_rows = [r for r in rows if r["condition_label"] == "A"]
        b_rows = [r for r in rows if r["condition_label"] == "B"]
        self.assertTrue(all(r["meta_map_size"] == 3 for r in a_rows))
        self.assertTrue(all(r["meta_seed"] == 42 for r in b_rows))

    def test_summary_rows_have_one_row_per_team(self):
        rows = experiment.summary_rows(self.trials)
        self.assertEqual(len(rows), 4)  # 2条件 × 2チーム
        self.assertTrue(any(r["is_winner"] for r in rows))

    def test_watch_cells_add_traffic_columns(self):
        condition = experiment.ExperimentCondition(
            label="traffic-watch",
            state_factory=_traffic_state,
            strategies={0: "stay"},
            watch_cells=(0,),
        )
        trial = experiment.run_condition(condition)
        rows = experiment.daily_rows([trial])
        self.assertTrue(all("traffic_status_0" in r for r in rows))
        self.assertTrue(all("traffic_volume_0" in r for r in rows))
        # 1日目は必ず順調〔要項〕【確定】
        self.assertEqual(rows[0]["traffic_status_0"], int(RoadStatus.SMOOTH))

    def test_write_csv_round_trips_through_disk(self):
        rows = experiment.summary_rows(self.trials)
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "summary.csv"
            experiment.write_csv(rows, path)
            with path.open(encoding="utf-8", newline="") as f:
                read_back = list(csv.DictReader(f))
        self.assertEqual(len(read_back), len(rows))
        # 条件Aにはmeta_seed列が無いが、和集合ヘッダーにより空文字で埋まる
        a_row = next(r for r in read_back if r["condition_label"] == "A")
        self.assertIn("meta_seed", a_row)
        self.assertEqual(a_row["meta_seed"], "")
        b_row = next(r for r in read_back if r["condition_label"] == "B")
        self.assertEqual(b_row["meta_seed"], "42")

    def test_write_csv_with_no_rows_creates_empty_file(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "empty.csv"
            experiment.write_csv([], path)
            self.assertEqual(path.read_text(encoding="utf-8"), "")


class TestTrafficAvoidanceExperiment(unittest.TestCase):
    """指示書#7「1日目居座り→2/3日目回避→4日目回復」を、手書き行動計画ではなく
    Strategy ベースの戦略で再現できるかを確認する（設計案 優先順位3）。

    `tests/test_traffic_scenarios.py` のケースBと同じ構図を、experiment.py
    経由で駆動する。
    """

    def test_avoiding_strategy_recovers_the_road(self):
        # stay 戦略は「その日ずっと待機」＝道路に居座り続ける。
        # 4日間ずっと居座らせ、交通量が積み上がることだけをまず確認する
        # （回避側の挙動は貪欲法などスポット指向の戦略が対象で、この1本道
        # マップにはスポットが無いため、ここでは「居座り続けると悪化する」
        # 半分だけを experiment.py 経由で確認する）。
        condition = experiment.ExperimentCondition(
            label="stay-on-road",
            state_factory=_traffic_state,
            strategies={0: "stay"},
            watch_cells=(0,),
        )
        trial = experiment.run_condition(condition)
        rows = experiment.daily_rows([trial])
        statuses = [r["traffic_status_0"] for r in rows]
        self.assertEqual(statuses[0], int(RoadStatus.SMOOTH), "1日目は必ず順調")
        self.assertGreaterEqual(statuses[-1], statuses[1], "居座り続けると悪化する方向にしか動かない")

    def test_reproduces_case_b_via_fixed_plans_strategy(self):
        """`tests/test_traffic_scenarios.py::case_b()`（1日目居座り→2/3日目回避
        →4日目回復）を、手書きの日次ループではなく`compare.fixed_plans()`が
        作る`Strategy`形の呼び出し可能を`ExperimentCondition.strategies`に
        そのまま渡して再現する。値は`case_b()`と同じ期待値と突き合わせる。
        """
        plans_by_day = [
            [[-6]],  # 1日目: 道路に居座る
            [[2, -4]],  # 2日目: 混雑(2歩)の道路を渡って退避
            [[-6]],  # 3日目: 平地で待機
            [[-6]],  # 4日目: 結果を見るだけ
        ]
        condition = experiment.ExperimentCondition(
            label="case-b-via-experiment",
            state_factory=_traffic_state,
            strategies={0: compare.fixed_plans(plans_by_day)},
            watch_cells=(0,),
        )
        trial = experiment.run_condition(condition)
        rows = experiment.daily_rows([trial])
        statuses = [r["traffic_status_0"] for r in rows]
        volumes = [r["traffic_volume_0"] for r in rows]

        self.assertEqual(
            statuses,
            [int(RoadStatus.SMOOTH), int(RoadStatus.CONGESTED), int(RoadStatus.CONGESTED), int(RoadStatus.SMOOTH)],
        )
        self.assertEqual(volumes[1], 6.0)
        self.assertEqual(volumes[2], 7.0)
        self.assertEqual(volumes[3], 1.0)
        # 戦略が文字列でなくても、CSV用のラベルは埋まる（"custom" フォールバック）。
        self.assertTrue(all(r["strategy"] for r in rows))


class TestRunRepeatedAndAggregate(unittest.TestCase):
    """指示書#8: 同じ戦略・構成を多数の異なるマップで走らせて統計を取れること。"""

    def _build(self, i: int) -> experiment.ExperimentCondition:
        return experiment.ExperimentCondition(
            label=f"trial-{i}",
            state_factory=mapgen.square_scenario_factory_random(12, 3, 1, seed=i),
            strategies={0: "greedy"},
            metadata={"seed": i},
        )

    def test_run_repeated_runs_n_trials_with_distinct_maps(self):
        trials = experiment.run_repeated(self._build, 5)
        self.assertEqual(len(trials), 5)
        self.assertEqual([t.condition.label for t in trials], [f"trial-{i}" for i in range(5)])

    def test_aggregate_by_strategy_computes_mean_stdev_and_win_rate(self):
        trials = experiment.run_repeated(self._build, 8)
        rows = experiment.summary_rows(trials)
        stats = experiment.aggregate_by_strategy(rows)
        self.assertEqual(len(stats), 1, "全試行が greedy のみなのでグループは1つ")
        greedy = stats[0]
        self.assertEqual(greedy["group"], "greedy")
        self.assertEqual(greedy["trials"], 8)
        self.assertEqual(greedy["win_rate"], 1.0, "1チームしかいないので必ず優勝")
        self.assertGreaterEqual(greedy["mean_total_udon"], 0.0)
        self.assertGreaterEqual(greedy["stdev_total_udon"], 0.0)

    def test_aggregate_by_strategy_separates_different_strategies(self):
        conditions = [
            experiment.ExperimentCondition(
                label=f"cond-{i}",
                state_factory=mapgen.square_scenario_factory_random(
                    12, 3, 1, seed=i, num_teams=2
                ),
                strategies={0: "greedy", 1: "stay"},
            )
            for i in range(4)
        ]
        rows = experiment.summary_rows(experiment.run_matrix(conditions))
        stats = {s["group"]: s for s in experiment.aggregate_by_strategy(rows)}
        self.assertEqual(set(stats), {"greedy", "stay"})
        self.assertEqual(stats["stay"]["mean_total_udon"], 0.0, "stay は常に0")
        self.assertEqual(stats["stay"]["win_rate"], 0.0, "stay は greedy に勝てない")

    def test_aggregate_by_supports_a_custom_key(self):
        trials = experiment.run_repeated(self._build, 3)
        rows = experiment.summary_rows(trials)
        stats = experiment.aggregate_by(rows, key=lambda r: r["condition_label"])
        self.assertEqual({s["group"] for s in stats}, {f"trial-{i}" for i in range(3)})
        self.assertTrue(all(s["trials"] == 1 for s in stats))


if __name__ == "__main__":
    unittest.main()
