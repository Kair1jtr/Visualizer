"""戦略実験基盤。`compare.py`（1回の実行エンジン）の1段上で、

    条件（マップ・構成・戦略の組み合わせ）を並べる
    → それぞれを実行する
    → 結果をCSVで書き出せる形に均す

を担う。**ルールや実行そのものは一切ここに書かない**。状態遷移は
`simulator.engine`、1回の実行は`compare.run_with_strategies()`にそのまま委ねる。

設計方針（`docs/report/戦略実験基盤の実装計画`に対応）:
    - `compare.py` / `scenarios.py` は変更しない。ここは薄いアダプタに徹する。
    - 「マップをどう作るか」は強制しない。`ExperimentCondition.state_factory`に
      閉じ込め、`scenarios.minimal_scenario()`への薄いラップでも、将来の
      `mapgen.generate_map()`を使ったものでも、呼び出し側が自由に選べる。
    - 「どの軸を振るか」（マップサイズ×構成×戦略×試行回数、等）もここでは
      強制しない。`ExperimentCondition`のリストを組み立てるのは呼び出し側の
      責務（`itertools.product`等）とし、`run_matrix()`はただ列を実行するだけ。
"""

from __future__ import annotations

import csv
import statistics
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import compare
from .state import HexaUdon
from .strategy import Strategy, create as create_strategy

# ---------------------------------------------------------------------------
# 条件と結果
# ---------------------------------------------------------------------------



# `strategies` の値として渡せるもの:
#   - str                       simulator.strategy.STRATEGY_CLASSES に登録された名前
#   - Strategy / 単なる呼び出し可能  そのまま使う（compare.fixed_plans() 等）
#
# 後者を許すのは、登録済みの汎用戦略では表現できない「特定の日にちょうどこの
# 行動計画を送る」という検証専用の戦略（例: 公式資料の状態遷移例の再現）も、
# experiment.py の同じ配線（実行→CSV化）に載せられるようにするため。


@dataclass(frozen=True)
class ExperimentCondition:
    """1回の試行を一意に決める条件。

    `metadata`は結果に付けて回したいだけの付随情報（マップサイズ・エージェント
    構成・乱数シード等）。実行そのものには使わず、`daily_rows()`/`summary_rows()`
    がCSVの列としてそのまま展開する。

    `watch_cells`を指定すると、その道路セルの状態・交通量を`daily_rows()`の列
    に含める（交通量の時系列を追う実験向け。指示書 #7）。
    """

    label: str
    state_factory: Callable[[], HexaUdon]
    strategies: dict[int, Any]  # team_id -> 戦略名(str) または Strategy そのもの
    strategy_params: dict[int, dict[str, Any]] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    watch_cells: tuple[int, ...] = ()

    def build_strategies(self) -> dict[int, Strategy]:
        """`strategies`/`strategy_params`から、実行可能な戦略インスタンスを作る。

        値が文字列なら`simulator.strategy.create()`で名前解決し、そうでなければ
        （既にできあがった`Strategy`インスタンスや`compare.fixed_plans()`の
        戻り値のような呼び出し可能そのものであれば）そのまま使う。
        """
        built: dict[int, Strategy] = {}
        for team_id, value in self.strategies.items():
            if isinstance(value, str):
                built[team_id] = create_strategy(value, self.strategy_params.get(team_id))
            else:
                built[team_id] = value
        return built

    def strategy_label(self, team_id: int) -> str:
        """`daily_rows()`/`summary_rows()`に出す、その戦略の表示名。"""
        value = self.strategies.get(team_id, "")
        if isinstance(value, str):
            return value
        return getattr(value, "name", None) or getattr(value, "__name__", "custom")


@dataclass
class TrialResult:
    """`ExperimentCondition`1件・1回分の結果。"""

    condition: ExperimentCondition
    run_result: compare.RunResult
    elapsed_ms: float


# ---------------------------------------------------------------------------
# 実行
# ---------------------------------------------------------------------------


def run_condition(condition: ExperimentCondition, *, trace: bool = False) -> TrialResult:
    """1条件を1回実行する。"""
    strategies = condition.build_strategies()
    state = condition.state_factory()
    start = time.perf_counter()
    run_result = compare.run_with_strategies(
        state, strategies, label=condition.label, trace=trace
    )
    elapsed_ms = (time.perf_counter() - start) * 1000
    return TrialResult(condition=condition, run_result=run_result, elapsed_ms=elapsed_ms)


def run_matrix(
    conditions: Iterable[ExperimentCondition], *, trace: bool = False
) -> list[TrialResult]:
    """複数条件を順に実行する。

    「マップサイズ×構成×戦略」のような直積は、呼び出し側が
    `itertools.product`等で`ExperimentCondition`のリストを組み立ててから渡す
    （ここでは軸の意味を決めつけない）。
    """
    return [run_condition(c, trace=trace) for c in conditions]


