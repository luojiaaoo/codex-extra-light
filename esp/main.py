import gc
import time

import network
import uasyncio as asyncio
import ujson as json
from machine import Pin

import config
from tft_display import TFT


STATE_IDLE = "idle"
STATE_WAITING = "waiting"
STATE_WORKING = "working"


def led_on(led):
    led.value(0 if getattr(config, "LED_ACTIVE_LOW", True) else 1)


def led_off(led):
    led.value(1 if getattr(config, "LED_ACTIVE_LOW", True) else 0)


def led_toggle(led):
    led.value(0 if led.value() else 1)


class CodexScreen:
    def __init__(self):
        self.tft = TFT(config)
        self.state = STATE_IDLE
        self.usage = {
            "plan_type": None,
            "five_hour_percent": None,
            "week_percent": None,
            "updated_at": None,
            "error": "waiting for PC client",
        }
        self.blink_on = False
        self.draw_all()

    def show_message(self, message):
        self.usage = {
            "plan_type": None,
            "five_hour_percent": None,
            "week_percent": None,
            "updated_at": None,
            "error": message,
        }
        self.draw_usage()
        self.draw_status()

    def draw_all(self):
        self.tft.fill(config.BACKGROUND)
        self.draw_usage()
        self.draw_status()

    def draw_usage(self):
        self.tft.fill_rect(0, 0, config.TFT_WIDTH, 198, config.BACKGROUND)
        self.tft.text("CODEX", 8, 10, config.TEXT, config.BACKGROUND, 3)
        if self.usage.get("error"):
            self.tft.text("usage error", 8, 52, config.BRIGHT_YELLOW, config.BACKGROUND, 2)
            self.tft.text(str(self.usage.get("error"))[:25], 8, 78, config.TEXT, config.BACKGROUND, 1)
            return

        plan = self.usage.get("plan_type") or "unknown"
        five = self.percent_text(self.usage.get("five_hour_percent"))
        week = self.percent_text(self.usage.get("week_percent"))
        updated = self.short_time(self.usage.get("updated_at"))

        self.tft.text("PLAN " + str(plan)[:12], 8, 52, config.TEXT, config.BACKGROUND, 2)
        self.tft.text("5H   " + five, 8, 84, config.TEXT, config.BACKGROUND, 3)
        self.tft.text("WEEK " + week, 8, 122, config.TEXT, config.BACKGROUND, 3)
        self.tft.text("UPD " + updated, 8, 166, config.TEXT, config.BACKGROUND, 1)

    def percent_text(self, value):
        if value is None:
            return "n/a"
        return str(value) + "%"

    def short_time(self, value):
        if not value:
            return "n/a"
        if "T" in value:
            return value.split("T", 1)[1][:5]
        return str(value)[:16]

    def draw_status(self):
        top = 200
        height = config.TFT_HEIGHT - top
        block_w = config.TFT_WIDTH // 3

        red = config.DIM_RED
        yellow = config.DIM_YELLOW
        green = config.DIM_GREEN
        if self.state == STATE_WORKING and self.blink_on:
            red = config.BRIGHT_RED
        if self.state == STATE_WAITING:
            yellow = config.BRIGHT_YELLOW
        if self.state == STATE_IDLE:
            green = config.BRIGHT_GREEN

        self.tft.fill_rect(0, top, block_w, height, red)
        self.tft.fill_rect(block_w, top, block_w, height, yellow)
        self.tft.fill_rect(block_w * 2, top, config.TFT_WIDTH - block_w * 2, height, green)
        self.tft.vline(block_w, top, height, config.TEXT)
        self.tft.vline(block_w * 2, top, height, config.TEXT)
        self.tft.text("WORK", 8, top + 48, config.TEXT, None, 1)
        self.tft.text("WAIT", block_w + 8, top + 48, config.TEXT, None, 1)
        self.tft.text("IDLE", block_w * 2 + 8, top + 48, config.TEXT, None, 1)

    async def blink_loop(self):
        while True:
            if self.state == STATE_WORKING:
                self.blink_on = not self.blink_on
                self.draw_status()
                await asyncio.sleep_ms(450)
            else:
                if self.blink_on:
                    self.blink_on = False
                    self.draw_status()
                await asyncio.sleep_ms(150)

    def update_snapshot(self, snapshot):
        state = snapshot.get("state")
        if state in (STATE_WORKING, STATE_WAITING, STATE_IDLE):
            self.state = state
        usage = snapshot.get("usage")
        if isinstance(usage, dict):
            self.usage = usage
        if self.state != STATE_WORKING:
            self.blink_on = False
        self.draw_usage()
        self.draw_status()
        gc.collect()


async def connect_wifi(screen=None):
    led = Pin(config.LED_PIN, Pin.OUT)
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)

    while not wlan.isconnected():
        led_off(led)
        if screen:
            screen.show_message("connecting wifi")
        try:
            wlan.disconnect()
        except Exception:
            pass
        await asyncio.sleep_ms(200)

        wlan.connect(config.WIFI_SSID, config.WIFI_PASSWORD)
        start = time.time()
        while not wlan.isconnected() and time.time() - start <= 20:
            led_toggle(led)
            await asyncio.sleep_ms(120)

        if not wlan.isconnected():
            led_off(led)
            if screen:
                screen.show_message("wifi retry")
            await asyncio.sleep(2)

    led_on(led)
    print("WiFi:", wlan.ifconfig())
    if screen:
        screen.show_message("waiting for PC client")
    return wlan, led


async def wifi_watch_loop(wlan, led, screen):
    while True:
        if wlan.isconnected():
            led_on(led)
            await asyncio.sleep(2)
            continue

        print("WiFi disconnected")
        if screen:
            screen.show_message("wifi reconnect")

        while not wlan.isconnected():
            try:
                wlan.disconnect()
            except Exception:
                pass
            await asyncio.sleep_ms(200)
            wlan.connect(config.WIFI_SSID, config.WIFI_PASSWORD)

            start = time.time()
            while not wlan.isconnected() and time.time() - start <= 15:
                led_toggle(led)
                await asyncio.sleep_ms(120)

            if not wlan.isconnected():
                led_off(led)
                await asyncio.sleep(2)

        led_on(led)
        print("WiFi reconnected:", wlan.ifconfig())
        if screen:
            screen.show_message("waiting for PC client")


async def handle_client(reader, writer, screen):
    try:
        line = await reader.readline()
        if line:
            screen.update_snapshot(json.loads(line))
        writer.write(b'{"ok":true}\n')
        await writer.drain()
    except Exception as exc:
        print("Client error:", exc)
        try:
            writer.write(b'{"ok":false}\n')
            await writer.drain()
        except Exception:
            pass
    finally:
        writer.close()
        if hasattr(writer, "wait_closed"):
            await writer.wait_closed()


async def main():
    screen = CodexScreen()
    wlan, led = await connect_wifi(screen)
    asyncio.create_task(screen.blink_loop())
    asyncio.create_task(wifi_watch_loop(wlan, led, screen))
    await asyncio.start_server(
        lambda reader, writer: handle_client(reader, writer, screen),
        "0.0.0.0",
        config.LISTEN_PORT,
    )
    print("Listening on 0.0.0.0:%d" % config.LISTEN_PORT)
    while True:
        await asyncio.sleep(3600)


asyncio.run(main())
