from machine import Pin


class TPIC6B595Chain:
    """
    Driver for daisy-chained TPIC6B595 power DMOS shift registers.
    Default pin mappings correspond to Raspberry Pi Pico physical header pins:
      - Pin 1  -> RP2040 GPIO 0 (\bar{SRCLR})
      - Pin 2  -> RP2040 GPIO 1 (\bar{G})
      - Pin 4  -> RP2040 GPIO 2 (S_ck)
      - Pin 5  -> RP2040 GPIO 3 (S_in)
      - Pin 6  -> RP2040 GPIO 4 (R_ck)
    """

    def __init__(
        self,
        srclr_pin: int = 0,
        g_pin: int = 1,
        sck_pin: int = 2,
        sin_pin: int = 3,
        rck_pin: int = 4,
        num_devices: int = 8,
    ):
        self.num_devices = num_devices
        self.num_bits = num_devices * 8

        # Output enable (\bar{G}): active-low.
        # Initialize HIGH (disabled) for fail-safe power-on state.
        self.pin_g = Pin(g_pin, Pin.OUT, value=1)

        # Shift register clear (\bar{SRCLR}): active-low.
        # Initialize HIGH (not clearing).
        self.pin_srclr = Pin(srclr_pin, Pin.OUT, value=1)

        # Shift register clock (S_ck): rising-edge active.
        self.pin_sck = Pin(sck_pin, Pin.OUT, value=0)

        # Serial data input (S_in).
        self.pin_sin = Pin(sin_pin, Pin.OUT, value=0)

        # Storage / latch clock (R_ck): rising-edge active.
        self.pin_rck = Pin(rck_pin, Pin.OUT, value=0)

        # Clear shift register stages and latch cleared output.
        self.clear()

    def set_enabled(self, enabled: bool) -> None:
        """Enable (True -> \bar{G} LOW) or disable (False -> \bar{G} HIGH) outputs."""
        self.pin_g.value(0 if enabled else 1)

    def is_enabled(self) -> bool:
        """Return True if outputs are enabled (\bar{G} is LOW)."""
        return self.pin_g.value() == 0

    def clear(self) -> None:
        """Pulse \bar{SRCLR} low to reset internal shift register stages and update latch."""
        self.pin_srclr.value(0)
        self.pin_srclr.value(1)
        self.latch()

    def latch(self) -> None:
        """Pulse R_ck (LOW -> HIGH -> LOW) to transfer shift register to storage register."""
        self.pin_rck.value(1)
        self.pin_rck.value(0)

    def write(self, data: bytes) -> None:
        """
        Shift out bytes (MSB first) across the daisy chain and latch to outputs.
        Expects len(data) == num_devices (8 bytes for 64 bits).
        """
        if len(data) != self.num_devices:
            raise ValueError(f"Expected {self.num_devices} bytes, got {len(data)}")

        sin_val = self.pin_sin.value
        sck_val = self.pin_sck.value

        for b in data:
            for bit_idx in range(7, -1, -1):
                sin_val((b >> bit_idx) & 1)
                sck_val(1)
                sck_val(0)

        self.latch()

    def write_int(self, value: int) -> None:
        """Write integer value (64-bit integer, MSB first) to the shift registers."""
        data = value.to_bytes(self.num_devices, "big")
        self.write(data)