def run_repeated(
    build_condition: Callable[[int], ExperimentCondition], trials: int, *, trace: bool = False
) -> list[TrialResult]:
    """同じ狙いの条件を、試行番号だけ変えて`trials`回実行する。

    「戦略・構成は固定し、マップだけ乱数で変えて何本も走らせ、結果のばらつき
    （平均・標準偏差・勝率）を見る」という統計的評価（指示書#8）向け。
    マップをどう変えるかは`build_condition`に委ねる
    （例: `lambda i: ExperimentCondition(..., state_factory=mapgen.square_scenario_factory_random(16, 3, 1, seed=i))`）。
    """
    return run_matrix((build_condition(i) for i in range(trials)), trace=trace)


# ---------------------------------------------------------------------------
# CSV化
# ---------------------------------------------------------------------------


def _traffic_columns(condition: ExperimentCondition, day: compare.DayRecord) -> dict[str, Any]:
    cols: dict[str, Any] = {}
    for cell in condition.watch_cells:
        status = day.traffics.get(cell)
        cols[f"traffic_status_{cell}"] = int(status) if status is not None else ""
        cols[f"traffic_volume_{cell}"] = day.volume_used.get(cell, "")
    return cols


def _metadata_columns(condition: ExperimentCondition) -> dict[str, Any]:
    return {f"meta_{k}": v for k, v in condition.metadata.items()}


def daily_rows(results: Iterable[TrialResult]) -> list[dict[str, Any]]:
    """1行 = 1試行 × 1日 × 1チーム。日ごとの推移を追う実験（#5〜#7）向け。"""
    rows: list[dict[str, Any]] = []
    for trial in results:
        cond = trial.condition
        for day in trial.run_result.days:
            traffic_cols = _traffic_columns(cond, day)
            for score in day.scores:
                rows.append(
                    {
                        "condition_label": cond.label,
                        "day": day.day,
                        "team_id": score.id,
                        "strategy": cond.strategy_label(score.id),
                        "brand_count": score.brand_count,
                        "daily_cumulative": score.daily_cumulative,
                        "total_udon": score.total_udon,
                        "rejected": score.rejected or "",
                        **traffic_cols,
                        **_metadata_columns(cond),
                    }
                )
    return rows


def summary_rows(results: Iterable[TrialResult]) -> list[dict[str, Any]]:
    """1行 = 1試行 × 1チームの最終結果。統計集計（#8）の入力に使う。"""
    rows: list[dict[str, Any]] = []
    for trial in results:
        cond = trial.condition
        winner_id = trial.run_result.winner()
        for score in trial.run_result.final_scores():
            rows.append(
                {
                    "condition_label": cond.label,
                    "team_id": score.id,
                    "strategy": cond.strategy_label(score.id),
                    "brand_count": score.brand_count,
                    "daily_cumulative": score.daily_cumulative,
                    "total_udon": score.total_udon,
                    "rejected": score.rejected or "",
                    "is_winner": score.id == winner_id,
                    "elapsed_ms": trial.elapsed_ms,
                    **_metadata_columns(cond),
                }
            )
    return rows


def aggregate_by(
    rows: Iterable[dict[str, Any]], key: Callable[[dict[str, Any]], str]
) -> list[dict[str, Any]]:
    """`summary_rows()`の出力を`key(row)`でグループ化し、平均・標準偏差・勝率を出す。

    `run_repeated()`と組み合わせて使う想定（指示書#8: 統計的な複数マップ評価）。
    1グループ = 通常は「同じ戦略」で複数マップ・複数試行ぶんの行が集まる。
    """
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        groups.setdefault(key(row), []).append(row)

    out: list[dict[str, Any]] = []
    for group_key, group_rows in groups.items():
        totals = [r["total_udon"] for r in group_rows]
        brands = [r["brand_count"] for r in group_rows]
        cumulative = [r["daily_cumulative"] for r in group_rows]
        wins = sum(1 for r in group_rows if r["is_winner"])
        out.append(
            {
                "group": group_key,
                "trials": len(group_rows),
                "mean_total_udon": statistics.fmean(totals),
                "stdev_total_udon": statistics.pstdev(totals) if len(totals) > 1 else 0.0,
                "mean_brand_count": statistics.fmean(brands),
                "mean_daily_cumulative": statistics.fmean(cumulative),
                "win_rate": wins / len(group_rows),
            }
        )
    return out


def aggregate_by_strategy(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """`aggregate_by()`を`strategy`列でグループ化する、最もよく使う形。"""
    return aggregate_by(rows, key=lambda r: r["strategy"])


def write_csv(rows: list[dict[str, Any]], path: Path) -> None:
    """`daily_rows()`/`summary_rows()`の出力をCSVに書き出す。

    条件ごとに`metadata`/`watch_cells`の内容が違うと行ごとの列集合が変わりうる
    ため、全行を先に見てヘッダーの和集合（登場順）を取ってから書く。
    """
    path = Path(path)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for key in row:
            if key not in seen:
                seen.add(key)
                fieldnames.append(key)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, restval="")
        writer.writeheader()
        writer.writerows(rows)
