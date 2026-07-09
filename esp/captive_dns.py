import socket
import uasyncio as asyncio


DNS_PORT = 53


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


async def loop(ip_address):
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
