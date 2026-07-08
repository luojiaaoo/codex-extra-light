import argparse
import asyncio
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, TypedDict

import codex_usage


ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "pc_client_config.json"
CONFIG_EXAMPLE_PATH = ROOT / "pc_client_config.example.json"
STATE_PATH = ROOT / ".pc_client_state.json"

State = Literal["working", "waiting", "idle"]

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


class SnapshotPayload(TypedDict):
    type: str
    state: State
    usage: codex_usage.UsageDisplay
    last_event: str
    last_event_at: str


@dataclass(frozen=True)
class ClientConfig:
    esp_host: str = "192.168.1.50"
    esp_port: int = 8765

    @property
    def esp_endpoint(self) -> str:
        return f"{self.esp_host}:{self.esp_port}"


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def load_config() -> ClientConfig:
    raw: dict[str, Any] = {}
    raw.update(read_json_object(CONFIG_EXAMPLE_PATH, required=False))
    raw.update(read_json_object(CONFIG_PATH, required=CONFIG_PATH.exists()))
    return coerce_config(raw)


def read_json_object(path: Path, *, required: bool) -> dict[str, Any]:
    if not path.exists():
        if required:
            raise FileNotFoundError(path)
        return {}
    with open(path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return data


def coerce_config(raw: dict[str, Any]) -> ClientConfig:
    defaults = ClientConfig()
    return ClientConfig(
        esp_host=str(raw.get("esp_host", defaults.esp_host)),
        esp_port=int(raw.get("esp_port", defaults.esp_port)),
    )


def load_cached_usage() -> codex_usage.UsageDisplay:
    try:
        data = read_json_object(STATE_PATH, required=False)
        usage = data.get("usage")
        if isinstance(usage, dict):
            return normalize_usage_dict(usage)
    except Exception:
        pass
    return dict(DEFAULT_USAGE)


def normalize_usage_dict(raw: dict[str, Any]) -> codex_usage.UsageDisplay:
    return {
        "plan_type": raw.get("plan_type"),
        "five_hour_percent": raw.get("five_hour_percent"),
        "week_percent": raw.get("week_percent"),
        "five_hour_reset": raw.get("five_hour_reset"),
        "week_reset": raw.get("week_reset"),
        "updated_at": raw.get("updated_at"),
        "error": raw.get("error"),
    }


def save_snapshot(snapshot: SnapshotPayload) -> None:
    tmp_path = STATE_PATH.with_suffix(STATE_PATH.suffix + ".tmp")
    with open(tmp_path, "w", encoding="utf-8") as handle:
        json.dump(snapshot, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    tmp_path.replace(STATE_PATH)


async def build_snapshot(event: str) -> SnapshotPayload:
    if event not in VALID_EVENTS:
        raise ValueError(f"Unsupported event: {event}")

    usage = load_cached_usage()
    if event == "Stop":
        try:
            usage = await codex_usage.collect_usage_async()
        except Exception as exc:
            usage = codex_usage.empty_usage_with_error(exc)

    return {
        "type": "snapshot",
        "state": VALID_EVENTS[event],
        "usage": usage,
        "last_event": event,
        "last_event_at": now_iso(),
    }


class EspSocketClient:
    def __init__(self, host: str, port: int, *, timeout: float = 2.0) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout

    async def post_snapshot(self, snapshot: SnapshotPayload) -> None:
        payload = json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")) + "\n"
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(self.host, self.port),
            timeout=self.timeout,
        )
        try:
            writer.write(payload.encode("utf-8"))
            await asyncio.wait_for(writer.drain(), timeout=self.timeout)
            try:
                await asyncio.wait_for(reader.readline(), timeout=self.timeout)
            except asyncio.TimeoutError:
                pass
        finally:
            writer.close()
            await writer.wait_closed()


async def run_hook_event(event: str, config: ClientConfig) -> int:
    snapshot = await build_snapshot(event)
    await EspSocketClient(config.esp_host, config.esp_port).post_snapshot(snapshot)
    save_snapshot(snapshot)
    return 0


async def async_main(args: argparse.Namespace) -> int:
    config = load_config()
    return await run_hook_event(args.event, config)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Codex ESP8266 status screen hook.")
    parser.add_argument("event", choices=sorted(VALID_EVENTS))
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(async_main(parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
