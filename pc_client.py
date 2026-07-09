import argparse
import asyncio
import json
import sys
import threading
import tkinter as tk
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from tkinter import messagebox, ttk
from typing import Any, Literal, NotRequired, TypedDict

import codex_usage


ROOT = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "pc_client_config.json"

State = Literal["working", "waiting", "idle"]

VALID_EVENTS: dict[str, State] = {
    "UserPromptSubmit": "working",
    "PreToolUse": "working",
    "PostToolUse": "working",
    "PermissionRequest": "waiting",
    "Stop": "idle",
}


class SnapshotPayload(TypedDict):
    type: str
    last_event: str
    last_event_at: str
    state: NotRequired[State]
    usage: NotRequired[codex_usage.UsageDisplay]


@dataclass(frozen=True)
class ClientConfig:
    esp_host: str = "192.168.1.50"
    esp_port: int = 8765
    poll_minutes: int = 1

    @property
    def esp_endpoint(self) -> str:
        return f"{self.esp_host}:{self.esp_port}"

    @property
    def poll_seconds(self) -> int:
        return max(1, self.poll_minutes) * 60


TZ_CST = timezone(timedelta(hours=8))


def _icon_path() -> str:
    if getattr(sys, "frozen", False):
        return str(Path(sys._MEIPASS) / "favicon.ico")
    return str(Path(__file__).parent / "favicon.ico")


def now_iso() -> str:
    return datetime.now(TZ_CST).isoformat(timespec="seconds")


def load_config() -> ClientConfig:
    raw = read_json_object(CONFIG_PATH, required=CONFIG_PATH.exists())
    return coerce_config(raw)


def save_config(config: ClientConfig) -> None:
    data = {
        "esp_host": config.esp_host,
        "esp_port": config.esp_port,
        "poll_minutes": config.poll_minutes,
    }
    with open(CONFIG_PATH, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


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
        esp_host=str(raw.get("esp_host", defaults.esp_host)).strip() or defaults.esp_host,
        esp_port=int(raw.get("esp_port", defaults.esp_port)),
        poll_minutes=int(raw.get("poll_minutes", defaults.poll_minutes)),
    )


def build_status_snapshot(event: str) -> SnapshotPayload:
    if event not in VALID_EVENTS:
        raise ValueError(f"Unsupported event: {event}")
    return {
        "type": "snapshot",
        "state": VALID_EVENTS[event],
        "last_event": event,
        "last_event_at": now_iso(),
    }


