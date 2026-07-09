import machine
import network
import socket
import uasyncio as asyncio
import gc
import ujson as json

import config


PORT = 80
DNS_PORT = 53
NETWORKS = []


def ap_name():
    prefix = getattr(config, "SETUP_AP_SSID_PREFIX", "Codex-Setup")
    try:
        mac = network.WLAN(network.STA_IF).config("mac")
        return prefix + "-" + "".join(["%02X" % b for b in mac[-3:]])
    except Exception:
        return prefix


def start_ap():
    ap = network.WLAN(network.AP_IF)
    ap.active(True)
    ssid = ap_name()
    password = getattr(config, "SETUP_AP_PASSWORD", "codex8266")
    if password:
        try:
            ap.config(essid=ssid, authmode=network.AUTH_WPA_WPA2_PSK, password=password)
        except Exception:
            ap.config(essid=ssid, password=password)
    else:
        ap.config(essid=ssid)
    return ap, ssid


def decode_form_value(value):
    value = value.replace("+", " ")
    out = bytearray()
    i = 0
    while i < len(value):
        if value[i] == "%" and i + 2 < len(value):
            try:
                out.append(int(value[i + 1:i + 3], 16))
                i += 3
                continue
            except Exception:
                pass
        out.append(ord(value[i]))
        i += 1
    return bytes(out).decode()


def parse_form(body):
    data = {}
    for pair in body.split("&"):
        if not pair:
            continue
        if "=" in pair:
            key, value = pair.split("=", 1)
        else:
            key, value = pair, ""
        data[decode_form_value(key)] = decode_form_value(value)
    return data


def save_wifi(ssid, password):
    with open(getattr(config, "WIFI_CONFIG_PATH", "wifi_config.json"), "w") as handle:
        handle.write(json.dumps({"ssid": ssid, "password": password}))


def html_escape(value):
    return (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def scan_networks(wlan):
    global NETWORKS
    found = []
    seen = {}
    try:
        wlan.active(True)
        for item in wlan.scan():
            raw_ssid = item[0]
            if not raw_ssid:
                continue
            try:
                ssid = raw_ssid.decode()
            except Exception:
                ssid = str(raw_ssid)
            if not ssid or ssid in seen:
                continue
            seen[ssid] = True
            found.append((ssid, item[3]))
    except Exception as exc:
        print("WiFi scan error:", exc)

    try:
        found.sort(key=lambda item: item[1], reverse=True)
    except Exception:
        pass
    NETWORKS = found[:20]
    gc.collect()
    print("WiFi scan found:", len(NETWORKS))
    return NETWORKS


def ip_bytes(ip_address):
    return bytes([int(part) for part in ip_address.split(".")])


def dns_reply(query, ip_address):
    if len(query) < 12:
        return None
    end = 12
    while end < len(query) and query[end] != 0:
        end += 1
    end += 5
    if end > len(query):
        return None
    question = query[12:end]
    return (
        query[:2]
        + b"\x81\x80"
        + query[4:6]
        + query[4:6]
        + b"\x00\x00\x00\x00"
        + question
        + b"\xc0\x0c\x00\x01\x00\x01\x00\x00\x00\x3c\x00\x04"
        + ip_bytes(ip_address)
    )


async def dns_loop(ip_address):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setblocking(False)
    try:
        sock.bind(("0.0.0.0", DNS_PORT))
        print("Captive DNS on 0.0.0.0:%d" % DNS_PORT)
        while True:
            try:
                query, addr = sock.recvfrom(512)
                reply = dns_reply(query, ip_address)
                if reply:
                    sock.sendto(reply, addr)
            except OSError:
                await asyncio.sleep_ms(40)
    finally:
        sock.close()


async def send_page(writer, message=""):
    writer.write(b"HTTP/1.0 200 OK\r\nContent-Type: text/html\r\nConnection: close\r\n\r\n")
    writer.write(b"<html><head><meta name=viewport content='width=device-width,initial-scale=1'>")
    writer.write(b"<title>WiFi Setup</title></head><body><h2>Codex ESP WiFi</h2>")
    if message:
        writer.write(("<p><b>" + message + "</b></p>").encode())
    writer.write(b"<form method=get action=/refresh><button>Refresh WiFi list</button></form>")
    writer.write(b"<form method=post action=/save>")
    writer.write(b"WiFi:<br><select name=ssid>")
    if NETWORKS:
        for ssid, rssi in NETWORKS:
            text = html_escape(ssid)
            writer.write(("<option value=\"" + text + "\">" + text + " (" + str(rssi) + "dBm)</option>").encode())
    else:
        writer.write(b"<option value=''>No WiFi found</option>")
    writer.write(b"</select><br>")
    writer.write(b"Other SSID:<br><input name=ssid_other><br>")
    writer.write(b"Password:<br><input name=password type=password><br><br>")
    writer.write(b"<button>Save and restart</button></form>")
    writer.write(b"<p>ESP restarts after saving.</p></body></html>")
    await writer.drain()


async def handle_client(reader, writer, wlan):
    try:
        request = await reader.readline()
        if not request:
            return
        parts = request.decode().split()
        method = parts[0] if parts else "GET"
        path = parts[1] if len(parts) > 1 else "/"
        path = path.split("?", 1)[0]
        length = 0
        while True:
            line = await reader.readline()
            if not line or line == b"\r\n":
                break
            line = line.decode()
            if line.lower().startswith("content-length:"):
                try:
                    length = int(line.split(":", 1)[1])
                except Exception:
                    length = 0

        if method == "GET" and path == "/refresh":
            scan_networks(wlan)
            await send_page(writer, "WiFi list refreshed")
            return

        if method == "POST" and path == "/save":
            body = ""
            if length:
                body = (await reader.read(length)).decode()
            form = parse_form(body)
            ssid = form.get("ssid_other", "").strip()
            if not ssid:
                ssid = form.get("ssid", "").strip()
            password = form.get("password", "")
            if not ssid:
                await send_page(writer, "WiFi name required")
                return
            save_wifi(ssid, password)
            await send_page(writer, "Saved. Restarting.")
            await asyncio.sleep_ms(600)
            machine.reset()
            return

        await send_page(writer)
    except Exception as exc:
        print("Setup client error:", exc)
    finally:
        writer.close()
        if hasattr(writer, "wait_closed"):
            await writer.wait_closed()


async def run(wlan, screen=None, message="wifi setup"):
    try:
        wlan.disconnect()
    except Exception:
        pass
    ap, ssid = start_ap()
    ip_address = ap.ifconfig()[0]
    print("Setup AP:", ssid, ip_address)
    if screen:
        password = getattr(config, "SETUP_AP_PASSWORD", "codex8266")
        if hasattr(screen, "show_setup_ap"):
            screen.show_setup_ap(ssid, password, ip_address, message)
        else:
            screen.set_ip(ip_address)
            screen.show_message(message)
    asyncio.create_task(dns_loop(ip_address))
    scan_networks(wlan)
    server = await asyncio.start_server(
        lambda reader, writer: handle_client(reader, writer, wlan),
        "0.0.0.0",
        PORT,
    )
    print("Setup HTTP on 0.0.0.0:%d" % PORT)
    while True:
        _ = server
        await asyncio.sleep(3600)
