import gc
import machine
import time

import network
import uasyncio as asyncio
import ujson as json

import config


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
    if screen:
        screen.show_wifi_connecting(ssid, timeout)

    try:
        wlan.disconnect()
    except Exception:
        pass
    stop_setup_ap()
    await asyncio.sleep_ms(200)

    print("WiFi connecting:", ssid)
    wlan.connect(ssid, creds.get("password", ""))
    deadline = time.ticks_add(time.ticks_ms(), timeout * 1000)
    last_remaining = timeout
    while not wlan.isconnected():
        remaining_ms = time.ticks_diff(deadline, time.ticks_ms())
        if remaining_ms <= 0:
            if screen:
                screen.show_wifi_connecting(ssid, 0)
            break
        remaining = (remaining_ms + 999) // 1000
        if remaining != last_remaining:
            last_remaining = remaining
            if screen:
                screen.show_wifi_connecting(ssid, remaining)
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
    print("Opening setup portal")
    if screen:
        screen.show_opening_hotspot()
        screen.release_display()
    gc.collect()
    import wifi_setup

    print("Setup portal module loaded")
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
        await writer.wait_closed()


async def main():
    from codex_screen import CodexScreen, SetupScreen

    setup_screen = SetupScreen()
    wlan = await connect_wifi(setup_screen)
    del setup_screen
    gc.collect()

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
