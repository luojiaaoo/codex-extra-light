import gc

import config
from tft_display import TFT


STATE_IDLE = "idle"
STATE_WAITING = "waiting"
STATE_WORKING = "working"


class SetupScreen:
    def __init__(self):
        self.tft = TFT(config)
        self.setup_ssid = None
        self.setup_password = None
        self.setup_ip = None
        self.setup_mode = "starting"
        self.setup_message = "starting network"
        self.setup_remaining = None
        self.draw_setup()

    def ensure_tft(self):
        if self.tft is None:
            self.tft = TFT(config)

    def release_display(self):
        self.tft = None
        gc.collect()

    def show_message(self, message):
        self.setup_message = message
        self.draw_setup()

    def set_ip(self, ip_address):
        self.setup_ip = ip_address
        self.draw_setup()

    def show_setup_ap(self, ssid, password, ip_address, message):
        self.setup_mode = "hotspot"
        self.setup_ssid = ssid
        self.setup_password = password or "open"
        self.setup_ip = ip_address
        self.setup_message = message
        self.setup_remaining = None
        self.draw_setup()

    def show_opening_hotspot(self):
        self.setup_mode = "opening_hotspot"
        self.setup_ssid = None
        self.setup_password = None
        self.setup_ip = None
        self.setup_message = "opening hotspot"
        self.setup_remaining = None
        self.draw_setup()

    def show_wifi_connecting(self, ssid, remaining=None):
        partial = self.setup_mode == "connecting" and self.setup_ssid == ssid
        self.setup_mode = "connecting"
        self.setup_ssid = ssid
        self.setup_password = None
        self.setup_ip = None
        self.setup_message = "WIFI Connecting"
        self.setup_remaining = remaining
        if partial:
            self.draw_countdown()
        else:
            self.draw_setup()

    def draw_setup(self):
        self.ensure_tft()
        self.tft.fill(config.BACKGROUND)
        self.tft.text("WIFI SETUP", 8, 10, config.TEXT, config.BACKGROUND, 2)
        self.tft.text(str(self.setup_message)[:27], 8, 44, config.BRIGHT_YELLOW, config.BACKGROUND, 1)
        if self.setup_mode == "hotspot" and self.setup_ssid:
            self.tft.text("HOTSPOT ON", 8, 72, config.BRIGHT_GREEN, config.BACKGROUND, 2)
            self.tft.text("SSID:", 8, 112, config.TEXT, config.BACKGROUND, 1)
            self.tft.text(str(self.setup_ssid)[:25], 8, 130, config.TEXT, config.BACKGROUND, 1)
            self.tft.text("PASS:", 8, 154, config.TEXT, config.BACKGROUND, 1)
            self.tft.text(str(self.setup_password)[:25], 8, 172, config.TEXT, config.BACKGROUND, 1)
            self.tft.text("URL: http://" + str(self.setup_ip or "192.168.4.1"), 8, 202, config.TEXT, config.BACKGROUND, 1)
        elif self.setup_mode == "connecting" and self.setup_ssid:
            self.tft.text("SSID:", 8, 92, config.TEXT, config.BACKGROUND, 1)
            self.tft.text(str(self.setup_ssid)[:25], 8, 112, config.TEXT, config.BACKGROUND, 1)
            self.tft.text("WIFI Connecting", 8, 150, config.BRIGHT_YELLOW, config.BACKGROUND, 2)
            self.draw_countdown()
        elif self.setup_mode == "opening_hotspot":
            self.tft.text("OPENING HOTSPOT", 8, 96, config.BRIGHT_YELLOW, config.BACKGROUND, 2)
            self.tft.text("PLEASE WAIT", 8, 134, config.TEXT, config.BACKGROUND, 2)
        else:
            self.tft.text("CONNECTING WIFI", 8, 96, config.TEXT, config.BACKGROUND, 2)
        self.tft.fill_rect(0, 250, config.TFT_WIDTH, config.TFT_HEIGHT - 250, config.DIM_RED)
        self.tft.text("SETUP MODE", 8, 282, config.TEXT, None, 2)

    def draw_countdown(self):
        self.ensure_tft()
        if self.setup_remaining is None:
            text = "LEFT: 30 sec"
        else:
            text = "LEFT: " + str(self.setup_remaining) + " sec"
        self.tft.fill_rect(8, 188, 190, 20, config.BACKGROUND)
        self.tft.text(text, 8, 188, config.TEXT, config.BACKGROUND, 2)


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
        import uasyncio as asyncio

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
