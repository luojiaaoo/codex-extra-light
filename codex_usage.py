import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TypedDict

import httpx


CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
USAGE_URL = "https://chatgpt.com/backend-api/wham/usage"
TOKEN_URL = "https://auth.openai.com/oauth/token"
REQUEST_TIMEOUT_SECONDS = 15.0


class CodexCredentials(TypedDict):
    access_token: str
    refresh_token: str | None
    account_id: str | None


class UsageDisplay(TypedDict):
    plan_type: str | None
    five_hour_percent: int | None
    week_percent: int | None
    five_hour_reset: int | float | None
    week_reset: int | float | None
    updated_at: str
    error: str | None


JsonObject = dict[str, Any]


def get_codex_credentials_path() -> Path | None:
    """按 Codex 常见位置查找 auth.json。"""
    home = Path.home()
    codex_home = os.environ.get("CODEX_HOME")
    candidates: list[Path] = []
    if codex_home:
        candidates.append(Path(codex_home) / "auth.json")
    candidates.extend(
        [
            home / ".config" / "codex" / "auth.json",
            home / ".codex" / "auth.json",
        ]
    )
    for path in candidates:
        if path.exists():
            return path
    return None


def load_codex_auth(auth_path: Path) -> CodexCredentials:
    """读取 Codex 登录凭证，只暴露本模块需要的令牌字段。"""
    with open(auth_path, "r", encoding="utf-8") as handle:
        auth_data = json.load(handle)

    tokens = auth_data.get("tokens", {})
    access_token = tokens.get("access_token")
    if not access_token:
        raise ValueError("auth.json 中没有 access_token")

    return {
        "access_token": access_token,
        "refresh_token": tokens.get("refresh_token"),
        "account_id": tokens.get("account_id"),
    }


def save_refreshed_auth(auth_path: Path, token_data: JsonObject) -> None:
    """刷新令牌后原子替换 auth.json，避免写到一半损坏认证文件。"""
    with open(auth_path, "r", encoding="utf-8") as handle:
        auth_data = json.load(handle)

    tokens = auth_data.setdefault("tokens", {})
    for key in ("access_token", "refresh_token", "id_token"):
        if token_data.get(key):
            tokens[key] = token_data[key]
    if token_data.get("expires_in") is not None:
        tokens["expires_in"] = token_data["expires_in"]

    tmp_path = auth_path.with_suffix(auth_path.suffix + ".tmp")
    with open(tmp_path, "w", encoding="utf-8") as handle:
        json.dump(auth_data, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    tmp_path.replace(auth_path)


async def refresh_codex_token_async(
    client: httpx.AsyncClient,
    refresh_token: str | None,
) -> JsonObject:
    if not refresh_token:
        raise ValueError("auth.json 中没有 refresh_token")

    response = await client.post(
        TOKEN_URL,
        data={
            "grant_type": "refresh_token",
            "client_id": CLIENT_ID,
            "refresh_token": refresh_token,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    response.raise_for_status()
    return response.json()


async def fetch_codex_usage_async(
    client: httpx.AsyncClient,
    access_token: str,
    account_id: str | None = None,
) -> JsonObject:
    headers: dict[str, str] = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0",
        "Content-Type": "application/json",
    }
    if account_id:
        headers["ChatGPT-Account-Id"] = account_id

    response = await client.get(USAGE_URL, headers=headers)
    response.raise_for_status()
    return response.json()


def normalize_usage(raw_usage: JsonObject) -> UsageDisplay:
    """把接口原始字段转换成屏幕端稳定消费的显示字段。"""
    rate_limit = raw_usage.get("rate_limit", {})
    primary_window = rate_limit.get("primary_window", {})
    secondary_window = rate_limit.get("secondary_window", {})

    five_hour_used = primary_window.get("used_percent")
    week_used = secondary_window.get("used_percent")

    return {
        "plan_type": raw_usage.get("plan_type"),
        "five_hour_percent": remaining_percent(five_hour_used),
        "week_percent": remaining_percent(week_used),
        "five_hour_reset": primary_window.get("reset_at"),
        "week_reset": secondary_window.get("reset_at"),
        "updated_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "error": None,
    }


def remaining_percent(used_percent: Any) -> int | None:
    if used_percent is None:
        return None
    try:
        return max(0, min(100, round(100 - float(used_percent))))
    except (TypeError, ValueError):
        return None


async def collect_usage_async(
    auth_path: str | Path | None = None,
    client: httpx.AsyncClient | None = None,
) -> UsageDisplay:
    """获取 Codex 用量；供电脑端守护进程 import 后直接 await 调用。"""
    auth_path = Path(auth_path) if auth_path else get_codex_credentials_path()
    if not auth_path:
        raise FileNotFoundError(
            "没有在 $CODEX_HOME、~/.config/codex 或 ~/.codex 中找到 Codex auth.json"
        )

    credentials = load_codex_auth(auth_path)
    if client is None:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as new_client:
            return await _collect_usage_with_client(auth_path, credentials, new_client)
    return await _collect_usage_with_client(auth_path, credentials, client)


async def _collect_usage_with_client(
    auth_path: Path,
    credentials: CodexCredentials,
    client: httpx.AsyncClient,
) -> UsageDisplay:
    try:
        raw_usage = await fetch_codex_usage_async(
            client,
            credentials["access_token"],
            credentials.get("account_id"),
        )
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code != 401:
            raise
        # 访问令牌过期时，只刷新一次令牌，然后重试用量接口。
        token_data = await refresh_codex_token_async(client, credentials.get("refresh_token"))
        save_refreshed_auth(auth_path, token_data)
        raw_usage = await fetch_codex_usage_async(
            client,
            token_data["access_token"],
            credentials.get("account_id"),
        )

    return normalize_usage(raw_usage)


def empty_usage_with_error(error: BaseException) -> UsageDisplay:
    """网络或认证失败时返回同结构数据，屏幕端可以直接显示错误。"""
    return {
        "plan_type": None,
        "five_hour_percent": None,
        "week_percent": None,
        "five_hour_reset": None,
        "week_reset": None,
        "updated_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "error": str(error),
    }