async def build_usage_snapshot() -> SnapshotPayload:
    try:
        usage = await codex_usage.collect_usage_async()
    except Exception as exc:
        usage = codex_usage.empty_usage_with_error(exc)

    return {
        "type": "snapshot",
        "usage": usage,
        "last_event": "UsagePoll",
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


async def send_snapshot(snapshot: SnapshotPayload, config: ClientConfig) -> None:
    await EspSocketClient(config.esp_host, config.esp_port).post_snapshot(snapshot)


async def run_hook_event(event: str, config: ClientConfig) -> int:
    await send_snapshot(build_status_snapshot(event), config)
    return 0


class DesktopApp:
    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title("CodexExtraLight")
        self.root.geometry("440x46")
        self.root.resizable(False, False)
        self.root.iconbitmap(_icon_path())

        self.config = load_config()
        self.polling = False
        self.worker_running = False
        self.stop_event = threading.Event()
        self.console_win = None

        self.switch_text = tk.StringVar(value="▶ Start")
        self.endpoint_text = tk.StringVar(value=self.config.esp_endpoint)

        self.build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def build_ui(self) -> None:
        row = ttk.Frame(self.root, padding=(12, 8))
        row.pack(fill="both", expand=True)

        ttk.Label(row, textvariable=self.endpoint_text, foreground="#888", font=("Segoe UI", 9)).pack(side="left")

        period = self.config.poll_minutes
        label = f"· {period}m"
        self.period_label = ttk.Label(row, text=label, foreground="#888", font=("Segoe UI", 9))
        self.period_label.pack(side="left", padx=(6, 0))

        ttk.Button(row, text="Console", width=7, command=self.open_console).pack(side="right", padx=(4, 0))
        ttk.Button(row, text="⚙", width=3, command=self.open_config).pack(side="right")

        self.btn = tk.Button(
            row,
            textvariable=self.switch_text,
            command=self.toggle_polling,
            font=("Segoe UI", 9, "bold"),
            bg="#4CAF50",
            fg="white",
            activebackground="#45a049",
            activeforeground="white",
            relief="flat",
            bd=0,
            padx=12,
            cursor="hand2",
        )
        self.btn.pack(side="right", padx=(0, 8))

        self._create_console()

    def _create_console(self) -> None:
        win = tk.Toplevel(self.root)
        win.title("Console")
        win.geometry("500x360")
        win.protocol("WM_DELETE_WINDOW", win.withdraw)
        win.withdraw()

        log_frame = ttk.Frame(win, padding=8)
        log_frame.pack(fill="both", expand=True)
        self.log_area = tk.Text(
            log_frame,
            bg="#1e1e1e",
            fg="#d4d4d4",
            insertbackground="#d4d4d4",
            relief="flat",
            borderwidth=0,
            font=("Consolas", 9),
            wrap="word",
            state="disabled",
        )
        scrollbar = ttk.Scrollbar(log_frame, command=self.log_area.yview)
        self.log_area.configure(yscrollcommand=scrollbar.set)
        self.log_area.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        self.console_win = win

    def open_console(self) -> None:
        self.console_win.deiconify()
        self.console_win.lift()

    def toggle_polling(self) -> None:
        self.polling = not self.polling
        self.stop_event.clear()
        if self.polling:
            self.switch_text.set("■ Stop")
            self.btn.configure(bg="#f44336", activebackground="#d32f2f")
        else:
            self.switch_text.set("▶ Start")
            self.btn.configure(bg="#4CAF50", activebackground="#45a049")
        if self.polling and not self.worker_running:
            threading.Thread(target=self.poll_loop, daemon=True).start()

    def poll_loop(self) -> None:
        self.worker_running = True
        self.log("--- Polling started ---")
        try:
            while self.polling and not self.stop_event.is_set():
                self.log("Fetching Codex usage…")
                snapshot = None
                for attempt in (1, 2):
                    try:
                        snapshot = asyncio.run(build_usage_snapshot())
                        break
                    except Exception as exc:
                        self.log(f"Attempt {attempt} failed: {exc}")
                        if attempt == 1:
                            self.log("Retrying…")
                if snapshot is None:
                    self.log("Skipped — fetch failed after retry")
                else:
                    usage = snapshot.get("usage", {})
                    if usage.get("error"):
                        self.log(f"Usage error: {usage['error']}")
                    else:
                        self.log(f"Sending: {json.dumps(snapshot, ensure_ascii=False)}")
                        try:
                            asyncio.run(send_snapshot(snapshot, self.config))
                            self.log(f"Sent OK → {self.format_usage(usage)}")
                        except Exception as exc:
                            self.log(f"Send failed: {exc}")

                seconds = self.config.poll_seconds
                for _ in range(seconds):
                    if not self.polling or self.stop_event.wait(1):
                        return
        finally:
            self.log("--- Polling stopped ---")
            self.worker_running = False

    def log(self, message: str) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        line = f"[{timestamp}] {message}\n"

        def append():
            self.log_area.configure(state="normal")
            self.log_area.insert("end", line)
            # Keep last 300 lines
            if int(self.log_area.index("end-1c").split(".")[0]) > 300:
                self.log_area.delete("1.0", "2.0")
            self.log_area.see("end")
            self.log_area.configure(state="disabled")

        self.root.after(0, append)

    def format_usage(self, usage: dict[str, Any]) -> str:
        plan = usage.get("plan_type") or "unknown"
        five = percent_text(usage.get("five_hour_percent"))
        week = percent_text(usage.get("week_percent"))
        updated = short_time(usage.get("updated_at"))
        return f"{plan} | 5h {five} | week {week} | {updated}"

    def open_config(self) -> None:
        style = ttk.Style()
        style.configure("Dialog.TButton", padding=(0, 8))

        dialog = tk.Toplevel(self.root)
        dialog.title("Settings")
        dialog.geometry("320x220")
        dialog.resizable(False, False)
        dialog.transient(self.root)
        dialog.grab_set()

        host_var = tk.StringVar(value=self.config.esp_host)
        port_var = tk.StringVar(value=str(self.config.esp_port))
        poll_var = tk.StringVar(value=str(self.config.poll_minutes))

        frame = ttk.Frame(dialog, padding=14)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="ESP IP").pack(anchor="w")
        ttk.Entry(frame, textvariable=host_var).pack(fill="x", pady=(2, 8))
        ttk.Label(frame, text="Port").pack(anchor="w")
        ttk.Entry(frame, textvariable=port_var).pack(fill="x", pady=(2, 8))
        ttk.Label(frame, text="Poll (min)").pack(anchor="w")
        ttk.Entry(frame, textvariable=poll_var).pack(fill="x", pady=(2, 12))

        buttons = ttk.Frame(frame)
        buttons.pack(fill="x")

        def save() -> None:
            host = host_var.get().strip()
            try:
                port = int(port_var.get().strip())
            except ValueError:
                messagebox.showerror("Invalid", "Port must be a number.", parent=dialog)
                return
            try:
                poll = int(poll_var.get().strip())
            except ValueError:
                messagebox.showerror("Invalid", "Poll must be a number.", parent=dialog)
                return
            if not host:
                messagebox.showerror("Invalid", "ESP IP is required.", parent=dialog)
                return
            if not (1 <= port <= 65535):
                messagebox.showerror("Invalid", "Port must be 1-65535.", parent=dialog)
                return
            if poll < 1:
                messagebox.showerror("Invalid", "Poll must be >= 1 minute.", parent=dialog)
                return

            self.config = ClientConfig(host, port, poll)
            save_config(self.config)
            self.endpoint_text.set(self.config.esp_endpoint)
            label = f"· {poll}m"
            self.period_label.configure(text=label)
            dialog.destroy()

        tk.Button(buttons, text="保存", width=3, command=save).pack(side="left")
        tk.Button(buttons, text="取消", width=3, command=dialog.destroy).pack(side="left", padx=(8, 0))

    def on_close(self) -> None:
        self.polling = False
        self.stop_event.set()
        self.root.destroy()

    def run(self) -> int:
        self.root.mainloop()
        return 0


def percent_text(value: Any) -> str:
    if value is None:
        return "n/a"
    return str(value) + "%"


def short_time(value: Any) -> str:
    if not value:
        return "n/a"
    text = str(value)
    if "T" in text:
        return text.split("T", 1)[1][:5]
    return text[:16]


async def async_main(args: argparse.Namespace) -> int:
    config = load_config()
    return await run_hook_event(args.event, config)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="CodexExtraLight")
    parser.add_argument("args", nargs="*", help="Optional hook event name.")
    parsed = parser.parse_args(argv)

    if not parsed.args:
        parsed.mode = "desktop"
        parsed.event = None
        return parsed

    if len(parsed.args) == 1 and parsed.args[0] in VALID_EVENTS:
        parsed.mode = "hook"
        parsed.event = parsed.args[0]
        return parsed

    parser.error("use no args for desktop mode, or pass one event: " + ", ".join(sorted(VALID_EVENTS)))
    raise AssertionError("unreachable")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.mode == "desktop":
        return DesktopApp().run()
    return asyncio.run(async_main(args))


if __name__ == "__main__":
    sys.exit(main())
