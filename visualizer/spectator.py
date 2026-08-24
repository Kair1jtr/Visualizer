"""実行中の procon-server（公式簡易サーバー）を観戦するためのクライアント。

公式APIは「自分のプレイヤー視点」しか提供しないが、`GET /`（試合状態取得API）
のレスポンスには自分の `agents` と他プレイヤー全員の `others[].agents` が
含まれるため、1チーム分のトークンだけで全チームの位置・燃料が分かる。

公式APIは各日開始時点のスナップショットしか返さず、途中経過（何ステップ目に
どこにいるか）は分からない。そこで日をまたいだ2つのスナップショット間を
Dijkstra最短経路で結んだものを「その日の推定軌跡」として見せる
（実際の経路と完全一致するとは限らない近似）。

試合終了後は procon-server がすべてのエンドポイントを 403 で拒否するため、
このクライアントは進行中に観測したスナップショットを内部に保持しておく
必要がある（終了後には取得し直せない）。
"""

import json
import urllib.error
import urllib.request

from .pathfinding import dijkstra, reconstruct_path, terrain_key

TERRAIN_BY_CODE = ["plain", "road", "mountain", "pond"]
ROAD_BY_CODE = ["smooth", "congested", "jammed"]


class SpectatorError(Exception):
    """procon-server との通信エラー（HTTPエラー・接続不可など）。"""


def _get(base_url: str, path: str, token: str) -> dict:
    req = urllib.request.Request(base_url + path, headers={"Procon-Token": token})
    try:
        with urllib.request.urlopen(req, timeout=5) as res:
            return json.loads(res.read())
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        raise SpectatorError(f"GET {path} -> HTTP {exc.code}: {body}")
    except urllib.error.URLError as exc:
        raise SpectatorError(f"procon-server に接続できません: {exc.reason}")


class MatchSpectator:
    """1試合分の観戦状態（設定・日ごとのスナップショット・推定軌跡）を保持する。

    procon-server を1チームのトークンで定期的にポーリングし、日が進むたびに
    スナップショットを記録する。試合終了後にサーバー側の情報が失われても、
    このオブジェクトの中には進行中に取得した分がすべて残る。
    """

    def __init__(self, base_url: str, token: str, team_names: list[str] | None = None):
        self.base_url = base_url
        self.token = token
        self.team_names = team_names or []
        self.setting: dict | None = None
        # day -> {"agentsByTeam": {team_id: [{"kind","pos","fuel"}, ...]}, "traffics": [...]}
        self.snapshots: dict[int, dict] = {}
        self.last_error: str | None = None
        self.ended = False

    def _team_name(self, team_id: int) -> str:
        if team_id < len(self.team_names) and self.team_names[team_id]:
            return self.team_names[team_id]
        return f"チーム{team_id}"

    def poll(self) -> None:
        """procon-server から最新状態を取得し、必要ならスナップショットを追加する。"""
        try:
            if self.setting is None:
                self.setting = _get(self.base_url, "/setting", self.token)
            state = _get(self.base_url, "/", self.token)
        except SpectatorError as exc:
            msg = str(exc)
            self.last_error = msg
            # "試合が設定される前、または試合終了後" を示すエラー（AccessTimeError）。
            # 開始前は "match has not started"、終了後は "match has ended" と
            # メッセージで区別される。終了後だけ ended フラグを立てる
            # （開始前はまだ観戦を続けたいので ended にしない）。
            if "match has ended" in msg:
                self.ended = True
            return
        self.last_error = None

        day = state["day"]
        if day not in self.snapshots:
            agents_by_team = {0: state["agents"]}
            for other in state["others"]:
                agents_by_team[other["id"]] = other["agents"]
            self.snapshots[day] = {
                "day": day,
                "endsAt": state["endsAt"],
                "agentsByTeam": agents_by_team,
                "traffics": state["traffics"],
            }

    # ----- 軌跡（推定） -----

    def _key_of(self, traffics: list[dict]):
        width = self.setting["map"]["width"]
        cells = self.setting["map"]["cells"]
        road = {t["pos"]: ROAD_BY_CODE[t["status"]] for t in traffics}

        def key_of(cell: int):
            r, c = divmod(cell, width)
            return terrain_key(TERRAIN_BY_CODE[cells[r][c]], road.get(cell, "smooth"))

        return key_of

    def trajectories_for_day(self, day: int) -> dict[int, dict] | None:
        """day の推定軌跡を返す。day+1 のスナップショットが無ければ None。

        戻り値: team_id -> [{"agent": i, "path": [cell, ...], "start": pos, "end": pos}]
        経路が求まらない（到達不能）場合は "path" は [start, end] の直線的な代替になる。
        """
        cur = self.snapshots.get(day)
        nxt = self.snapshots.get(day + 1)
        if cur is None or nxt is None:
            return None
        width = self.setting["map"]["width"]
        height = self.setting["map"]["height"]
        key_of = self._key_of(cur["traffics"])

        result: dict[int, list] = {}
        for team_id, start_agents in cur["agentsByTeam"].items():
            end_agents = nxt["agentsByTeam"].get(team_id)
            if end_agents is None:
                continue
            rows = []
            for i, (a0, a1) in enumerate(zip(start_agents, end_agents)):
                start, end = a0["pos"], a1["pos"]
                path = [start, end]
                if start != end:
                    dist, prev = dijkstra(start, width, height, key_of)
                    if end in dist:
                        path = reconstruct_path(prev, start, end)
                rows.append({"agent": i, "kind": a0["kind"], "start": start, "end": end, "path": path})
            result[team_id] = rows
        return result

    # ----- スナップショット出力 -----

    @property
    def phase(self) -> str:
        """"waiting"（種別受付/開始待ち）/ "running" / "ended" のいずれか。"""
        if self.ended:
            return "ended"
        if self.last_error and "not started" in self.last_error:
            return "waiting"
        if self.setting is None:
            return "connecting"
        return "running"

    def summary(self) -> dict:
        days = sorted(self.snapshots.keys())
        current_day = days[-1] if days else None
        team_ids = sorted(self.snapshots[current_day]["agentsByTeam"].keys()) if current_day is not None else []
        return {
            "running": not self.ended,
            "phase": self.phase,
            "connected": self.setting is not None,
            "error": self.last_error,
            "ended": self.ended,
            "setting": self.setting,
            "currentDay": current_day,
            "numDays": len(self.setting["daySteps"]) if self.setting else None,
            "teams": [{"id": tid, "name": self._team_name(tid)} for tid in team_ids],
            "days": [
                {
                    **self.snapshots[d],
                    "trajectories": self.trajectories_for_day(d),
                }
                for d in days
            ],
        }
