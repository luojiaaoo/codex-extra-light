import pytest
import codex_usage


def test_remaining_percent_clamps_and_handles_invalid_values() -> None:
    assert codex_usage.remaining_percent(25) == 75
    assert codex_usage.remaining_percent(-10) == 100
    assert codex_usage.remaining_percent(130) == 0
    assert codex_usage.remaining_percent(None) is None
    assert codex_usage.remaining_percent("bad") is None


@pytest.mark.asyncio
async def test_collect_usage_async_accesses_real_codex_usage() -> None:
    usage = await codex_usage.collect_usage_async()

    assert set(usage) == {
        "plan_type",
        "five_hour_percent",
        "week_percent",
        "five_hour_reset",
        "week_reset",
        "updated_at",
        "error",
    }
    assert usage["error"] is None
    assert usage["updated_at"]

    for key in ("five_hour_percent", "week_percent"):
        value = usage[key]
        assert value is None or 0 <= value <= 100


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
