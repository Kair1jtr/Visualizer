"""公式配布の簡易サーバー（procon-server）をサブプロセスとして起動・停止する。

`server/簡易サーバー/` に同梱されているバイナリのうち、実行中のOS/CPUに
合ったものを選び、`server/試合設定用JSONファイル/example.json` の試合設定で
起動する。試合の開始・終了はこのサブプロセスの起動・停止に対応する
（procon-server 自身が起動時刻・締切をすべて内部で管理し、
`-kind-deadline` 秒後にエージェント種別受付を締め切り、
その後 `-match-start-delay` 秒後に試合を開始する）。
"""

import platform
import socket
import subprocess
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
SERVER_DIR = BASE_DIR / "server" / "簡易サーバー"
DEFAULT_CONFIG = BASE_DIR / "server" / "試合設定用JSONファイル" / "example.json"

_BINARY_BY_PLATFORM = {
    ("Linux", "x86_64"): "procon-server-linux-amd64",
    ("Darwin", "x86_64"): "procon-server-darwin-amd64",
    ("Darwin", "arm64"): "procon-server-darwin-arm64",
    ("Windows", "AMD64"): "procon-server-windows-amd64.exe",
}


class ProconProcessError(Exception):
    """起動・停止の失敗（プロセス管理上のエラー）。"""


def _pick_binary() -> Path:
    key = (platform.system(), platform.machine())
    name = _BINARY_BY_PLATFORM.get(key)
    if name is None:
        raise ProconProcessError(
            f"このOS/CPU（{key[0]} {key[1]}）用の procon-server バイナリが見つかりません。"
            f"対応: {sorted(_BINARY_BY_PLATFORM.values())}"
        )
    path = SERVER_DIR / name
    if not path.exists():
        raise ProconProcessError(f"バイナリが見つかりません: {path}")
    return path


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class ProconProcess:
    """procon-server の1インスタンスのライフサイクルを管理する。"""

    def __init__(self):
        self.proc: subprocess.Popen | None = None
        self.port: int | None = None
        self.config_path: Path | None = None
        self.log_lines: list[str] = []
        self._log_thread = None

    @property
    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    @property
    def base_url(self) -> str | None:
        return f"http://127.0.0.1:{self.port}" if self.port else None

    def start(
        self,
        config_path: Path | str = DEFAULT_CONFIG,
        kind_deadline: str = "5s",
        match_start_delay: str = "5s",
    ) -> str:
        """procon-server を起動し、base_url を返す。既に起動中なら例外。"""
        if self.running:
            raise ProconProcessError("すでに試合が起動中です（先に停止してください）")
        binary = _pick_binary()
        config_path = Path(config_path).resolve()
        if not config_path.exists():
            raise ProconProcessError(f"設定ファイルが見つかりません: {config_path}")

        port = _free_port()
        self.proc = subprocess.Popen(
            [
                str(binary),
                "-config", str(config_path),
                "-addr", f":{port}",
                "-kind-deadline", kind_deadline,
                "-match-start-delay", match_start_delay,
            ],
            cwd=str(binary.parent),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        self.port = port
        self.config_path = config_path
        self.log_lines = []
        self._start_log_reader()

        # プロセスが即座に落ちていないか（設定ファイル不正など）だけ確認する
        time.sleep(0.3)
        if not self.running:
            log = "\n".join(self.log_lines)
            self.proc = None
            self.port = None
            raise ProconProcessError(f"procon-server の起動に失敗しました:\n{log}")
        return self.base_url

    def _start_log_reader(self):
        import threading

        proc = self.proc

        def reader():
            if proc.stdout is None:
                return
            for line in proc.stdout:
                self.log_lines.append(line.rstrip())
                del self.log_lines[:-200]  # 直近200行だけ保持

        self._log_thread = threading.Thread(target=reader, daemon=True)
        self._log_thread.start()

    def stop(self) -> None:
        """procon-server を停止する（起動していなければ何もしない）。"""
        if self.proc is None:
            return
        self.proc.terminate()
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait(timeout=5)
        self.proc = None
        self.port = None
