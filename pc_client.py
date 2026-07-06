import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, TypedDict

import codex_usage


ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "pc_client_config.json"
CONFIG_EXAMPLE_PATH = ROOT / "pc_client_config.example.json"
State = Literal["working", "waiting", "idle"]
RefreshSource = Literal["startup", "stop", "periodic"]


class ClientConfig(TypedDict):
    esp_host: str
    esp_port: int
    listen_host: str
    listen_port: int
    usage_refresh_seconds: int


class HookPayload(TypedDict):
    event: str


class SnapshotPayload(TypedDict):
    type: str
    state: State
    usage: codex_usage.UsageDisplay
    last_event: str
    last_event_at: str


VALID_EVENTS: dict[str, State] = {
    "UserPromptSubmit": "working",
    "PreToolUse": "working",
    "PostToolUse": "working",
    "PermissionRequest": "waiting",
    "Stop": "idle",
}
DEFAULT_USAGE: codex_usage.UsageDisplay = {
    "plan_type": None,
    "five_hour_percent": None,
    "week_percent": None,
    "five_hour_reset": None,
    "week_reset": None,
    "updated_at": None,
    "error": "usage not loaded yet",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def load_config() -> ClientConfig:
    config: ClientConfig = {
        "esp_host": "192.168.1.50",
        "esp_port": 8765,
        "listen_host": "127.0.0.1",
        "listen_port": 8766,
        "usage_refresh_seconds": 60,
    }
    if CONFIG_EXAMPLE_PATH.exists():
        try:
            with open(CONFIG_EXAMPLE_PATH, "r", encoding="utf-8") as handle:
                config.update(_coerce_config(json.load(handle)))
        except Exception:
            pass
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, "r", encoding="utf-8") as handle:
            config.update(_coerce_config(json.load(handle)))
    return config


def _coerce_config(raw: dict[str, Any]) -> ClientConfig:
    return {
        "esp_host": str(raw.get("esp_host", "192.168.1.50")),
        "esp_port": int(raw.get("esp_port", 8765)),
        "listen_host": str(raw.get("listen_host", "127.0.0.1")),
        "listen_port": int(raw.get("listen_port", 8766)),
        "usage_refresh_seconds": int(raw.get("usage_refresh_seconds", 60)),
    }


class ScreenDaemon:
    def __init__(self, config: ClientConfig) -> None:
        self.config = config
        self.state: State = "idle"
        self.usage = dict(DEFAULT_USAGE)
        self.last_event = "startup"
        self.last_event_at = now_iso()
        self.lock = asyncio.Lock()
        self.usage_task: asyncio.Task[None] | None = None
        self.pending_usage_refresh = False
        self.last_usage_refresh_at = 0.0

    async def run(self) -> None:
        host = self.config["listen_host"]
        port = int(self.config["listen_port"])
        server = await asyncio.start_server(self.handle_hook_client, host, port)
        print(f"Codex 状态屏守护进程正在监听 {host}:{port}")
        print(f"ESP 目标地址：{self.config['esp_host']}:{self.config['esp_port']}")

        self.schedule_usage_refresh("startup")
        asyncio.create_task(self.periodic_usage_refresh())

        async with server:
            await server.serve_forever()

    async def handle_hook_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        try:
            data = await asyncio.wait_for(reader.readline(), timeout=1.0)
            payload: HookPayload = json.loads(data.decode("utf-8").strip() or "{}")
            await self.apply_event(payload.get("event", ""))
            writer.write(b'{"ok":true}\n')
        except Exception as exc:
            writer.write(json.dumps({"ok": False, "error": str(exc)}).encode("utf-8") + b"\n")
        finally:
            try:
                await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()

    async def apply_event(self, event: str) -> None:
        if event not in VALID_EVENTS:
            raise ValueError(f"Unsupported event: {event}")

        async with self.lock:
            self.state = VALID_EVENTS[event]
            self.last_event = event
            self.last_event_at = now_iso()
        await self.push_snapshot()
        if event == "Stop":
            self.schedule_usage_refresh("stop")

    async def periodic_usage_refresh(self) -> None:
        interval = max(60, int(self.config.get("usage_refresh_seconds", 60)))
        while True:
            elapsed = asyncio.get_running_loop().time() - self.last_usage_refresh_at
            await asyncio.sleep(max(1.0, interval - elapsed))
            elapsed = asyncio.get_running_loop().time() - self.last_usage_refresh_at
            if elapsed >= interval:
                self.schedule_usage_refresh("periodic")

    def schedule_usage_refresh(self, source: RefreshSource) -> None:
        # 主动刷新和周期刷新共用同一个时间戳，避免 Stop 后马上重复刷新。
        self.last_usage_refresh_at = asyncio.get_running_loop().time()
        if self.usage_task and not self.usage_task.done():
            if source != "periodic":
                self.pending_usage_refresh = True
            return
        self.usage_task = asyncio.create_task(self.refresh_usage())

    async def refresh_usage(self) -> None:
        while True:
            try:
                usage = await codex_usage.collect_usage_async()
            except Exception as exc:
                usage = codex_usage.empty_usage_with_error(exc)
            async with self.lock:
                self.usage = usage
            await self.push_snapshot()
            if not self.pending_usage_refresh:
                return
            self.pending_usage_refresh = False
            self.last_usage_refresh_at = asyncio.get_running_loop().time()

    async def snapshot(self) -> SnapshotPayload:
        async with self.lock:
            return {
                "type": "snapshot",
                "state": self.state,
                "usage": self.usage,
                "last_event": self.last_event,
                "last_event_at": self.last_event_at,
            }

    async def push_snapshot(self) -> None:
        payload = json.dumps(
            await self.snapshot(),
            ensure_ascii=False,
            separators=(",", ":"),
        ) + "\n"
        host = self.config["esp_host"]
        port = int(self.config["esp_port"])
        try:
            reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=2.0)
            writer.write(payload.encode("utf-8"))
            await asyncio.wait_for(writer.drain(), timeout=2.0)
            writer.close()
            await writer.wait_closed()
        except OSError as exc:
            print(f"ESP push failed: {exc}", file=sys.stderr)
        except asyncio.TimeoutError:
            print("ESP push failed: timeout", file=sys.stderr)


async def send_hook_event(event: str, config: ClientConfig) -> int:
    if event not in VALID_EVENTS:
        raise SystemExit(f"Unsupported event: {event}")
    payload = json.dumps({"event": event}, separators=(",", ":")) + "\n"
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(config["listen_host"], int(config["listen_port"])),
            timeout=0.35,
        )
        writer.write(payload.encode("utf-8"))
        await asyncio.wait_for(writer.drain(), timeout=0.35)
        try:
            await asyncio.wait_for(reader.read(1024), timeout=0.35)
        except asyncio.TimeoutError:
            pass
        writer.close()
        await writer.wait_closed()
    except (OSError, asyncio.TimeoutError):
        # 守护进程未运行时 hook 也必须快速退出，不能拖慢 Codex。
        return 0
    return 0


async def async_main(args: argparse.Namespace) -> int:
    config = load_config()
    if args.command == "daemon":
        await ScreenDaemon(config).run()
        return 0
    if args.command == "hook":
        return await send_hook_event(args.event, config)
    raise SystemExit(f"Unknown command: {args.command}")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Codex ESP8266 状态屏电脑端客户端。")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("daemon", help="运行本机常驻守护进程")

    hook_parser = subparsers.add_parser("hook", help="向守护进程发送一个 Codex hook 事件")
    hook_parser.add_argument("event", choices=sorted(VALID_EVENTS))

    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(async_main(parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
