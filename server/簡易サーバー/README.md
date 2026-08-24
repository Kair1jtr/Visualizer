# procon-server 動作確認用サーバー

第37回全国高等専門学校プログラミングコンテストの競技者向け動作確認サーバーです。

## 使い方

```bash
# Linux
./procon-server-linux-amd64 -config config.json

# macOS
./procon-server-darwin-arm64 -config config.json

# Windows
procon-server-windows-amd64.exe -config config.json
```

### フラグ

| フラグ | デフォルト | 説明 |
|--------|-----------|------|
| `-config` | （必須） | 問題・参加チーム情報を含む設定 JSON のパス |
| `-addr` | `PORT` 環境変数、未設定時は `:8080` | HTTP リッスンアドレス |
| `-kind-deadline` | `5s` | 起動からエージェント種別締切までの時間 |
| `-match-start-delay` | `5s` | 種別締切から試合開始までの時間 |

起動後、指定時間経過で自動的に種別締切・試合開始が行われます。

## 同梱ファイル

| ファイル | 説明 |
|---------|------|
| `procon-server-linux-amd64` | Linux (amd64) 向けバイナリ |
| `procon-server-darwin-arm64` | macOS (Apple Silicon) 向けバイナリ |
| `procon-server-darwin-amd64` | macOS (Intel) 向けバイナリ |
| `procon-server-windows-amd64.exe` | Windows (amd64) 向けバイナリ |
| `CREDITS.txt` | 依存ライブラリのライセンス情報 |
