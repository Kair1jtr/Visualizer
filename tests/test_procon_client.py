"""`visualizer/procon_client.py`（公式簡易サーバーと対戦するクライアント）の検証。

ここで検証したいのは「通信の配線が正しいか」であって、ゲームルールの正しさ
ではない（ルールは `tests/test_official_supplement.py` 等で既に検証済み）。
そのため擬似サーバーは `simulator.engine` をそのまま使い、クライアント側の
ローカル再現（`_replay_locally`）と独立に「本物のサーバーのふるまい」を
再現する。
"""

from __future__ import annotations

import http.server
import json
import threading
import time
import unittest

from simulator import engine, validation
from simulator.grid import build_grid
from simulator.policies import DEFAULT_POLICIES
from simulator.state import MatchConfig, SpotDef
from simulator.strategy import create
from simulator.terrain import RoadStatus, Terrain
from visualizer.procon_client import ClientError, PlayerClient, default_kinds


class TestDefaultKinds(unittest.TestCase):
    def test_last_agent_is_supply_when_more_than_one(self):
        self.assertEqual(default_kinds(3), [0, 0, 1])

    def test_single_agent_is_patrol(self):
        self.assertEqual(default_kinds(1), [0])


def _line_scenario():
    """〔補足〕と同じ1行×4列（道路・道路・平地・平地）。巡回車1＋補給車1。"""
    grid = build_grid(
        1, 4, [[Terrain.ROAD, Terrain.ROAD, Terrain.PLAIN, Terrain.PLAIN]], DEFAULT_POLICIES
    )
    spots = [SpotDef(pos=2, brand=0, stocks=4)]
    starts = [0, 3]
    config = MatchConfig(
        daySteps=(6, 6),
        daySeconds=(30, 30),
        fuelLimits=3,
        busyThreshold=1,
        jammedThreshold=2,
        players=1,
        policies=DEFAULT_POLICIES,
    )
    state = engine.create_game(grid, config, spots, starts, [[0, 1]])
    state.traffic.traffics = {0: RoadStatus.SMOOTH, 1: RoadStatus.SMOOTH}
    return state, starts


