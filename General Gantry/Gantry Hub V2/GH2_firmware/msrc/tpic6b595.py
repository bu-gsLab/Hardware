try:
    from machine import Pin
except ImportError:
    Pin = None


# Default channel mapping based on schematic "valve_block_interface.kicad_sch".
# Connectors:
#   Bank 0 -> J6A (schematic nets CHANNEL_33 .. CHANNEL_48)
#   Bank 1 -> J6B (schematic nets CHANNEL_49 .. CHANNEL_64)
#   Bank 2 -> J2A (schematic nets CHANNEL_01 .. CHANNEL_16)
#   Bank 3 -> J2B (schematic nets CHANNEL_17 .. CHANNEL_32)
#
# Daisy chain order from Pico S_IN:
#   Chip 0: U9  (bits 0-7,   DRAIN0-DRAIN7)
#   Chip 1: U8  (bits 8-15,  DRAIN0-DRAIN7)
#   Chip 2: U4  (bits 16-23, DRAIN0-DRAIN7)
#   Chip 3: U1  (bits 24-31, DRAIN0-DRAIN7)
#   Chip 4: U13 (bits 32-39, DRAIN0-DRAIN7)
#   Chip 5: U12 (bits 40-47, DRAIN0-DRAIN7)
#   Chip 6: U11 (bits 48-55, DRAIN0-DRAIN7)
#   Chip 7: U10 (bits 56-63, DRAIN0-DRAIN7)

BANK_0_J6A_MAP = [33, 35, 36, 38, 41, 44, 49, 52, 54, 57, 59, 60, 62, 43, 46, 51]
BANK_1_J6B_MAP = [32, 34, 37, 39, 40, 45, 48, 53, 55, 56, 58, 61, 63, 42, 47, 50]
BANK_2_J2A_MAP = [1, 3, 4, 6, 9, 12, 17, 20, 22, 25, 27, 28, 30, 11, 14, 19]
BANK_3_J2B_MAP = [0, 2, 5, 7, 8, 13, 16, 21, 23, 24, 26, 29, 31, 10, 15, 18]

DEFAULT_CHANNEL_MAP = BANK_0_J6A_MAP + BANK_1_J6B_MAP + BANK_2_J2A_MAP + BANK_3_J2B_MAP

# Legacy / simple pass-through mapping
PASS_THROUGH_CHANNEL_MAP = [bank * 16 + ch for bank in range(4) for ch in range(16)]


