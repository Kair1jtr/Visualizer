# algorithm/ — 行動計画アルゴリズムのテンプレート

**試合状況（公式フォーマットのJSON）を受け取り、行動計画（移動JSON）を
計算して返す**アルゴリズムのテンプレート置き場です。自分の戦略を書く際の
出発点として `template.py` をコピー・改造してください。

## 中身

- `template.py` — 貪欲法（`examples/client.py` と同じ戦略）の実装例:
  - 巡回車: 最寄りの未予約スポットへ Dijkstra 経路で連鎖訪問
  - 補給車: 燃料が最も少ない巡回車の到達予定地点へ向かう
  - 燃料切れの巡回車は待機扱いになるだけなので、戦略の改良は自由

  書き換える場所は `plan_patrol()` / `plan_supply()`（各エージェント1台分の
  1日の行動計画を決める）と、それらを呼び出す `build_plans()`
  （試合状況→行動計画のフォーマット変換）です。

- `plan_builder.py` — **方向コード（0〜5）や待機の負数（`-N`）を直接書かず、
  `move()`/`wait()`/`goto()` の関数呼び出しで1日分の行動計画を組み立てる
  ビルダー**。JSON配列を手で組み立てる代わりに使う。

  ```python
  from algorithm.plan_builder import DayPlan

  plan = DayPlan(match, info, kinds)   # kinds は自分が提出したエージェント種別
  plan.agent(0).goto(43)               # セル43へ、届く分だけ経路に沿って移動
  plan.agent(1).move(2)                # 方向2(右)へ1マス
  plan.agent(2).wait(5)                # 5ステップ待機
  # agent(3) など何も呼ばなければ自動的に残り全ステップが待機になる

  plans_json = plan.to_json()          # そのまま POST /api/actions の body
  ```

  - `move(direction)` — 0〜5方向へ1マス移動。盤外・池・ステップ予算不足・
    （巡回車の）燃料不足のときは `ValueError` を投げる。事前確認したい場合は
    `can_move(direction)`（bool）を使う
  - `move_to(cell)` — 隣接セルへの移動を、方向コードを自分で計算せずに指定
  - `goto(cell)` — 最短経路で向かう。予算が尽きたら途中で止まる
    （戻り値は実際に到達したセル）。残りは次の `goto()`/`move()` で続けられる
  - `wait(n=1)` — nステップ待機
  - `.remaining` / `.cell` / `.fuel` で現在の残りステップ・位置・燃料を確認できる

## 入出力フォーマット

| 名前 | 内容 | 取得元 |
|---|---|---|
| `match` | 試合開始前のマップ構成フォーマット | `GET /api/match` |
| `info` | 各日開始時の試合情報フォーマット | `GET /api/match/{day}` |
| `kinds` | 自分が提出したエージェント種別 | 自分が `POST /api/agents` で送った値 |
| 戻り値 `plans` | 行動計画の回答フォーマット | そのまま `POST /api/actions` の body に使える |

いずれも `SPEC.md` の1章・2章に仕様がある公式フォーマットそのもの。

## 使い方（CLI）

```bash
# match.json / info.json（それぞれ公式フォーマットのJSON）を用意して:
python algorithm/template.py --match match.json --info info.json --kinds 0,0,0,1

# {"match":..., "info":..., "kinds":[...]} をまとめた1つのJSONを標準入力から:
cat combined.json | python algorithm/template.py

# 実行中のモックサーバーから直接取得して計算（--kinds は自分が提出した種別）:
python app.py &                # サーバー起動
python algorithm/template.py --server http://127.0.0.1:8000 --day 0 --kinds 0,0,0,1
```

標準出力に行動計画（移動JSON）が書き出されます。そのまま
`POST /api/actions?day=N` に送信すれば提出できます。

## 使い方（Pythonから直接）

```python
from algorithm.template import build_plans

plans = build_plans(match, info, kinds)
```

## 通信ループへの組み込み例

- `examples/client.py` — `build_plans()`（Dijkstraで最適化した貪欲法）を使う版
- `example_manual.py` — `plan_builder.py`（`move()`/`goto()`/`wait()`）を使う版

どちらも `/api/live/new` → `/api/agents` → (`/api/match/{day}` →
`/api/actions` を日数分繰り返す) という同じ通信ループです。実際に動かして
比べてみてください:

```bash
python app.py                           # 別ターミナルでサーバー起動
python algorithm/example_manual.py --seed 42
```

`decide(match, info, kinds)` 関数の中身（`plan_builder.py` の呼び出し方）が
自作プログラムを書く際の見本になります。
