from machine import Pin, SPI
import framebuf
import time


def color565(red, green, blue):
    return ((red & 0xF8) << 8) | ((green & 0xFC) << 3) | (blue >> 3)


class TFT:
    def __init__(self, config):
        self.width = config.TFT_WIDTH
        self.height = config.TFT_HEIGHT
        self.driver = config.TFT_DRIVER
        self.rotation = config.TFT_ROTATION
        self.spi = SPI(
            1,
            baudrate=26000000,
            polarity=0,
            phase=0,
            sck=Pin(config.PIN_SCK),
            mosi=Pin(config.PIN_MOSI),
        )
        self.cs = Pin(config.PIN_CS, Pin.OUT, value=1)
        self.dc = Pin(config.PIN_DC, Pin.OUT, value=0)
        self.rst = Pin(config.PIN_RST, Pin.OUT, value=1)
        self.init()

    def init(self):
        self.reset()
        if self.driver == "ili9341":
            self.init_ili9341()
        else:
            self.init_st7789()

    def reset(self):
        self.rst(1)
        time.sleep_ms(50)
        self.rst(0)
        time.sleep_ms(50)
        self.rst(1)
        time.sleep_ms(150)

    def command(self, command, data=None):
        self.cs(0)
        self.dc(0)
        self.spi.write(bytes([command]))
        if data:
            self.dc(1)
            self.spi.write(data)
        self.cs(1)

    def init_st7789(self):
        self.command(0x01)
        time.sleep_ms(150)
        self.command(0x11)
        time.sleep_ms(120)
        self.command(0x3A, b"\x55")
        self.command(0x36, self.madctl())
        self.command(0x21)
        self.command(0x13)
        self.command(0x29)
        time.sleep_ms(50)

    def init_ili9341(self):
        self.command(0x01)
        time.sleep_ms(150)
        self.command(0x28)
        self.command(0x3A, b"\x55")
        self.command(0x36, self.madctl())
        self.command(0x11)
        time.sleep_ms(120)
        self.command(0x29)
        time.sleep_ms(50)

    def madctl(self):
        values = [0x00, 0x60, 0xC0, 0xA0]
        return bytes([values[self.rotation % 4]])

    def set_window(self, x0, y0, x1, y1):
        self.command(
            0x2A,
            bytes([(x0 >> 8) & 0xFF, x0 & 0xFF, (x1 >> 8) & 0xFF, x1 & 0xFF]),
        )
        self.command(
            0x2B,
            bytes([(y0 >> 8) & 0xFF, y0 & 0xFF, (y1 >> 8) & 0xFF, y1 & 0xFF]),
        )
        self.command(0x2C)

    def fill(self, color):
        self.fill_rect(0, 0, self.width, self.height, color)

    def fill_rect(self, x, y, width, height, color):
        if width <= 0 or height <= 0:
            return
        x = max(0, min(self.width - 1, x))
        y = max(0, min(self.height - 1, y))
        width = min(width, self.width - x)
        height = min(height, self.height - y)
        hi = (color >> 8) & 0xFF
        lo = color & 0xFF
        self.set_window(x, y, x + width - 1, y + height - 1)
        self.cs(0)
        self.dc(1)
        remaining = width * height
        chunk_pixels = min(remaining, 512)
        chunk = bytes([hi, lo]) * chunk_pixels
        while remaining:
            count = min(remaining, chunk_pixels)
            self.spi.write(chunk[: count * 2])
            remaining -= count
        self.cs(1)

    def hline(self, x, y, width, color):
        self.fill_rect(x, y, width, 1, color)

    def vline(self, x, y, height, color):
        self.fill_rect(x, y, 1, height, color)

    def text(self, text, x, y, color, background=None, scale=1):
        text = str(text)
        if not text:
            return
        char_w = 8
        char_h = 8
        width = len(text) * char_w
        height = char_h
        if background is not None:
            self.fill_rect(x, y, width * scale, height * scale, background)
        buf = bytearray((width * height + 7) // 8)
        fb = framebuf.FrameBuffer(buf, width, height, framebuf.MONO_HLSB)
        fb.fill(0)
        fb.text(text, 0, 0, 1)
        for yy in range(height):
            for xx in range(width):
                index = yy * width + xx
                if buf[index >> 3] & (1 << (index & 7)):
                    self.fill_rect(x + xx * scale, y + yy * scale, scale, scale, color)
