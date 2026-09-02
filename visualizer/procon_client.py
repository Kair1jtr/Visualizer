"""公式配布の簡易サーバー（procon-server）と**実際に対戦する**クライアント。

`visualizer/spectator.py` が観戦専用（`GET /setting` `GET /` しか叩かない）
なのに対し、こちらは `POST /agent` `POST /` で実際に行動計画を提出する。

ルール判定は一切持たない。行動計画は `simulator.strategy` の戦略へ委任し、
提出前に必ず `simulator.validation.validate_team_plan()` で検証する
（1体でも不正なら回答全体がリジェクトされる〔書式〕【確定】ため、
検証を通らない場合は全員待機にフォールバックし、無回答＝その日を丸ごと
失う〔書式〕〔Q55〕【確定】事態を避ける）。

公式APIは自チームの位置・燃料は毎日教えてくれる（`GET /` の `agents[]`）が、
スポット在庫・獲得系列・玉数は一切教えてくれない。ただし在庫・獲得は
チームごとに独立している〔要項〕【確定】ため、**自チームの行動だけから
正確に再現できる**。そこでここでは、提出した計画を手元の `simulator.engine`
でも実行し（`_replay_locally`）、位置・燃料は毎日サーバーの報告値で
上書きして補正しつつ、スコアと軌跡はこの手元シミュレーションから記録する。
つまり記録される軌跡・得点は推定ではなく、選んだ戦略で実際に得られる値そのもの。
"""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request

from simulator import engine, validation
from simulator.actions import PlanError, TeamPlan
from simulator.grid import build_grid
from simulator.policies import DEFAULT_POLICIES, Policies
from simulator.state import HexaUdon, MatchConfig, SpotDef
from simulator.strategy import Strategy
from simulator.terrain import AgentKind, RoadStatus


class ClientError(Exception):
    """procon-server との通信エラー（HTTPエラー・接続不可など）。"""


