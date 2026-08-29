# ヘキサうどん Visualizer

第37回全国高等専門学校プログラミングコンテスト 競技部門「ヘキサうどん」用の
ツール一式です。役割は2つあります。

1. **本番試合観戦**: 配布された公式簡易サーバー（`procon-server`）をサブ
   プロセスとして起動・停止し、その進行状況（各チームの位置・推定移動軌跡）を
   ブラウザで観戦できます（トップページ `/`）。
2. **シミュレーター観戦**: 公式ルールを忠実に再現したシミュレーター
   （`simulator/`）で1試合を走らせ、**1と同じ盤面**で表示します
   （`/sim.html`）。公式簡易サーバーに渡すのと同じ設定 JSON を読むので、
   同一設定の試合を実サーバー版とシミュレーター版で見比べられます。
   シミュレーターは全ステップの状態を保持しているため、軌跡は推定ではなく
   **実測**で、日中の途中経過をステップ単位で再生できます。

競技ルールの実装は `simulator/` の1か所だけです（`docs/ルール説明書.md` の
公式一次資料に対応）。ブラウザ側も 1・2 で同じ描画コードを使います。

> ルールの概要と API エンドポイント仕様の一覧は [SPEC.md](SPEC.md) にまとめています。

![screenshot](docs/screenshot.png)

## 起動方法

```bash
pip install -r requirements.txt
python app.py            # http://127.0.0.1:8000
# または
uvicorn app:app --http h11
```

- 本番試合を観戦する場合: ブラウザで http://127.0.0.1:8000 を開き、
  「試合サーバーを起動」を押してください。
- シミュレーターで見る場合: http://127.0.0.1:8000/sim.html を開き、
  「実行」を押してください。

## 本番試合観戦 (`/`)

配布物 `server/簡易サーバー/procon-server-*` を、実行中の OS/CPU に合わせて
このアプリ自身がサブプロセスとして起動・停止します。試合設定は既定で
`server/試合設定用JSONファイル/example.json` を使用します。

| メソッド/パス | 内容 |
|---|---|
| `POST /api/real/start?config=<path>` | `procon-server` を起動（`config` 省略時は配布サンプル）。チーム0のトークンで観戦を開始する |
| `POST /api/real/stop` | `procon-server` を停止する |
| `GET /api/real/status` | 観戦データ（試合設定・日ごとのスナップショット・推定移動軌跡）を返す |

- トップページ `/` はこれらのポーリングだけで動く**閲覧専用**ページです
  （試合そのものへの操作はできません。起動・停止ボタンはサブプロセスの
  管理用）。
- 公式APIは「自分のチームのトークンで見た自分の位置」と「他チーム全員の
  位置」は各日開始時点のスナップショットとして返しますが、**日中の実際の
  経路までは提供しません**。このアプリは前日・翌日のスナップショット間を
  地形・道路状況込みの Dijkstra 最短経路で結び、「推定移動軌跡」として
  表示します（実際の経路と一致するとは限りません）。
- 上記の理由により、**試合最終日の移動軌跡は原理上観測できません**
  （比較対象となる「翌日のスナップショット」が存在しないため）。
- 右側の「チーム」欄でチームを選ぶと、盤面下の「推測経路」欄にそのチームの
  車両（巡回車・補給車）ごとの出発点・到達点・通過セルの座標一覧が表示され、
  行をクリックするとその車両の推測経路だけが盤面上に半透明でハイライトされます
  （既定では軌跡線は表示されません）。
- 公式APIは試合終了後、すべてのエンドポイントを `403`（`match has ended`）
  で拒否するため、**最終的なスコアや在庫などの結果は公式APIから一切
  取得できません**。観戦中に取得できた日ごとのスナップショットのみが
  `GET /api/real/status` に残り続けます。

## シミュレーター観戦 (`/sim.html`)

公式ルール忠実シミュレーター（`simulator/`）で1試合を最後まで走らせ、
本番試合観戦とまったく同じ盤面で表示します。実時間の締切が無いので
「実行」を押すと全日程が一瞬で終わります。

