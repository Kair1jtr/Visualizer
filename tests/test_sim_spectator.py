"""参照戦略（`simulator/strategy.py`）と観戦アダプタ（`visualizer/sim_spectator.py`）の検証。

戦略側でいちばん大事なのは「組み立てた行動計画が公式の受付時検証を通ること」。
1体でも不正なら回答全体がリジェクトされる〔書式〕【確定】ため、
通らない戦略はその日を丸ごと失う。

観戦アダプタ側は、`visualizer/spectator.py`（公式簡易サーバーの観戦）と
**同じ形** の JSON を返すことを確認する。ブラウザ側が同じ描画コードで
両方を扱えるのはこれが前提になっている。
"""

import unittest

from simulator import engine, validation
from simulator.strategy import STRATEGIES, greedy_team_plan, stay_team_plan
from visualizer.sim_spectator import (
    DEFAULT_CONFIG,
    SimSpectator,
    SimSpectatorError,
    available_strategies,
    load_match_config,
    parse_strategies,
    run_simulation,
)

# `spectator.MatchSpectator.summary()` が返すキー。この形が崩れると
# ブラウザ側の共通描画（static/js/matchview.js）が両対応できなくなる。
SPECTATOR_KEYS = {
    "running",
    "phase",
    "connected",
    "error",
    "ended",
    "setting",
    "currentDay",
    "numDays",
    "teams",
    "days",
}


class TestGreedyStrategy(unittest.TestCase):
    def test_every_strategy_passes_official_validation_every_day(self):
        """どの戦略が作る計画も、全日・全チームで受付時検証を通る。"""
        for name, strategy in STRATEGIES.items():
            with self.subTest(strategy=name):
                state, _names = load_match_config(DEFAULT_CONFIG)
                while not state.finished:
                    engine.begin_day(state)
                    plans = {
                        t.id: strategy(state, t.id) for t in state.teams
                    }
                    errors = validation.validate_all(state, plans)
                    for team_id, error in errors.items():
                        self.assertIsNone(
                            error,
                            f"{name}: {state.day + 1}日目 チーム{team_id} が"
                            f"リジェクトされた: {error}",
                        )
                    engine.run_day_body(state, plans)

    def test_plan_step_total_matches_day_steps(self):
        """行動計画は1日のステップ数と一致する必要がある〔書式〕【確定】。"""
        from simulator.actions import total_steps, walk_plan

        state, _names = load_match_config(DEFAULT_CONFIG)
        engine.begin_day(state)
        team = state.teams[0]
        plan = greedy_team_plan(state, team.id)
        self.assertEqual(len(plan), len(team.agents))
        for agent, agent_plan in zip(team.agents, plan):
            walk = walk_plan(
                agent_plan,
                agent.pos,
                state.map,
                state.traffic.traffics,
                is_patrol=agent.is_patrol,
            )
            self.assertEqual(total_steps(walk), state.steps_today)

    def test_greedy_beats_staying_put(self):
        """貪欲法は待機だけの相手より上位になる（勝敗①種類数）。〔要項〕【確定】"""
        spectator = run_simulation(strategy="greedy,stay")
        ranking = spectator.summary()["ranking"]
        self.assertEqual(ranking[0]["teamId"], 0)
        self.assertGreater(ranking[0]["brandCount"], 0)
        self.assertEqual(ranking[1]["brandCount"], 0)

    def test_stay_strategy_is_a_single_wait(self):
        state, _names = load_match_config(DEFAULT_CONFIG)
        engine.begin_day(state)
        plan = stay_team_plan(state, 0)
        self.assertTrue(all(p == [-state.steps_today] for p in plan))