class TestPlayerClientLocalReplay(unittest.TestCase):
    """ネットワークを介さず、ローカル再現ロジックだけを検証する。"""

    def setUp(self):
        self.client = PlayerClient("http://unused", "token", create("stay"))
        self.client.state, self.starts = _line_scenario()

    def test_sync_day_overwrites_position_and_fuel_from_server(self):
        match_state = {
            "day": 1,
            "endsAt": 0,
            "agents": [{"kind": 0, "pos": 2, "fuel": 1}, {"kind": 1, "pos": 2, "fuel": 3}],
            "others": [],
            "traffics": [{"pos": 0, "status": 1}, {"pos": 1, "status": 2}],
        }
        self.client._sync_day(match_state)
        team = self.client.state.teams[0]
        self.assertEqual(team.agents[0].pos, 2)
        self.assertEqual(team.agents[0].fuel, 1)
        self.assertEqual(
            self.client.state.traffic.traffics,
            {0: RoadStatus.CONGESTED, 1: RoadStatus.JAMMED},
        )
        # 日開始時の資産はサーバーの報告に関わらずリフレッシュされる〔要項〕【確定】
        self.assertEqual(team.spot_stocks[2], 4)

    def test_replay_advances_position_and_consumes_fuel(self):
        """`_replay_locally` が移動・燃料消費・日ステップ数を正しく反映する。

        燃料タイミング等のルール自体は `tests/test_official_supplement.py` が
        公式例で検証済み。ここでは配線（`_sync_day` → `_replay_locally` の
        繋ぎ）だけを確認する。
        """
        self.client._sync_day(
            {
                "day": 0,
                "endsAt": 0,
                "agents": [{"kind": 0, "pos": 0, "fuel": 3}, {"kind": 1, "pos": 0, "fuel": 3}],
                "others": [],
                "traffics": [{"pos": 0, "status": 0}, {"pos": 1, "status": 0}],
            }
        )
        # セル0(道路・順調)を1ステップ・燃料2で出て、残り5ステップは待機。
        plan = [[2, -5], [-6]]
        steps = self.client._replay_locally(plan)
        self.assertEqual(len(steps), 7)  # 0〜6ステップ
        positions = [s["agentsByTeam"][0][0]["pos"] for s in steps]
        self.assertEqual(positions, [0, 1, 1, 1, 1, 1, 1])
        fuels = [s["agentsByTeam"][0][0]["fuel"] for s in steps]
        self.assertEqual(fuels, [3, 1, 1, 1, 1, 1, 1])

    def test_trajectory_collapses_repeated_cells(self):
        start_agents = [{"kind": 0, "pos": 0, "fuel": 3}]
        steps = [
            {"agentsByTeam": {0: [{"kind": 0, "pos": p, "fuel": 3}]}} for p in [0, 0, 1, 1, 2]
        ]
        rows = self.client._trajectories(start_agents, steps)
        self.assertEqual(rows[0]["path"], [0, 1, 2])
        self.assertEqual(rows[0]["start"], 0)
        self.assertEqual(rows[0]["end"], 2)

    def test_invalid_plan_falls_back_to_full_wait(self):
        """戦略が不正な計画を返しても、必ず有効な提出（全員待機）に差し替わる。

        無回答・不正な回答はその日を丸ごと失う〔書式〕〔Q55〕【確定】ため、
        提出そのものを失敗させてはならない。
        """
        self.client.strategy = lambda state, team_id: [[9, 9], [9, 9]]  # 不正な方向コード
        self.client._post = lambda path, body: {"revision": 1}
        match_state = {
            "day": 0,
            "endsAt": 0,
            "agents": [{"kind": 0, "pos": 0, "fuel": 3}, {"kind": 1, "pos": 3, "fuel": 3}],
            "others": [],
            "traffics": [{"pos": 0, "status": 0}, {"pos": 1, "status": 0}],
        }
        self.client.play_day(match_state)
        day = self.client.days[-1]
        self.assertIsNotNone(day["rejected"])
        self.assertEqual(day["plan"], [[-6], [-6]])


# ---------------------------------------------------------------------------
# 擬似サーバーを使った通信の配線テスト
# ---------------------------------------------------------------------------


class _FakeApi:
    """`simulator.engine` をそのまま使い、公式APIのふるまいを模す擬似サーバー。

    クライアントの `_replay_locally` とは完全に独立した状態を持つ
    （両者が一致すれば、通信の往復が正しく機能している証拠になる）。
    """

    def __init__(self):
        self.state, self.agent_starts = _line_scenario()
        self.lock = threading.Lock()
        self.submitted_kinds: list[int] | None = None

    def setting(self) -> dict:
        cfg = self.state.config
        map_ = self.state.map
        return {
            "startsAt": 0,
            "daySeconds": list(cfg.daySeconds),
            "daySteps": list(cfg.daySteps),
            "map": {
                "height": map_.height,
                "width": map_.width,
                "cells": [[int(c) for c in row] for row in map_.cells],
            },
            "spots": [{"brand": s.brand, "pos": s.pos, "stocks": s.stocks} for s in self.state.spots],
            "agents": list(self.agent_starts),
            "fuelLimits": cfg.fuelLimits,
            "players": 1,
            "busyThreshold": cfg.busyThreshold,
            "jammedThreshold": cfg.jammedThreshold,
        }

    def match_state(self) -> dict | None:
        with self.lock:
            if self.state.finished:
                return None
            team = self.state.teams[0]
            traffics = [
                {"pos": c, "status": int(self.state.traffic.traffics.get(c, RoadStatus.SMOOTH))}
                for c in self.state.map.road_cells()
            ]
            return {
                "endsAt": 0,
                "day": self.state.day,
                "agents": [{"kind": int(a.kind), "pos": a.pos, "fuel": a.fuel} for a in team.agents],
                "others": [],
                "traffics": traffics,
            }

    def submit_kinds(self, kinds: list[int]) -> None:
        self.submitted_kinds = kinds

    def submit_actions(self, plan) -> int:
        with self.lock:
            team = self.state.teams[0]
            error = validation.validate_team_plan(self.state, team, plan)
            if error is not None:
                return -1
            engine.set_plans(self.state, {0: plan})
            engine.simulate_day_steps(self.state)
            engine.end_day(self.state)
            if not self.state.finished:
                engine.begin_day(self.state)
            return 1