| メソッド/パス | 内容 |
|---|---|
| `GET /api/sim/strategies?config=<path>` | 選べる戦略と、設定JSON上のプレイヤー一覧。UI はこれでプルダウンを組み立てる |
| `POST /api/sim/start?config=<path>&strategy=<spec>` | シミュレーターで1試合を実行する。`config` は `procon-server` に渡すのと同じ設定JSON（省略時は配布サンプル） |
| `POST /api/sim/stop` | 実行結果を破棄する |
| `GET /api/sim/status` | 観戦データを返す。**`GET /api/real/status` と同じ形** |

レスポンスの形を実サーバー版と揃えてあるため、ブラウザ側は同じ描画コード
（`static/js/matchview.js`）で両方を表示します。違いは次の3点です。

| | 本番試合観戦 `/` | シミュレーター観戦 `/sim.html` |
|---|---|---|
| 盤面を動かすもの | 公式簡易サーバー `procon-server` | `simulator/`（公式ルール忠実） |
| 移動軌跡 | **推定**（日をまたぐ2点を Dijkstra で結ぶ） | **実測**（全ステップの状態を保持） |
| 途中経過 | 見られない（各日のスナップショットのみ） | ステップ単位で再生できる |

シミュレーター側だけの追加情報として、日ごとの得点（①種類数 ②日ごと種類数の
累積 ③玉数）、道路セルごとの交通量、リジェクト理由、採用中の仮仕様
（`simulator/policies.py` の U-1／U-3／U-4／U-5／U-6）も表示されます。

### 戦略のプレイヤーごとの割り当て

行動計画は `simulator/strategy.py` の戦略クラスが組み立てます。**プレイヤーごとに
戦略とそのパラメータを個別に設定できる**ので、同じ盤面で戦略同士を戦わせたり、
同じ戦略の重み違いを比べたりできます。

画面右の「戦略の割り当て」欄でプレイヤーのボタンを押すと、ページ遷移なしで
設定ダイアログが開きます。戦略を選ぶとその戦略のパラメータ欄が入れ替わり、
数値・スイッチで細かく調整できます（「既定に戻す」で初期値に戻ります）。

| 戦略 | 内容 |
|---|---|
| `greedy` 貪欲法 | 系列の価値÷距離が最大のスポットへ。既取得系列も玉数のために拾う |
| `brand` 系列優先 | まだ取っていない系列だけを狙い、既取得系列は無視する |
| `nearest` 最近傍 | 系列を見ず、ただ近いスポットから順に回る（玉数狙い） |
| `stay` 待機 | その日ずっと動かない（比較の基準線） |

主なパラメータ:

| パラメータ | 対象 | 内容 |
|---|---|---|
| 距離の効き方 | 全戦略 | 大きいほど近場を優先。0 で距離を無視 |
| 1日に狙うスポット数 | 全戦略 | この数だけ回ったら残りは待機 |
| スポットを重複して狙わない | 全戦略 | 切ると複数の巡回車が同じスポットへ向かう |
| 補給車を巡回車に追従させる | 全戦略 | 切ると補給車はその場で待機 |
| 新規系列 / 本日未取得 / 取得済み の価値 | greedy・brand | 勝敗①②③のどれを重視するかの重みづけ |
| 在庫の重み | greedy・brand | 在庫が多いスポットを優先する度合い |

### 自分の戦略を追加する

`SpotScoreStrategy` を継承して `score_spot()` を書き、`@register` を付けるだけです。
`params` に宣言したパラメータは、API のスキーマにも設定ダイアログのフォームにも
自動で現れます（`app.py` も JavaScript も触る必要はありません）。

```python
from simulator.strategy import Param, SpotScoreStrategy, register

@register
class StockHungry(SpotScoreStrategy):
    name = "stock"
    label = "在庫優先"
    description = "在庫が多いスポットを優先する"
    params = SpotScoreStrategy.params + (
        Param("stock_weight", "在庫の重み", "float", 2.0, minimum=0.0, maximum=10.0),
    )

    def score_spot(self, state, team, spot, dist):
        stock = team.spot_stocks.get(spot.pos, 0)
        return stock * self.p["stock_weight"] * self._distance_factor(dist)
```

`score_spot()` の戻り値が大きいスポットから順に回ります（0以下は「狙わない」）。
歩き方（その日の道路状態でステップ数を数え、燃料が尽きる前に止める）と補給車の
動きは基底クラスが持つので、通常はこの1メソッドだけで済みます。1日の組み立てごと
変えたい場合は `plan()` を上書きしてください（`StayStrategy` がその例）。

