import sys
import time

try:
    import uselect as select
except ImportError:
    import select

from machine import Pin
from tpic6b595 import TPIC6B595Chain
from mcp4802 import MCP4802
from digital_out import DigitalOutputs
from protocol import ProtocolHandler

# Ensure ticks timing functions are available in both MicroPython and standard CPython environments
if not hasattr(time, "ticks_ms"):
    time.ticks_ms = lambda: int(time.time() * 1000)
if not hasattr(time, "ticks_diff"):
    time.ticks_diff = lambda a, b: a - b


def get_status_led():
    """Attempt to initialize the onboard status LED for visual feedback."""
    try:
        return Pin("LED", Pin.OUT, value=0)
    except (TypeError, ValueError):
        try:
            return Pin(25, Pin.OUT, value=0)
        except Exception:
            return None


def main():
    led = get_status_led()
    if led:
        led.value(1)

    # 1. Initialize 8x TPIC6B595N shift registers in safe disabled state
    # Pico physical pins: \bar{SRCLR} = Pin 1, \bar{G} = Pin 2, S_ck = Pin 4, S_in = Pin 5, R_ck = Pin 6
    # Corresponding RP2040 GPIOs: GP0, GP1, GP2, GP3, GP4
    sr_chain = TPIC6B595Chain(
        srclr_pin=0,
        g_pin=1,
        sck_pin=2,
        sin_pin=3,
        rck_pin=4,
        num_devices=8,
    )

    # 2. Initialize MCP4802 DAC:
    # Pico physical pins: \bar{CS} = Pin 12, SCK = Pin 14, SDI/MOSI = Pin 15
    # Corresponding RP2040 GPIOs: GP9 (\bar{CS}), GP10 (SCK, SPI1), GP11 (MOSI, SPI1)
    dac = MCP4802(
        spi_id=1,
        sck_pin=10,
        mosi_pin=11,
        cs_pin=9,
        baudrate=10_000_000,
    )

    # 3. Initialize 4 digital outputs (channels 0-3):
    # Channel 0 -> RP2040 GPIO 5 (Pico physical Pin 7)
    # Channel 1 -> RP2040 GPIO 6 (Pico physical Pin 9)
    # Channel 2 -> RP2040 GPIO 7 (Pico physical Pin 10)
    # Channel 3 -> RP2040 GPIO 8 (Pico physical Pin 11)
    digital_out = DigitalOutputs(pins=(0, 1, 2, 3))

    # Protocol dispatcher
    handler = ProtocolHandler(sr_chain, dac, digital_out)

    # Set up non-blocking polling on USB serial (stdin)
    poll_obj = select.poll()
    poll_obj.register(sys.stdin, select.POLLIN)

    if led:
        led.value(0)

    buf = []
    MAX_BUF_LEN = 256
    START_SENTINEL = "$"

    # Heartbeat timing: 2 Hz pulse (toggle state every 250 ms -> 500 ms period)
    HEARTBEAT_INTERVAL_MS = 250
    last_heartbeat = time.ticks_ms()

    while True:
        # Update heartbeat LED (2 Hz pulse)
        now = time.ticks_ms()
        if time.ticks_diff(now, last_heartbeat) >= HEARTBEAT_INTERVAL_MS:
            last_heartbeat = now
            if led:
                if hasattr(led, "toggle"):
                    led.toggle()
                else:
                    led.value(0 if led.value() else 1)

        # Non-blocking poll with 10 ms timeout
        events = poll_obj.poll(10)
        for obj, event in events:
            if event & select.POLLIN:
                ch = sys.stdin.read(1)
                if not ch:
                    continue

                # Sentinel character '$' marks the start of a fresh command frame.
                # It immediately clears any stale or partial characters accumulated in the buffer.
                if ch == START_SENTINEL:
                    buf.clear()
                    continue

                if ch in ("\r", "\n"):
                    if buf:
                        cmd_str = "".join(buf)
                        buf.clear()
                        response = handler.process_command(cmd_str)
                        if response:
                            sys.stdout.write(response + "\r\n")
                else:
                    if len(buf) >= MAX_BUF_LEN:
                        # Prevent unbounded buffer growth if newline was lost
                        buf.clear()
                        sys.stdout.write("ERR BUFFER_OVERFLOW\r\n")
                    else:
                        buf.append(ch)


if __name__ == "__main__":
    main()
