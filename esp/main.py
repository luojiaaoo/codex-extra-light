import gc
import machine
import time

import network
import uasyncio as asyncio
import ujson as json

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
        else:
            self.tft.text("CONNECTING WIFI", 8, 96, config.TEXT, config.BACKGROUND, 2)
        self.tft.fill_rect(0, 250, config.TFT_WIDTH, config.TFT_HEIGHT - 250, config.DIM_RED)
        self.tft.text("SETUP MODE", 8, 282, config.TEXT, None, 2)

    def draw_countdown(self):
        if self.setup_remaining is None:
            text = "LEFT: 30 sec"
        else:
            text = "LEFT: " + str(self.setup_remaining) + " sec"
        self.tft.fill_rect(8, 188, 190, 20, config.BACKGROUND)
        self.tft.text(text, 8, 188, config.TEXT, config.BACKGROUND, 2)


async def connect_wifi(screen=None):
    wlan = network.WLAN(network.STA_IF)
    stop_setup_ap()
    wlan.active(True)
    try:
        wlan.disconnect()
    except Exception:
        pass
    await asyncio.sleep_ms(300)

    while True:
        creds = load_wifi_credentials()
        if not creds:
            await start_setup_portal(wlan, screen, "no wifi config")

        if await try_wifi_once(wlan, creds, screen):
            break
        await start_setup_portal(wlan, screen, "wifi setup needed")

    ifconfig = wlan.ifconfig()
    print("WiFi:", ifconfig)
    stop_setup_ap()
    if screen:
        screen.set_ip(ifconfig[0])
        screen.show_message("waiting for PC client")
    return wlan


async def try_wifi_once(wlan, creds, screen=None):
    timeout = getattr(config, "WIFI_CONNECT_TIMEOUT", 30)
    ssid = creds["ssid"]
    if screen and hasattr(screen, "show_wifi_connecting"):
        screen.show_wifi_connecting(ssid, timeout)
    elif screen:
        screen.show_message("WIFI Connecting")

    try:
        wlan.disconnect()
    except Exception:
        pass
    stop_setup_ap()
    await asyncio.sleep_ms(200)

    print("WiFi connecting:", ssid)
    wlan.connect(ssid, creds.get("password", ""))
    start = time.time()
    last_remaining = timeout
    while not wlan.isconnected() and time.time() - start <= timeout:
        remaining = timeout - int(time.time() - start)
        if remaining != last_remaining:
            last_remaining = remaining
            if screen and hasattr(screen, "show_wifi_connecting"):
                screen.show_wifi_connecting(ssid, max(remaining, 0))
        await asyncio.sleep_ms(120)
    if wlan.isconnected():
        return True

    print("WiFi connect timeout:", ssid)
    if screen:
        screen.show_message("wifi connect failed")
    return False


async def wifi_watch_loop(wlan, screen):
    while True:
        if wlan.isconnected():
            await asyncio.sleep(2)
            continue

        print("WiFi disconnected")
        machine.reset()


def load_wifi_credentials():
    try:
        with open(getattr(config, "WIFI_CONFIG_PATH", "wifi_config.json"), "r") as handle:
            creds = json.loads(handle.read())
    except Exception:
        return None

    if not isinstance(creds, dict):
        return None
    ssid = str(creds.get("ssid", "")).strip()
    if not ssid:
        return None
    return {
        "ssid": ssid,
        "password": str(creds.get("password", "")),
    }


def stop_setup_ap():
    try:
        ap = network.WLAN(network.AP_IF)
        if ap.active():
            ap.active(False)
    except Exception:
        pass


async def start_setup_portal(wlan, screen, message):
    import wifi_setup

    await wifi_setup.run(wlan, screen, message)


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
    setup_screen = SetupScreen()
    wlan = await connect_wifi(setup_screen)
    del setup_screen
    gc.collect()

    from codex_screen import CodexScreen

    screen = CodexScreen()
    screen.set_ip(wlan.ifconfig()[0])
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