def map_bank_channel_to_bit(bank: int, channel: int, mapping=None) -> int:
    """
    Translate bank (0-3) and channel (0-15) to bit position (0-63) in 64-bit shift register pattern.

    Default mapping matches valve_block_interface.kicad_sch:
      - Bank 0: Connector J6A (nets CHANNEL_33..CHANNEL_48)
      - Bank 1: Connector J6B (nets CHANNEL_49..CHANNEL_64)
      - Bank 2: Connector J2A (nets CHANNEL_01..CHANNEL_16)
      - Bank 3: Connector J2B (nets CHANNEL_17..CHANNEL_32)
    """
    if not 0 <= bank < 4:
        raise ValueError(f"Invalid bank {bank}. Must be in range 0-3.")
    if not 0 <= channel < 16:
        raise ValueError(f"Invalid channel {channel}. Must be in range 0-15.")
    if mapping is None:
        mapping = DEFAULT_CHANNEL_MAP
    if callable(mapping):
        return mapping(bank, channel)
    return mapping[bank * 16 + channel]


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
        channel_map=None,
    ):
        self.num_devices = num_devices
        self.num_bits = num_devices * 8
        self.num_banks = self.num_bits // 16  # 4 banks for 64 bits
        self.channels_per_bank = 16
        self.channel_map = channel_map if channel_map is not None else DEFAULT_CHANNEL_MAP

        # Stored current state (64-bit pattern integer)
        self.state = 0

        if Pin is not None:
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
        else:
            self.pin_g = None
            self.pin_srclr = None
            self.pin_sck = None
            self.pin_sin = None
            self.pin_rck = None

        # Clear shift register stages and latch cleared output.
        self.clear()

    def set_enabled(self, enabled: bool) -> None:
        """Enable (True -> \bar{G} LOW) or disable (False -> \bar{G} HIGH) outputs."""
        if self.pin_g is not None:
            self.pin_g.value(0 if enabled else 1)

    def is_enabled(self) -> bool:
        """Return True if outputs are enabled (\bar{G} is LOW)."""
        if self.pin_g is not None:
            return self.pin_g.value() == 0
        return False

    def clear(self) -> None:
        """Pulse \bar{SRCLR} low to reset internal shift register stages and update latch."""
        self.state = 0
        if self.pin_srclr is not None:
            self.pin_srclr.value(0)
            self.pin_srclr.value(1)
        self.latch()

    def latch(self) -> None:
        """Pulse R_ck (LOW -> HIGH -> LOW) to transfer shift register to storage register."""
        if self.pin_rck is not None:
            self.pin_rck.value(1)
            self.pin_rck.value(0)

    def write(self, data: bytes) -> None:
        """
        Shift out bytes (MSB first) across the daisy chain and latch to outputs.
        Expects len(data) == num_devices (8 bytes for 64 bits).
        """
        if len(data) != self.num_devices:
            raise ValueError(f"Expected {self.num_devices} bytes, got {len(data)}")

        self.state = int.from_bytes(data, "big")

        if self.pin_sin is not None and self.pin_sck is not None:
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
        if not 0 <= value <= (1 << self.num_bits) - 1:
            raise ValueError(f"Value 0x{value:X} out of {self.num_bits}-bit range")
        data = value.to_bytes(self.num_devices, "big")
        self.write(data)

    def map_channel(self, bank: int, channel: int) -> int:
        """Translate (bank, channel) to bit position in 64-bit pattern using configured mapping."""
        return map_bank_channel_to_bit(bank, channel, self.channel_map)

    def write_bank(self, bank: int, value: int) -> None:
        """
        Update a single 16-bit bank and write new 64-bit pattern to shift registers.

        :param bank: Bank index (0-3).
        :param value: 16-bit unsigned integer (0-65535).
        """
        if not 0 <= bank < self.num_banks:
            raise ValueError(f"Invalid bank {bank}. Must be in range 0-{self.num_banks - 1}.")
        if not 0 <= value <= 0xFFFF:
            raise ValueError(f"Value 0x{value:X} out of 16-bit range (0-65535).")

        new_state = self.state
        for c in range(self.channels_per_bank):
            bit_val = (value >> c) & 1
            bit_pos = self.map_channel(bank, c)
            if bit_val:
                new_state |= (1 << bit_pos)
            else:
                new_state &= ~(1 << bit_pos)

        self.write_int(new_state)

    def set_channel(self, bank: int, channel: int, state: bool | int) -> None:
        """
        Update a single channel within a bank and write new 64-bit pattern to shift registers.

        :param bank: Bank index (0-3).
        :param channel: Channel index within bank (0-15).
        :param state: True/1 for HIGH, False/0 for LOW.
        """
        if not 0 <= bank < self.num_banks:
            raise ValueError(f"Invalid bank {bank}. Must be in range 0-{self.num_banks - 1}.")
        if not 0 <= channel < self.channels_per_bank:
            raise ValueError(f"Invalid channel {channel}. Must be in range 0-{self.channels_per_bank - 1}.")

        bit_pos = self.map_channel(bank, channel)
        new_state = self.state
        if state:
            new_state |= (1 << bit_pos)
        else:
            new_state &= ~(1 << bit_pos)

        self.write_int(new_state)

    def get_channel(self, bank: int, channel: int) -> int:
        """Return the state (0 or 1) of the specified channel in the specified bank."""
        bit_pos = self.map_channel(bank, channel)
        return (self.state >> bit_pos) & 1

    def get_bank(self, bank: int) -> int:
        """Return the 16-bit value of the specified bank based on mapped bits."""
        if not 0 <= bank < self.num_banks:
            raise ValueError(f"Invalid bank {bank}. Must be in range 0-{self.num_banks - 1}.")
        val = 0
        for c in range(self.channels_per_bank):
            bit_pos = self.map_channel(bank, c)
            if (self.state >> bit_pos) & 1:
                val |= (1 << c)
        return val