def _make_handler(fake: _FakeApi):
    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _send_json(self, obj, status=200):
            data = json.dumps(obj).encode("utf-8") if obj is not None else b""
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            if data:
                self.wfile.write(data)

        def do_GET(self):
            if self.path == "/setting":
                self._send_json(fake.setting())
                return
            if self.path == "/":
                state = fake.match_state()
                if state is None:
                    self._send_json({"code": "AccessTimeError", "message": "match has ended"}, 403)
                    return
                self._send_json(state)
                return
            self._send_json({"message": "not found"}, 404)

        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length)) if length else None
            if self.path == "/agent":
                fake.submit_kinds(body)
                self._send_json(None)
                return
            if self.path == "/":
                revision = fake.submit_actions(body)
                self._send_json({"revision": revision})
                return
            self._send_json({"message": "not found"}, 404)

    return Handler


class TestPlayerClientAgainstFakeServer(unittest.TestCase):
    """実際にHTTPで通信し、擬似サーバーとの1試合を完走させる。"""

    def _run(self, strategy_name: str) -> tuple[PlayerClient, _FakeApi]:
        fake = _FakeApi()
        httpd = http.server.HTTPServer(("127.0.0.1", 0), _make_handler(fake))
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            base_url = f"http://127.0.0.1:{httpd.server_address[1]}"
            client = PlayerClient(base_url, "token", create(strategy_name), poll_interval=0.02)
            client_thread = threading.Thread(target=client.run_until_ended, daemon=True)
            client_thread.start()
            client_thread.join(timeout=5)
            self.assertFalse(client_thread.is_alive(), "クライアントが時間内に終了しなかった")
        finally:
            httpd.shutdown()
            thread.join(timeout=2)
            httpd.server_close()
        return client, fake

    def test_completes_both_days_with_stay_strategy(self):
        client, fake = self._run("stay")
        self.assertIsNone(client.last_error)
        self.assertTrue(client.ended)
        self.assertEqual(len(client.days), 2)
        self.assertEqual(fake.submitted_kinds, [0, 1])
        for day in client.days:
            self.assertIsNone(day["rejected"])
            # 待機戦略なので位置は動かない
            self.assertTrue(all(len(row["path"]) == 1 for row in day["trajectories"][0]))

    def test_greedy_strategy_reaches_the_spot_and_scores(self):
        client, fake = self._run("greedy")
        self.assertTrue(client.ended)
        self.assertEqual(len(client.days), 2)
        for day in client.days:
            self.assertIsNone(day["rejected"])
        # クライアントのローカル再現と、独立した擬似サーバーの最終状態が一致する
        # （通信の往復とローカル再現の両方が正しいことの相互検証になる）
        local_team = client.state.teams[0]
        fake_team = fake.state.teams[0]
        self.assertEqual(local_team.total_udon, fake_team.total_udon)
        self.assertEqual(local_team.brands_all, fake_team.brands_all)
        self.assertGreater(local_team.total_udon, 0)

    def test_disconnected_server_is_reported_not_raised(self):
        client = PlayerClient("http://127.0.0.1:1", "token", create("stay"), timeout=0.5)
        with self.assertRaises(ClientError):
            client.connect()


if __name__ == "__main__":
    unittest.main()
