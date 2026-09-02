"""戦略クラスとパラメータの検証。

`simulator/strategy.py` は「継承して自分の戦略を足せる」ことを目的にしている。
ここでは、その土台（パラメータの宣言・型変換・範囲検査・継承・登録）が
壊れていないことを確認する。
"""

import unittest

from simulator import engine, validation
from simulator.strategy import (
    STRATEGY_CLASSES,
    GreedyStrategy,
    Param,
    SpotScoreStrategy,
    Strategy,
    StrategyError,
    create,
    override_defaults,
    schemas,
)
from visualizer.sim_spectator import DEFAULT_CONFIG, load_match_config


class TestParam(unittest.TestCase):
    def test_number_is_coerced_to_the_declared_type(self):
        self.assertIsInstance(Param("a", "A", "int", 1).coerce("3"), int)
        self.assertIsInstance(Param("a", "A", "float", 1.0).coerce("3"), float)

    def test_out_of_range_is_rejected(self):
        param = Param("a", "A", "float", 1.0, minimum=0.0, maximum=10.0)
        self.assertEqual(param.coerce(10), 10.0)
        with self.assertRaises(StrategyError):
            param.coerce(-1)
        with self.assertRaises(StrategyError):
            param.coerce(11)

    def test_non_numeric_is_rejected(self):
        with self.assertRaises(StrategyError):
            Param("a", "A", "float", 1.0).coerce("abc")

    def test_bool_accepts_strings(self):
        param = Param("a", "A", "bool", True)
        self.assertIs(param.coerce("false"), False)
        self.assertIs(param.coerce("on"), True)

    def test_choice_must_be_one_of_the_choices(self):
        param = Param("a", "A", "choice", "x", choices=("x", "y"))
        self.assertEqual(param.coerce("y"), "y")
        with self.assertRaises(StrategyError):
            param.coerce("z")

    def test_schema_carries_the_bounds(self):
        schema = Param("a", "A", "int", 3, minimum=1, maximum=9, step=2).to_schema()
        self.assertEqual(schema["min"], 1)
        self.assertEqual(schema["max"], 9)
        self.assertEqual(schema["step"], 2)


class TestStrategyConstruction(unittest.TestCase):
    def test_defaults_are_used_when_nothing_is_given(self):
        strategy = create("greedy")
        self.assertEqual(strategy.p["new_brand_value"], 6.0)

    def test_given_values_override_defaults(self):
        strategy = create("greedy", {"new_brand_value": 20})
        self.assertEqual(strategy.p["new_brand_value"], 20.0)

    def test_unknown_parameter_is_rejected(self):
        with self.assertRaises(StrategyError):
            create("greedy", {"nonexistent": 1})

    def test_unknown_strategy_is_rejected(self):
        with self.assertRaises(StrategyError):
            create("nonexistent")

    def test_settings_records_what_was_used(self):
        settings = create("greedy", {"repeat_value": 0}).settings()
        self.assertEqual(settings["strategy"], "greedy")
        self.assertEqual(settings["params"]["repeat_value"], 0.0)


class TestInheritance(unittest.TestCase):
    def test_brand_inherits_greedy_and_only_changes_defaults(self):
        """系列優先は貪欲法のサブクラスで、重みの既定値だけが違う。"""
        brand = create("brand")
        greedy = create("greedy")
        self.assertIsInstance(brand, GreedyStrategy)
        self.assertEqual(set(brand.p), set(greedy.p))
        self.assertEqual(brand.p["repeat_value"], 0.0)
        self.assertEqual(greedy.p["repeat_value"], 1.0)

    def test_override_defaults_rejects_unknown_names(self):
        with self.assertRaises(StrategyError):
            override_defaults(GreedyStrategy.params, nonexistent=1)

    def test_a_new_subclass_only_needs_score_spot(self):
        """継承して score_spot を書くだけで、有効な行動計画が作れる。"""

        class StockHungry(SpotScoreStrategy):
            name = "test-stock"
            label = "在庫優先（テスト用）"
            params = SpotScoreStrategy.params + (
                Param("stock_weight", "在庫の重み", "float", 2.0, minimum=0.0),
            )

            def score_spot(self, state, team, spot, dist):
                stock = team.spot_stocks.get(spot.pos, 0)
                return stock * self.p["stock_weight"] * self._distance_factor(dist)

        strategy = StockHungry(stock_weight=5)
        state, _names = load_match_config(DEFAULT_CONFIG)
        engine.begin_day(state)
        for team in state.teams:
            plan = strategy(state, team.id)
            self.assertIsNone(validation.validate_team_plan(state, team, plan))

    def test_plan_must_be_implemented(self):
        class Empty(Strategy):
            name = "test-empty"

        state, _names = load_match_config(DEFAULT_CONFIG)
        engine.begin_day(state)
        with self.assertRaises(NotImplementedError):
            Empty()(state, 0)


class TestRegistryAndSchemas(unittest.TestCase):
    def test_every_registered_class_exposes_a_schema(self):
        names = {s["name"] for s in schemas()}
        self.assertEqual(names, set(STRATEGY_CLASSES))
        for entry in schemas():
            self.assertTrue(entry["label"], f"{entry['name']} に label が無い")
            self.assertTrue(entry["description"], f"{entry['name']} に説明が無い")
            for param in entry["params"]:
                self.assertIn(param["kind"], ("int", "float", "bool", "choice"))
                self.assertIn("default", param)

    def test_schema_defaults_construct_without_error(self):
        """スキーマの既定値をそのまま送り返しても通る（UI の往復が壊れない）。"""
        for entry in schemas():
            params = {p["name"]: p["default"] for p in entry["params"]}
            strategy = create(entry["name"], params)
            self.assertEqual(strategy.p, params)


class TestParametersChangeBehaviour(unittest.TestCase):
    def test_zero_repeat_value_stops_chasing_known_brands(self):
        """取得済み系列の価値を0にすると、玉数が減って種類数は保たれる。"""
        from visualizer.sim_spectator import run_simulation

        both = run_simulation(
            players=[
                {"strategy": "greedy"},
                {"strategy": "greedy", "params": {"repeat_value": 0}},
            ]
        ).summary()
        scores = {t["teamId"]: t for t in both["ranking"]}
        self.assertGreaterEqual(scores[0]["totalUdon"], scores[1]["totalUdon"])

    def test_supply_follow_off_keeps_the_supply_truck_still(self):
        state, _names = load_match_config(DEFAULT_CONFIG)
        engine.begin_day(state)
        team = state.teams[0]
        plan = create("greedy", {"supply_follow": False})(state, team.id)
        for agent, agent_plan in zip(team.agents, plan):
            if not agent.is_patrol:
                self.assertEqual(agent_plan, [-state.steps_today])

    def test_max_targets_limits_how_many_spots_are_visited(self):
        state, _names = load_match_config(DEFAULT_CONFIG)
        engine.begin_day(state)
        team = state.teams[0]
        one = create("greedy", {"max_targets": 1})(state, team.id)
        many = create("greedy", {"max_targets": 8})(state, team.id)
        moves_one = sum(1 for p in one for v in p if v >= 0)
        moves_many = sum(1 for p in many for v in p if v >= 0)
        self.assertLessEqual(moves_one, moves_many)


if __name__ == "__main__":
    unittest.main()
