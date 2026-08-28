"""`simulator/pathfinding.py` の検証。

辺の重みが **移動元セル** の地形で決まる〔要項〕〔Q10〕〔Q25〕【確定】ことと、
その日の道路状態で経路が変わることを確認する。
"""

import unittest

from simulator.grid import build_grid
from simulator.pathfinding import dijkstra, directions_from, path_cells, route
from simulator.policies import DEFAULT_POLICIES
from simulator.terrain import RoadStatus, Terrain


def line_grid():
    """〔補足〕と同じ 1行×4列（道路・道路・平地・平地）。"""
    return build_grid(
        height=1,
        width=4,
        cells=[[Terrain.ROAD, Terrain.ROAD, Terrain.PLAIN, Terrain.PLAIN]],
        policies=DEFAULT_POLICIES,
    )


class TestDijkstra(unittest.TestCase):
    def test_cost_comes_from_the_source_cell(self):
        """セル0(道路・順調)=1, セル1(道路・混雑)=2, セル2(平地)=2 を積み上げる。"""
        grid = line_grid()
        road = {0: RoadStatus.SMOOTH, 1: RoadStatus.CONGESTED}
        dist, _prev = dijkstra(grid, road, 0)
        self.assertEqual(dist[0], 0)
        self.assertEqual(dist[1], 1)  # セル0 を出るのに1ステップ
        self.assertEqual(dist[2], 3)  # + セル1 を出るのに2ステップ
        self.assertEqual(dist[3], 5)  # + セル2 を出るのに2ステップ

    def test_road_status_changes_distance(self):
        """同じ経路でも、その日の道路状態が悪いとステップ数が増える。"""
        grid = line_grid()
        smooth = dijkstra(grid, {0: RoadStatus.SMOOTH, 1: RoadStatus.SMOOTH}, 0)[0]
        jammed = dijkstra(grid, {0: RoadStatus.JAMMED, 1: RoadStatus.JAMMED}, 0)[0]
        self.assertEqual(smooth[2], 2)  # 1 + 1
        self.assertEqual(jammed[2], 8)  # 4 + 4

    def test_pond_is_unreachable(self):
        """池には進入できない〔要項〕【確定】ので、その先も到達不能になる。"""
        grid = build_grid(
            height=1,
            width=3,
            cells=[[Terrain.PLAIN, Terrain.POND, Terrain.PLAIN]],
            policies=DEFAULT_POLICIES,
        )
        dist, _prev = dijkstra(grid, {}, 0)
        self.assertNotIn(1, dist)
        self.assertNotIn(2, dist)

    def test_route_and_path_cells_round_trip(self):
        grid = line_grid()
        road = {0: RoadStatus.SMOOTH, 1: RoadStatus.SMOOTH}
        directions = route(grid, road, 0, 3)
        self.assertEqual(directions, [2, 2, 2])  # 2 = 右
        self.assertEqual(path_cells(grid, 0, directions), [0, 1, 2, 3])

    def test_route_to_self_is_empty(self):
        grid = line_grid()
        self.assertEqual(route(grid, {}, 2, 2), [])

    def test_missing_road_status_is_treated_as_smooth(self):
        """道路状態が与えられていない道路セルは順調として扱う（1日目と同じ）。"""
        grid = line_grid()
        without = dijkstra(grid, {}, 3)[0]
        smooth = dijkstra(grid, {0: RoadStatus.SMOOTH, 1: RoadStatus.SMOOTH}, 3)[0]
        self.assertEqual(without, smooth)

    def test_unreachable_route_is_none(self):
        grid = build_grid(
            height=1,
            width=3,
            cells=[[Terrain.PLAIN, Terrain.POND, Terrain.PLAIN]],
            policies=DEFAULT_POLICIES,
        )
        self.assertIsNone(route(grid, {}, 0, 2))


class TestHexRouting(unittest.TestCase):
    def test_diagonal_directions_on_even_r_grid(self):
        """六角格子（偶数行が右にずれる〔Q1〕【確定】）で斜めの経路が復元できる。"""
        grid = build_grid(
            height=3,
            width=3,
            cells=[[0, 0, 0], [0, 0, 0], [0, 0, 0]],
            policies=DEFAULT_POLICIES,
        )
        _dist, prev = dijkstra(grid, {}, 0)
        for goal in range(9):
            directions = directions_from(prev, 0, goal)
            self.assertIsNotNone(directions, f"セル {goal} へ到達できない")
            # 復元した方向列を実際に歩くと目的地に着くこと
            self.assertEqual(path_cells(grid, 0, directions)[-1], goal)


if __name__ == "__main__":
    unittest.main()