class TestSimSpectator(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.summary = run_simulation(strategy="greedy,stay", run_key=7).summary()

    def test_summary_has_the_same_keys_as_the_real_spectator(self):
        self.assertTrue(SPECTATOR_KEYS.issubset(self.summary.keys()))

    def test_setting_follows_the_official_map_format(self):
        """〔書式〕のマップ構成フォーマットと同じキーを持つ。"""
        setting = self.summary["setting"]
        for key in (
            "daySteps",
            "daySeconds",
            "map",
            "spots",
            "agents",
            "fuelLimits",
            "players",
            "busyThreshold",
            "jammedThreshold",
        ):
            self.assertIn(key, setting)
        self.assertEqual(len(setting["map"]["cells"]), setting["map"]["height"])
        self.assertEqual(len(setting["map"]["cells"][0]), setting["map"]["width"])
        # 実行ごとに変わるキー。盤面を作り直すかの判定に使う。
        self.assertEqual(setting["key"], "sim7")

    def test_one_record_per_day(self):
        self.assertEqual(len(self.summary["days"]), self.summary["numDays"])
        self.assertEqual(self.summary["phase"], "ended")

    def test_steps_cover_zero_through_the_last_step(self):
        """0ステップ目から最終ステップまで全部の状態を持つ〔Q6〕〔補足〕【確定】。"""
        for day in self.summary["days"]:
            self.assertEqual(len(day["steps"]), day["numSteps"] + 1)
            self.assertEqual(day["steps"][0]["step"], 0)
            self.assertEqual(day["steps"][-1]["step"], day["numSteps"])

    def test_trajectories_are_measured_not_estimated(self):
        """軌跡の両端が、ステップ記録の最初と最後の位置に一致する。"""
        self.assertTrue(self.summary["exact"])
        for day in self.summary["days"]:
            for team_id, rows in day["trajectories"].items():
                for index, row in enumerate(rows):
                    first = day["steps"][0]["agentsByTeam"][team_id][index]["pos"]
                    last = day["steps"][-1]["agentsByTeam"][team_id][index]["pos"]
                    self.assertEqual(row["start"], first)
                    self.assertEqual(row["end"], last)
                    self.assertEqual(row["path"][0], first)
                    self.assertEqual(row["path"][-1], last)

    def test_trajectory_path_has_no_repeats_or_jumps(self):
        """経路は連続するセル列（同じセルの連続なし・隣接のみ）。"""
        state, _names = load_match_config(DEFAULT_CONFIG)
        map_ = state.map
        for day in self.summary["days"]:
            for rows in day["trajectories"].values():
                for row in rows:
                    for a, b in zip(row["path"], row["path"][1:]):
                        self.assertNotEqual(a, b)
                        self.assertIn(b, map_.neighbors(a))

    def test_day_start_snapshot_matches_step_zero(self):
        for day in self.summary["days"]:
            self.assertEqual(day["agentsByTeam"], day["steps"][0]["agentsByTeam"])

    def test_traffics_cover_every_road_cell(self):
        state, _names = load_match_config(DEFAULT_CONFIG)
        roads = set(state.map.road_cells())
        for day in self.summary["days"]:
            self.assertEqual({t["pos"] for t in day["traffics"]}, roads)
            for t in day["traffics"]:
                self.assertIn(t["status"], (0, 1, 2))

    def test_first_day_roads_are_all_smooth(self):
        """1日目の道路状態は全て順調〔要項〕【確定】。"""
        for t in self.summary["days"][0]["traffics"]:
            self.assertEqual(t["status"], 0)

    def test_no_rejections_with_the_reference_strategies(self):
        for day in self.summary["days"]:
            self.assertTrue(all(v is None for v in day["rejected"].values()))


class TestPerPlayerStrategies(unittest.TestCase):
    """プレイヤーごとの戦略割り当て（`parse_strategies`）。"""

    def test_single_name_applies_to_everyone(self):
        self.assertEqual(parse_strategies("greedy", 3), ["greedy"] * 3)

    def test_positional_list_maps_in_order(self):
        self.assertEqual(
            parse_strategies("greedy,stay,brand", 3), ["greedy", "stay", "brand"]
        )

    def test_indexed_form_overrides_only_that_player(self):
        self.assertEqual(
            parse_strategies("1:stay", 3), ["greedy", "stay", "greedy"]
        )

    def test_positional_and_indexed_can_be_mixed(self):
        self.assertEqual(
            parse_strategies("nearest,2:stay", 3), ["nearest", "nearest", "stay"]
        )

    def test_wrong_count_is_rejected(self):
        with self.assertRaises(SimSpectatorError):
            parse_strategies("greedy,stay", 3)

    def test_index_out_of_range_is_rejected(self):
        with self.assertRaises(SimSpectatorError):
            parse_strategies("5:stay", 3)

    def test_non_integer_index_is_rejected(self):
        with self.assertRaises(SimSpectatorError):
            parse_strategies("x:stay", 3)

    def test_available_strategies_covers_the_registry(self):
        names = [s["name"] for s in available_strategies()]
        self.assertEqual(set(names), set(STRATEGIES))
        for entry in available_strategies():
            self.assertTrue(entry["description"], f"{entry['name']} に説明が無い")

    def test_assigned_strategy_is_reported_per_team(self):
        summary = run_simulation(strategy="brand,1:stay").summary()
        self.assertEqual(
            [t["strategy"] for t in summary["teams"]], ["brand", "stay"]
        )


class TestSimSpectatorErrors(unittest.TestCase):
    def test_unknown_strategy_is_rejected(self):
        state, names = load_match_config(DEFAULT_CONFIG)
        with self.assertRaises(SimSpectatorError):
            SimSpectator(state, team_names=names, strategy="nonexistent")

    def test_strategy_count_must_match_player_count(self):
        state, names = load_match_config(DEFAULT_CONFIG)
        with self.assertRaises(SimSpectatorError):
            SimSpectator(state, team_names=names, strategy="greedy,stay,greedy")

    def test_missing_config_file_is_reported(self):
        from pathlib import Path

        with self.assertRaises(SimSpectatorError):
            load_match_config(Path("存在しない設定.json"))


if __name__ == "__main__":
    unittest.main()
