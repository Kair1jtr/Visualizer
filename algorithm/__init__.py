"""試合状況から行動計画(移動JSON)を作るアルゴリズムのテンプレート置き場。

`template.py`（貪欲法の実装例 + CLI）と `plan_builder.py`
（move()/wait()/goto() で組み立てるビルダー API）が実体。

このパッケージが import された時点でリポジトリ直下を sys.path に通し、
サブモジュールが `visualizer.*`（六角形座標・経路探索）を import できる
ようにする。
"""

import sys
from pathlib import Path

_REPO_ROOT = str(Path(__file__).resolve().parent.parent)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)