既存戦略の重みだけを変えた派生を作るなら、`override_defaults()` で既定値を
差し替えるだけです（`BrandFirstStrategy` が `GreedyStrategy` に対してそうしています）。

## 構成

```
app.py                     FastAPI サーバー（本番観戦API + シミュレーター観戦API + 静的配信）
server/
  簡易サーバー/             配布された procon-server バイナリ（各OS/CPU版）
  試合設定用JSONファイル/   試合設定。procon-server とシミュレーターの
                           両方がこの同じファイルを読む（既定: example.json）
  回答システムに関する情報/  公式 API 仕様（OpenAPI）
simulator/                 公式ルール忠実シミュレーター（通信も描画も含まない純粋なロジック）
  engine.py                反映フェーズ／アクションフェーズ・日・試合の進行
  validation.py            受付時検証（構造 → 歩行 → 燃料 dry-run）
  state.py                 GameState / TeamState / AgentState / SpotDef / TrafficState
  actions.py               行動計画の表現と展開
  terrain.py               地形・道路状態・移動コスト表（〔要項〕表1）
  grid.py                  セル番号・座標・隣接関係（even-r）
  pathfinding.py           その日の道路状態を反映した六角グリッド上の Dijkstra
  strategy.py              戦略クラス（Strategy / SpotScoreStrategy と参照実装4種）。
                           継承して自分の戦略を足せる。ルールではなく攻略側の層
  policies.py              未確定仕様の切り替え（U-1/U-3/U-4/U-5/U-6）
  tracing.py               追跡ログとスナップショット
  scenarios.py             公式資料に載っている試合設定
  compare.py               戦略比較の実行基盤
visualizer/                入出力の層（ルールは持たない）
  procon_process.py        procon-server（配布バイナリ）のサブプロセス起動・停止
  spectator.py             procon-server をポーリングして観戦データを構築
                           （日ごとのスナップショット保持・軌跡のDijkstra推定）
  sim_spectator.py         simulator/ の試合を spectator.py と同じ形の JSON に
                           変換する観戦アダプタ（軌跡は実測・ステップ記録つき）
  hexgrid.py               六角形グリッド（even-r）座標・方向コード変換
  pathfinding.py           地形コスト付き Dijkstra（spectator.py の軌跡推定用）
tests/                     unittest 125件（ルール・交通量・経路探索・戦略・観戦アダプタ）
examples/
  simulator_demo.py        simulator/ の公式例の再生・戦略比較デモ（CLI）
static/
  index.html               本番試合観戦ビュー（procon-serverの起動・停止 + 閲覧、`/`）
  sim.html                 シミュレーター観戦ビュー（`/sim.html`）
  style.css                共通スタイル（ライト/ダーク対応）
  js/matchview.js          観戦ビューの描画本体（index.html と sim.html で共用）
  js/realmatch.js          matchview に /api/real/* を渡すだけの薄い結線
  js/simmatch.js           matchview の結線 + 戦略の設定ダイアログ（スキーマからフォーム生成）
  js/hex.js / js/palette.js  六角形座標計算（even-r）・配色
docs/                      公式一次資料と、そこから起こしたルール説明書・状態設計書
```

## テスト

```bash
python -m unittest discover -s tests -t .
```

`simulator/` は公式Q&A補足資料の状態遷移例（巡回車A〜G＋補給車A、6ステップ）を
全項目で再現します。詳細は [docs/実装ノート.md](docs/実装ノート.md)。

## 備考

- 本番試合観戦（`/api/real/*`）は配布済みの `procon-server` バイナリを実際に
  起動して通信するため、公式APIの挙動をそのまま反映します。ただし公式APIは
  各日開始時のスナップショットしか返さないため、日中の軌跡は推定です。
- シミュレーター観戦（`/api/sim/*`）は通信を伴わず、`simulator/` の状態遷移を
  そのまま描画します。公式資料だけでは決まらない項目（セル番号の割り当て、
  交通量の除算方式など）は `simulator/policies.py` に集約してあり、
  仕様が確定したらそこだけを変更すれば全体が追随します。
- 対戦クライアント（自作プログラムを公式簡易サーバーに接続して実際に指すもの）は
  まだありません。`simulator/strategy.py` の戦略を `server/回答システムに関する情報/`
  の API に載せる作業が次の一歩になります。