def _request(base_url: str, method: str, path: str, token: str, body=None, timeout: float = 5.0):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Procon-Token": token}
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(base_url + path, method=method, data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            raw = res.read()
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        body_text = exc.read().decode("utf-8", "replace")
        raise ClientError(f"{method} {path} -> HTTP {exc.code}: {body_text}")
    except urllib.error.URLError as exc:
        raise ClientError(f"procon-server に接続できません: {exc.reason}")


def default_kinds(num_agents: int) -> list[int]:
    """既定のエージェント種別。最後の1体だけ補給車にする。

    公式は種別未提出なら全員巡回車とする〔書式〕〔Q53〕【確定】が、
    それでは補給が一度も起きず補給ルールの効果を確認できないため、
    既定としては補給車を1体入れる。エージェントが1体の場合は全員巡回車。
    """
    if num_agents <= 1:
        return [int(AgentKind.PATROL)] * num_agents
    return [int(AgentKind.PATROL)] * (num_agents - 1) + [int(AgentKind.SUPPLY)]


class PlayerClient:
    """procon-server の1チームぶんを、公式APIで実際に操作するクライアント。

    `run_until_ended()` で日ごとのポーリング・提出ループを回す。
    別スレッドで動かす想定（`app.py` から `threading.Thread` で起動する）。
    """

    def __init__(
        self,
        base_url: str,
        token: str,
        strategy: Strategy,
        *,
        name: str | None = None,
        policies: Policies = DEFAULT_POLICIES,
        poll_interval: float = 1.0,
        timeout: float = 5.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.strategy = strategy
        self.name = name or "Player"
        self.policies = policies
        self.poll_interval = poll_interval
        self.timeout = timeout

        # 自チームのみを保持する単一チームのローカル状態。
        # 他チームの情報は追わない（在庫・獲得は独立しているため不要）。
        self.state: HexaUdon | None = None
        self.kinds: list[int] = []
        self.connected = False
        self.kind_submitted = False
        self.days: list[dict] = []
        self.last_error: str | None = None
        self.ended = False
        self._stop = threading.Event()
        self._processed_days: set[int] = set()

    # ----- 通信 -----

    def _get(self, path: str):
        return _request(self.base_url, "GET", path, self.token, timeout=self.timeout)

    def _post(self, path: str, body):
        return _request(self.base_url, "POST", path, self.token, body=body, timeout=self.timeout)

    # ----- 初期化 -----

    def connect(self) -> dict:
        """`GET /setting` を読み、ローカルの単一チーム状態を組み立てる。"""
        setting = self._get("/setting")
        grid = build_grid(
            setting["map"]["height"], setting["map"]["width"], setting["map"]["cells"], self.policies
        )
        spots = [SpotDef(pos=s["pos"], brand=s["brand"], stocks=s["stocks"]) for s in setting["spots"]]
        starts = list(setting["agents"])
        config = MatchConfig(
            daySteps=tuple(setting["daySteps"]),
            daySeconds=tuple(setting["daySeconds"]),
            fuelLimits=setting["fuelLimits"],
            busyThreshold=setting["busyThreshold"],
            jammedThreshold=setting["jammedThreshold"],
            players=1,
            policies=self.policies,
        )
        self.kinds = default_kinds(len(starts))
        self.state = engine.create_game(grid, config, spots, starts, [self.kinds])
        self.connected = True
        return setting

    def submit_kind(self) -> None:
        self._post("/agent", self.kinds)
        self.kind_submitted = True

    # ----- 1日分の処理 -----

    def _sync_day(self, match_state: dict) -> None:
        """サーバーが報告した日開始時点の状態でローカル状態を補正する。

        位置・燃料はサーバーの報告値が真実（毎日上書きして補正する）。
        道路状態も交通量から自前で計算せず、サーバーの報告をそのまま使う
        （交通量は全チーム分の合算が必要で、自チームの情報だけでは
        計算できないため）〔要項〕【確定】。
        """
        state = self.state
        team = state.teams[0]
        state.day = match_state["day"]
        state.step = 0
        state.finished = False
        state.traffic.traffics = {
            t["pos"]: RoadStatus(t["status"]) for t in match_state["traffics"]
        }
        team.spot_stocks = {s.pos: s.stocks for s in state.spots}
        team.brands_today = set()
        for agent, reported in zip(team.agents, match_state["agents"]):
            agent.pos = reported["pos"]
            agent.fuel = reported["fuel"]
            agent.acquired_spots_today = set()
            agent.reserved = None
            agent.plan = ()
            agent.plan_cursor = 0

    def _replay_locally(self, plan: TeamPlan) -> list[dict]:
        """提出する計画を手元でも実行し、位置・燃料・スコア・軌跡を記録する。

        公式APIは自チームの在庫・獲得系列・玉数を一切教えないため、
        これが唯一の記録手段になる。
        """
        state = self.state
        team = state.teams[0]
        n = state.steps_today
        engine.set_plans(state, {0: plan})
        steps: list[dict] = []
        for step in range(n + 1):
            state.step = step
            if step > 0:
                engine.reflection_phase(state)
            if step < n:
                engine.action_phase(state)
            steps.append(
                {
                    "step": step,
                    "agentsByTeam": {
                        0: [{"kind": int(a.kind), "pos": a.pos, "fuel": a.fuel} for a in team.agents]
                    },
                }
            )
        return steps

    def _trajectories(self, start_agents: list[dict], steps: list[dict]) -> list[dict]:
        rows = []
        for i in range(len(start_agents)):
            cells = [s["agentsByTeam"][0][i]["pos"] for s in steps]
            path: list[int] = []
            for c in cells:
                if not path or path[-1] != c:
                    path.append(c)
            rows.append(
                {"agent": i, "kind": start_agents[i]["kind"], "start": path[0], "end": path[-1], "path": path}
            )
        return rows

    def play_day(self, match_state: dict) -> None:
        """1日ぶんの行動計画を組み立てて提出し、結果を記録する。"""
        team = self.state.teams[0]
        start_agents = [{"kind": int(a.kind), "pos": a.pos, "fuel": a.fuel} for a in team.agents]
        traffics = list(match_state["traffics"])

        self._sync_day(match_state)
        n = self.state.steps_today

        day_error: str | None = None
        try:
            plan = self.strategy(self.state, 0)
        except Exception as exc:  # 戦略側の不具合でも必ず何か提出する
            day_error = f"戦略が計画を作れませんでした: {exc}"
            plan = [[-n] for _ in team.agents]
        else:
            error = validation.validate_team_plan(self.state, team, plan)
            if error:
                day_error = str(error)
                plan = [[-n] for _ in team.agents]

        try:
            response = self._post("/", plan)
        except ClientError as exc:
            self.last_error = str(exc)
            response = None
        else:
            self.last_error = day_error
        if response and response.get("revision", 0) < 0:
            self.last_error = f"提出が受理されませんでした（revision={response.get('revision')}）"

        steps = self._replay_locally(plan)
        engine.end_day(self.state)

        self.days.append(
            {
                "day": match_state["day"],
                "numSteps": n,
                "agentsByTeam": {0: start_agents},
                "traffics": traffics,
                "steps": steps,
                "plan": plan,
                "rejected": day_error,
                "trajectories": {0: self._trajectories(start_agents, steps)},
            }
        )

    # ----- 実行ループ -----

    def stop(self) -> None:
        self._stop.set()

    def run_until_ended(self) -> None:
        """接続 → 種別提出 → 日ごとのポーリング・提出、を試合終了まで繰り返す。"""
        try:
            self.connect()
            self.submit_kind()
        except ClientError as exc:
            self.last_error = str(exc)
            return
        while not self._stop.is_set():
            try:
                match_state = self._get("/")
            except ClientError as exc:
                msg = str(exc)
                if "match has ended" in msg:
                    # 試合の正常終了。エラーではないので last_error は立てない。
                    self.ended = True
                    self.last_error = None
                    return
                self.last_error = msg
                time.sleep(self.poll_interval)
                continue
            day = match_state["day"]
            if day not in self._processed_days:
                self._processed_days.add(day)
                try:
                    self.play_day(match_state)
                except (ClientError, PlanError) as exc:
                    self.last_error = str(exc)
            time.sleep(self.poll_interval)

    # ----- 出力 -----

    def settings(self) -> dict:
        """この戦略の設定内容（プレイヤー一覧表示に使う）。"""
        return {"strategy": self.strategy.name, "params": dict(self.strategy.p)}


def spawn(
    base_url: str,
    token: str,
    strategy: Strategy,
    *,
    name: str | None = None,
    policies: Policies = DEFAULT_POLICIES,
    poll_interval: float = 1.0,
    timeout: float = 5.0,
) -> tuple[PlayerClient, threading.Thread]:
    """`PlayerClient` を作り、バックグラウンドスレッドで走らせる。"""
    client = PlayerClient(
        base_url,
        token,
        strategy,
        name=name,
        policies=policies,
        poll_interval=poll_interval,
        timeout=timeout,
    )
    thread = threading.Thread(target=client.run_until_ended, daemon=True)
    thread.start()
    return client, thread
