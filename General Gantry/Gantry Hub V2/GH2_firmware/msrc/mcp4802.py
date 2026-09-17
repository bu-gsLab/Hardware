from machine import Pin, SPI


class MCP4802:
    """
    Driver for Microchip MCP4802 dual-channel 8-bit DAC via SPI.
    Default pin mappings correspond to Raspberry Pi Pico physical header pins:
      - Pin 12 -> RP2040 GPIO 9 (\bar{CS})
      - Pin 14 -> RP2040 GPIO 10 (SCK, SPI1)
      - Pin 15 -> RP2040 GPIO 11 (SDI / MOSI, SPI1)
    """

    def __init__(
        self,
        spi_id: int = 1,
        sck_pin: int = 10,
        mosi_pin: int = 11,
        cs_pin: int = 9,
        baudrate: int = 10_000_000,
    ):
        # Active-low chip select; initialize HIGH (unselected)
        self.cs = Pin(cs_pin, Pin.OUT, value=1)
        self.spi = SPI(
            spi_id,
            baudrate=baudrate,
            polarity=0,
            phase=0,
            sck=Pin(sck_pin),
            mosi=Pin(mosi_pin),
        )

        # Fail-safe power-on state: set both channels to 0V (active mode)
        self.write(0, 0, gain_2x=False, active=True)
        self.write(1, 0, gain_2x=False, active=True)

    @staticmethod
    def build_command(
        channel: int | str,
        value: int,
        gain_2x: bool = False,
        active: bool = True,
    ) -> int:
        """
        Build 16-bit command word for MCP4802:
        Bit 15: ~A/B (0=A, 1=B)
        Bit 14: Reserved (0)
        Bit 13: ~GA (0=2x gain, 1=1x gain)
        Bit 12: ~SHDN (0=shutdown high-Z, 1=active output)
        Bits 11-4: Data bits D7-D0 (8-bit value)
        Bits 3-0: Reserved (0)
        """
        if isinstance(channel, str):
            ch = channel.strip().upper()
            if ch == "A":
                ch_bit = 0
            elif ch == "B":
                ch_bit = 1
            else:
                raise ValueError(f"Invalid channel string: {channel}")
        else:
            ch_bit = int(channel)
            if ch_bit not in (0, 1):
                raise ValueError(f"Invalid channel: {channel}")

        if not 0 <= value <= 255:
            raise ValueError(f"DAC value {value} out of range (0-255)")

        gain_bit = 0 if gain_2x else 1
        shdn_bit = 1 if active else 0
        cmd_word = (
            (ch_bit << 15)
            | (gain_bit << 13)
            | (shdn_bit << 12)
            | ((value & 0xFF) << 4)
        )
        return cmd_word

    def write(
        self,
        channel: int | str,
        value: int,
        gain_2x: bool = False,
        active: bool = True,
    ) -> None:
        """Assemble command word and transmit over SPI."""
        cmd_word = self.build_command(channel, value, gain_2x=gain_2x, active=active)
        buf = bytes([cmd_word >> 8, cmd_word & 0xFF])
        self.cs.value(0)
        self.spi.write(buf)
        self.cs.value(1)
