"""公式ルール忠実シミュレーター（`simulator/`）の試合を、観戦ビューに流し込む層。

`visualizer/spectator.py`（公式簡易サーバーの観戦）と **同じ形の JSON** を返す。
そのためブラウザ側は同じ描画コードで両方を表示できる。

両者の違いは軌跡の確からしさだけである。

    spectator.py     公式APIは各日開始時のスナップショットしか返さないため、
                     日をまたぐ2点間を Dijkstra で結んだ **推定** 軌跡を出す。
    sim_spectator.py シミュレーターは全ステップの状態を持っているので、
                     **実測** の軌跡とステップ単位の再生データを出せる。

この層はルールを一切実装しない。状態遷移はすべて `simulator.engine` に任せ、
ここは記録と JSON 化だけを行う（`docs/状態設計書.md` 第18.3節の層分け）。
"""

from __future__ import annotations

import json
from pathlib import Path

from simulator import engine, validation
from simulator.actions import TeamPlan
from simulator.grid import build_grid
from simulator.policies import DEFAULT_POLICIES, Policies
from simulator.state import HexaUdon, MatchConfig, SpotDef
from simulator.strategy import (
    DEFAULT_STRATEGY,
    STRATEGY_CLASSES,
    Strategy,
    StrategyError,
    create,
    schemas,
)
from simulator.terrain import AgentKind

BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = BASE_DIR / "server" / "試合設定用JSONファイル" / "example.json"


class SimSpectatorError(Exception):
    """試合設定の読み込みや戦略指定の誤り。"""


# ---------------------------------------------------------------------------
# 試合設定JSON（procon-server と同じ形式）→ HexaUdon
# ---------------------------------------------------------------------------


