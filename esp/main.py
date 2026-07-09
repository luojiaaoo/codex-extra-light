import gc
import time

import network
import uasyncio as asyncio
import ujson as json

import config
from tft_display import TFT


STATE_IDLE = "idle"
STATE_WAITING = "waiting"
STATE_WORKING = "working"


class CodexScreen:
    def __init__(self):
        self.tft = TFT(config)
        self.state = STATE_IDLE
        self.ip_address = None
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

    def set_ip(self, ip_address):
        self.ip_address = ip_address
        self.draw_usage()

    def draw_all(self):
        self.tft.fill(config.BACKGROUND)
        self.draw_usage()
        self.draw_status()

    def draw_ip(self):
        if self.ip_address:
            text = "IP " + self.ip_address + ":" + str(config.LISTEN_PORT)
        else:
            text = "IP connecting"
        self.tft.text(text[:29], 8, 184, config.TEXT, config.BACKGROUND, 1)

    def draw_usage(self):
        self.tft.fill_rect(0, 0, config.TFT_WIDTH, 198, config.BACKGROUND)
        self.tft.text("CODEX", 8, 10, config.TEXT, config.BACKGROUND, 3)
        if self.usage.get("error"):
            self.tft.text("usage error", 8, 52, config.BRIGHT_YELLOW, config.BACKGROUND, 2)
            self.tft.text(str(self.usage.get("error"))[:25], 8, 78, config.TEXT, config.BACKGROUND, 1)
            self.draw_ip()
            return

        plan = self.usage.get("plan_type") or "unknown"
        five = self.percent_text(self.usage.get("five_hour_percent"))
        week = self.percent_text(self.usage.get("week_percent"))
        updated = self.short_time(self.usage.get("updated_at"))

        self.tft.text("PLAN " + str(plan)[:12], 8, 52, config.TEXT, config.BACKGROUND, 2)
        self.tft.text("5H   " + five, 8, 84, config.TEXT, config.BACKGROUND, 3)
        self.tft.text("WEEK " + week, 8, 122, config.TEXT, config.BACKGROUND, 3)
        self.tft.text("UPD " + updated, 8, 166, config.TEXT, config.BACKGROUND, 1)
        self.draw_ip()

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
        width = config.TFT_WIDTH

        if self.state == STATE_WORKING:
            color = config.BRIGHT_RED if self.blink_on else config.DIM_RED
            label = "WORKING"
        elif self.state == STATE_WAITING:
            color = config.BRIGHT_YELLOW
            label = "WAITING"
        else:
            color = config.BRIGHT_GREEN
            label = "IDLE"

        self.tft.fill_rect(0, top, width, height, color)
        self.tft.text(label, 8, top + 48, config.TEXT, None, 2)

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
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)

    while not wlan.isconnected():
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
            await asyncio.sleep_ms(120)

        if not wlan.isconnected():
            if screen:
                screen.show_message("wifi retry")
            await asyncio.sleep(2)

    ifconfig = wlan.ifconfig()
    print("WiFi:", ifconfig)
    if screen:
        screen.set_ip(ifconfig[0])
        screen.show_message("waiting for PC client")
    return wlan


async def wifi_watch_loop(wlan, screen):
    while True:
        if wlan.isconnected():
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
                await asyncio.sleep_ms(120)

            if not wlan.isconnected():
                await asyncio.sleep(2)

        ifconfig = wlan.ifconfig()
        print("WiFi reconnected:", ifconfig)
        if screen:
            screen.set_ip(ifconfig[0])
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
    wlan = await connect_wifi(screen)
    asyncio.create_task(screen.blink_loop())
    asyncio.create_task(wifi_watch_loop(wlan, screen))
    await asyncio.start_server(
        lambda reader, writer: handle_client(reader, writer, screen),
        "0.0.0.0",
        config.LISTEN_PORT,
    )
    print("Listening on 0.0.0.0:%d" % config.LISTEN_PORT)
    while True:
        await asyncio.sleep(3600)


asyncio.run(main())