def load_match_config(
    path: Path,
    *,
    kinds_by_team: list[list[int]] | None = None,
    policies: Policies = DEFAULT_POLICIES,
) -> tuple[HexaUdon, list[str]]:
    """`server/試合設定用JSONファイル/` と同じ形式の設定JSONから初期状態を作る。

    公式簡易サーバーに渡すのと同じファイルをそのまま使えるので、
    「簡易サーバーで動かした試合」と「シミュレーターで動かした試合」を
    同じ設定で見比べられる。
    """
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except OSError as exc:
        raise SimSpectatorError(f"試合設定を読み込めません: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise SimSpectatorError(f"試合設定JSONが不正です: {exc}") from exc

    try:
        problem = raw["problem"]
        teams_raw = raw["teams"]
        grid = build_grid(problem["height"], problem["width"], problem["cells"], policies)
        spots = [
            SpotDef(pos=s["pos"], brand=s["brand"], stocks=s["stocks"])
            for s in problem["spots"]
        ]
        starts = list(problem["agentStarts"])
        config = MatchConfig(
            daySteps=tuple(problem["daySteps"]),
            daySeconds=tuple(problem["daySeconds"]),
            fuelLimits=problem["fuelLimits"],
            busyThreshold=problem["busyThreshold"],
            jammedThreshold=problem["jammedThreshold"],
            players=len(teams_raw),
            policies=policies,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise SimSpectatorError(f"試合設定の内容が不正です: {exc}") from exc

    names = [
        t.get("name") or f"Player {i}" for i, t in enumerate(teams_raw)
    ]
    if kinds_by_team is None:
        kinds_by_team = [default_kinds(len(starts)) for _ in teams_raw]
    state = engine.create_game(grid, config, spots, starts, kinds_by_team)
    return state, names


def default_kinds(num_agents: int) -> list[int]:
    """既定のエージェント種別。最後の1体だけ補給車にする。

    公式は種別未提出なら全員巡回車とする〔書式〕〔Q53〕【確定】が、
    それでは補給が一度も起きず補給ルールを目視できないため、
    観戦用の既定としては補給車を1体入れる。エージェントが1体の場合は全員巡回車。
    """
    if num_agents <= 1:
        return [int(AgentKind.PATROL)] * num_agents
    return [int(AgentKind.PATROL)] * (num_agents - 1) + [int(AgentKind.SUPPLY)]


# ---------------------------------------------------------------------------
# 戦略のプレイヤーごとの割り当て
# ---------------------------------------------------------------------------


def parse_strategies(spec: str, num_teams: int) -> list[str]:
    """戦略の指定文字列を、プレイヤー番号順のリストに展開する。

    受け付ける書き方:
        "greedy"              全プレイヤーに greedy
        "greedy,stay"         P0=greedy, P1=stay（プレイヤー数と同数を並べる）
        "1:stay"              P1 だけ stay、残りは既定
        "greedy,2:stay,3:brand"  既定を greedy にしつつ P2/P3 だけ差し替え

    位置指定（`番号:戦略`）と並べ書きは混ぜられる。並べ書きだけの場合、
    要素数はプレイヤー数と一致していなければならない。
    """
    tokens = [s.strip() for s in spec.split(",") if s.strip()]
    if not tokens:
        raise SimSpectatorError("戦略が指定されていません")

    positional: list[str] = []
    explicit: dict[int, str] = {}
    for token in tokens:
        if ":" in token:
            head, _, name = token.partition(":")
            try:
                index = int(head)
            except ValueError:
                raise SimSpectatorError(
                    f"プレイヤー番号が整数ではありません: {token!r}"
                ) from None
            if not 0 <= index < num_teams:
                raise SimSpectatorError(
                    f"プレイヤー番号が範囲外です: {index}（0〜{num_teams - 1}）"
                )
            explicit[index] = _check_strategy(name.strip())
        else:
            positional.append(_check_strategy(token))

    if not positional:
        base = [DEFAULT_STRATEGY] * num_teams
    elif len(positional) == 1:
        base = positional * num_teams
    elif len(positional) == num_teams:
        base = list(positional)
    else:
        raise SimSpectatorError(
            f"戦略の指定数がプレイヤー数と一致しません: {len(positional)} != {num_teams}"
        )

    for index, name in explicit.items():
        base[index] = name
    return base


def _check_strategy(name: str) -> str:
    if name not in STRATEGY_CLASSES:
        raise SimSpectatorError(
            f"未知の戦略です: {name}（選べるのは {sorted(STRATEGY_CLASSES)}）"
        )
    return name


def build_assignments(
    num_teams: int,
    *,
    spec: str | None = None,
    players: list[dict] | None = None,
) -> list[Strategy]:
    """プレイヤー番号順の戦略インスタンス列を作る。

    `players` はプレイヤーごとの `{"strategy": 名前, "params": {...}}` の配列で、
    パラメータまで指定できる（ブラウザの設定ダイアログはこちらを送る）。
    `spec` は文字列だけの簡易指定（CLI・クエリパラメータ用）。
    両方省略した場合は全プレイヤーが既定の戦略・既定パラメータになる。
    """
    if players is not None:
        if len(players) != num_teams:
            raise SimSpectatorError(
                f"プレイヤー設定の数が一致しません: {len(players)} != {num_teams}"
            )
        out = []
        for index, entry in enumerate(players):
            if not isinstance(entry, dict):
                raise SimSpectatorError(
                    f"プレイヤー{index} の設定はオブジェクトである必要があります"
                )
            name = _check_strategy(str(entry.get("strategy", DEFAULT_STRATEGY)))
            params = entry.get("params") or {}
            if not isinstance(params, dict):
                raise SimSpectatorError(
                    f"プレイヤー{index} の params はオブジェクトである必要があります"
                )
            try:
                out.append(create(name, params))
            except StrategyError as exc:
                raise SimSpectatorError(f"プレイヤー{index}: {exc}") from exc
        return out

    names = parse_strategies(spec or DEFAULT_STRATEGY, num_teams)
    return [create(name) for name in names]


def available_strategies() -> list[dict]:
    """選べる戦略とパラメータの一覧（UI がフォームを組み立てるのに使う）。"""
    return schemas()


# ---------------------------------------------------------------------------
# 観戦データの生成
# ---------------------------------------------------------------------------


def _agents_payload(state: HexaUdon) -> dict[int, list[dict]]:
    """`spectator.py` のスナップショットと同じ形（チームID → エージェント配列）。"""
    return {
        team.id: [
            {"kind": int(a.kind), "pos": a.pos, "fuel": a.fuel} for a in team.agents
        ]
        for team in state.teams
    }


def _traffics_payload(state: HexaUdon) -> list[dict]:
    """公式の `traffics` と同じ形（道路セルとその状態）。〔書式〕【確定】"""
    return [
        {"pos": cell, "status": int(status)}
        for cell, status in sorted(state.traffic.traffics.items())
    ]


def _dedup(cells: list[int]) -> list[int]:
    """連続する同じセルを畳んで、通過セル列にする。"""
    out: list[int] = []
    for c in cells:
        if not out or out[-1] != c:
            out.append(c)
    return out


class SimSpectator:
    """シミュレーターの1試合を最後まで走らせ、観戦用の記録を残す。

    公式簡易サーバーの観戦と違い、試合は一瞬で終わる（実時間の締切が無い）ため、
    `run()` で全日程を回してから `summary()` を読む使い方になる。
    """

    def __init__(
        self,
        state: HexaUdon,
        *,
        team_names: list[str] | None = None,
        strategy: str | None = None,
        players: list[dict] | None = None,
        run_key: int = 0,
    ):
        setups = build_assignments(len(state.teams), spec=strategy, players=players)
        self.state = state
        self.setups = setups
        self.strategy_names = [s.name for s in setups]
        self.strategy_name = ",".join(self.strategy_names)
        self.strategies = {
            team.id: setups[i] for i, team in enumerate(state.teams)
        }
        self.team_names = team_names or [f"Player {t.id}" for t in state.teams]
        self.run_key = run_key
        self.agent_starts = [a.pos for a in state.teams[0].agents] if state.teams else []
        self.kinds = [int(a.kind) for a in state.teams[0].agents] if state.teams else []
        self.days: list[dict] = []
        self.error: str | None = None

    # ----- 実行 -----

    def run(self) -> None:
        """全日程を進める。各日・各ステップの状態を記録する。"""
        while not self.state.finished:
            self._run_one_day()

    def _run_one_day(self) -> None:
        state = self.state
        engine.begin_day(state)
        day = state.day
        num_steps = state.steps_today

        traffics_now = dict(state.traffic.traffics)
        volumes = {c: engine.traffic_volume(state, c) for c in traffics_now}
        start_agents = _agents_payload(state)
        traffics = _traffics_payload(state)

        # 各チームの回答を作り、公式と同じ手順で検証する〔Q6〕【確定】
        plans: dict[int, TeamPlan] = {}
        for team in state.teams:
            plans[team.id] = self.strategies[team.id](state, team.id)
        errors = validation.validate_all(state, plans)

        # 1体でも不正ならそのチームは全員最終ステップまで待機〔書式〕〔Q55〕【確定】
        fallback = engine.all_wait_plans(state)
        effective = {
            tid: (fallback[tid] if errors.get(tid) else plans[tid]) for tid in plans
        }
        engine.set_plans(state, effective)

        # ステップ単位の記録。0ステップ目はアクションのみ、最終ステップは反映のみ
        # 〔Q6〕〔補足〕【確定】という境界は engine 側の定義をそのまま使う。
        # 検証を通った計画（または全員待機のフォールバック）しか渡していないので、
        # engine が不正を検出することはない。もし出たら実装の食い違いなので
        # 握りつぶさず、そのまま例外を上げる。
        steps: list[dict] = []
        for step in range(num_steps + 1):
            state.step = step
            if step > 0:
                engine.reflection_phase(state)
            if step < num_steps:
                engine.action_phase(state)
            steps.append({"step": step, "agentsByTeam": _agents_payload(state)})

        stay = dict(state.traffic.stay_today)
        engine.end_day(state)

        self.days.append(
            {
                "day": day,
                "numSteps": num_steps,
                "agentsByTeam": start_agents,
                "traffics": traffics,
                "volumes": {str(c): v for c, v in sorted(volumes.items())},
                "stay": {str(c): v for c, v in sorted(stay.items())},
                "steps": steps,
                "trajectories": self._trajectories(start_agents, steps),
                "plans": {
                    str(tid): [list(p) for p in effective[tid]] for tid in sorted(effective)
                },
                "rejected": {
                    str(tid): (str(err) if err else None) for tid, err in sorted(errors.items())
                },
                "scores": self._scores(),
            }
        )

    def _trajectories(
        self, start_agents: dict[int, list[dict]], steps: list[dict]
    ) -> dict[int, list[dict]]:
        """実測の軌跡。ステップごとの位置をそのまま畳んだもの（推定ではない）。"""
        out: dict[int, list[dict]] = {}
        for team_id, agents in start_agents.items():
            rows = []
            for index in range(len(agents)):
                cells = [s["agentsByTeam"][team_id][index]["pos"] for s in steps]
                path = _dedup(cells)
                rows.append(
                    {
                        "agent": index,
                        "kind": agents[index]["kind"],
                        "start": path[0],
                        "end": path[-1],
                        "path": path,
                    }
                )
            out[team_id] = rows
        return out

    def _scores(self) -> list[dict]:
        return [
            {
                "teamId": t.id,
                "brandCount": t.brand_count,
                "dailyCumulative": t.daily_brand_cumulative,
                "totalUdon": t.total_udon,
                "dailyBrandCounts": list(t.daily_brand_counts),
            }
            for t in self.state.teams
        ]

    # ----- 出力 -----

    def setting(self) -> dict:
        """公式の「試合開始前のマップ構成フォーマット」と同じ形。〔書式〕【確定】"""
        cfg = self.state.config
        map_ = self.state.map
        return {
            # 実時間の進行はシミュレーターの対象外なので 0 を入れる。
            # 盤面を作り直すべきかの判定にはこの下の "key" を使う。
            "startsAt": 0,
            "key": f"sim{self.run_key}",
            "daySeconds": list(cfg.daySeconds),
            "daySteps": list(cfg.daySteps),
            "map": {
                "height": map_.height,
                "width": map_.width,
                "cells": [[int(t) for t in row] for row in map_.cells],
            },
            "spots": [
                {"brand": s.brand, "pos": s.pos, "stocks": s.stocks} for s in self.state.spots
            ],
            "agents": list(self.agent_starts),
            "kinds": list(self.kinds),
            "fuelLimits": cfg.fuelLimits,
            "players": cfg.players,
            "busyThreshold": cfg.busyThreshold,
            "jammedThreshold": cfg.jammedThreshold,
        }

    def summary(self) -> dict:
        """`spectator.MatchSpectator.summary()` と同じ形（＋シミュレーター固有の追加分）。"""
        ranking = self.state.ranking()
        return {
            "running": not self.state.finished,
            "phase": "ended" if self.state.finished else "running",
            "connected": True,
            "started": True,
            "processAlive": False,
            "error": self.error,
            "ended": self.state.finished,
            "setting": self.setting(),
            "currentDay": self.days[-1]["day"] if self.days else None,
            "numDays": self.state.config.num_days,
            "teams": [
                {
                    "id": t.id,
                    "name": self._name(t.id),
                    "strategy": self.strategy_names[i],
                    "params": dict(self.setups[i].p),
                }
                for i, t in enumerate(self.state.teams)
            ],
            "days": self.days,
            # --- ここから下はシミュレーター側にしかない情報 ---
            "exact": True,  # 軌跡が推定ではなく実測であることを示す
            "strategy": self.strategy_name,
            "policies": self.state.config.policies.describe(),
            "ranking": [
                {
                    "teamId": t.id,
                    "name": self._name(t.id),
                    "brandCount": t.brand_count,
                    "dailyCumulative": t.daily_brand_cumulative,
                    "totalUdon": t.total_udon,
                }
                for t in ranking
            ],
        }

    def _name(self, team_id: int) -> str:
        if 0 <= team_id < len(self.team_names):
            return self.team_names[team_id]
        return f"Player {team_id}"


def run_simulation(
    config_path: Path = DEFAULT_CONFIG,
    *,
    strategy: str | None = None,
    players: list[dict] | None = None,
    run_key: int = 0,
    policies: Policies = DEFAULT_POLICIES,
) -> SimSpectator:
    """設定JSONを読み、全日程を走らせた `SimSpectator` を返す。

    戦略はプレイヤーごとに指定できる。`players` はパラメータまで含む配列、
    `strategy` は名前だけの簡易指定。
    """
    state, names = load_match_config(config_path, policies=policies)
    spectator = SimSpectator(
        state,
        team_names=names,
        strategy=strategy,
        players=players,
        run_key=run_key,
    )
    spectator.run()
    return spectator
