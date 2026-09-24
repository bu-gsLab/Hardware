"""GH2 Host API Client Library and CLI verification tool.

Provides high-level USB serial control over the Gantry Hub V2 (GH2)
Raspberry Pi Pico MicroPython firmware.
"""

from __future__ import annotations

import click
import sys
import threading
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

try:
    import serial
except ImportError:
    serial = None  # type: ignore[assignment]


class GH2Error(Exception):
    """Base exception for all GH2 errors."""


class GH2ConnectionError(GH2Error):
    """Raised when a serial connection fails to establish or disconnects."""


class GH2TimeoutError(GH2Error):
    """Raised when a communication timeout occurs waiting for a response."""


class GH2ProtocolError(GH2Error):
    """Raised when the controller returns an ERR response or malformed payload."""


class GH2Controller:
    """PC-side controller for Gantry Hub V2 (GH2) peripheral hardware."""

    # Discrete digital outputs (0-3) mapped directly to RP2040 GPIO lines:
    #   - Output 0 -> RP2040 GPIO 5 (Raspberry Pi Pico physical Pin 7)
    #   - Output 1 -> RP2040 GPIO 6 (Raspberry Pi Pico physical Pin 9)
    #   - Output 2 -> RP2040 GPIO 7 (Raspberry Pi Pico physical Pin 10)
    #   - Output 3 -> RP2040 GPIO 8 (Raspberry Pi Pico physical Pin 11)
    VALID_PINS = (0, 1, 2, 3)

    def __init__(
        self,
        port: Optional[str] = None,
        baudrate: int = 115200,
        timeout: float = 2.0,
        serial_instance: Optional[Any] = None,
    ) -> None:
        """
        Initialize the GH2Controller.

        :param port: Serial port name (e.g. '/dev/ttyACM0' or 'COM3').
        :param baudrate: Baud rate for serial communication (default: 115200).
        :param timeout: Read timeout in seconds (default: 2.0).
        :param serial_instance: Optional pre-configured or mock serial object.
        """
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self._serial = serial_instance
        self._lock = threading.Lock()
        self._is_open = serial_instance is not None and getattr(serial_instance, "is_open", True)

    @property
    def is_open(self) -> bool:
        """Return True if the serial connection is currently open."""
        if self._serial is None:
            return False
        return getattr(self._serial, "is_open", False)

    def open(self) -> None:
        """Open the serial connection if not already open."""
        with self._lock:
            if self._serial is not None and getattr(self._serial, "is_open", False):
                return

            if serial is None:
                raise GH2ConnectionError("pyserial is not installed.")

            if self.port is None:
                raise GH2ConnectionError("No serial port specified.")

            try:
                self._serial = serial.Serial(
                    port=self.port,
                    baudrate=self.baudrate,
                    timeout=self.timeout,
                    write_timeout=self.timeout,
                )
                # Flush initial buffers
                self._serial.reset_input_buffer()
                self._serial.reset_output_buffer()
                self._is_open = True
            except Exception as e:
                raise GH2ConnectionError(f"Failed to open serial port {self.port}: {e}") from e

    def close(self) -> None:
        """Close the serial connection."""
        with self._lock:
            if self._serial is not None:
                try:
                    if getattr(self._serial, "is_open", False):
                        self._serial.close()
                except Exception:
                    pass
                self._serial = None
            self._is_open = False

    def __enter__(self) -> GH2Controller:
        self.open()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()

    START_SENTINEL = "$"

    def send_command(self, command: str) -> str:
        """
        Send an ASCII command to the controller and return the response.

        Commands are framed with a leading '$' sentinel character and terminated with
        newline ('\\r\\n'). The '$' sentinel guarantees that any stale, partial, or corrupted
        characters in the Pico's serial receive buffer are discarded before the new command
        is parsed.

        :param command: Command string without trailing newline (leading '$' optional).
        :return: Response string stripped of trailing newline.
        :raises GH2ConnectionError: If not connected.
        :raises GH2TimeoutError: If response times out.
        :raises GH2ProtocolError: If firmware returns ERR.
        """
        with self._lock:
            if self._serial is None or not getattr(self._serial, "is_open", False):
                raise GH2ConnectionError("Serial port is not open. Call open() first.")

            # Flush any stale unread data from serial input buffer
            if hasattr(self._serial, "reset_input_buffer"):
                try:
                    self._serial.reset_input_buffer()
                except Exception:
                    pass

            raw_cmd = command.strip()
            if not raw_cmd.startswith(self.START_SENTINEL):
                raw_cmd = f"{self.START_SENTINEL}{raw_cmd}"
            cmd_bytes = (raw_cmd + "\r\n").encode("ascii")

            try:
                self._serial.write(cmd_bytes)
                if hasattr(self._serial, "flush"):
                    self._serial.flush()
            except Exception as e:
                raise GH2ConnectionError(f"Serial write failed: {e}") from e

            # Read response, skipping any empty lines that might remain from prior CR/LF
            deadline = time.time() + self.timeout
            response = ""
            while True:
                try:
                    raw_response = self._serial.readline()
                except Exception as e:
                    raise GH2ConnectionError(f"Serial read failed: {e}") from e

                if not raw_response:
                    raise GH2TimeoutError(
                        f"Timeout ({self.timeout}s) waiting for response to command '{command}'"
                    )

                line = raw_response.decode("ascii", errors="replace").strip()
                if line:
                    response = line
                    break

                if time.time() > deadline:
                    raise GH2TimeoutError(
                        f"Timeout ({self.timeout}s) waiting for non-empty response to '{command}'"
                    )

            if response.startswith("ERR"):
                raise GH2ProtocolError(f"Firmware error response for '{command}': {response}")

            return response

    def ping(self) -> bool:
        """
        Send a PING heartbeat command to verify connection.

        :return: True if Pico responds with PONG.
        """
        response = self.send_command("PING")
        return response == "PONG"

    def write_shift_registers(
        self, data: Union[bytes, bytearray, int, Sequence[int]]
    ) -> None:
        """
        Shift 64 bits (8 bytes) into the TPIC6B595N daisy chain and latch to outputs.

        :param data: 8-byte bytes/bytearray, 64-bit integer, or sequence of 8 integers (0-255).
        """
        if isinstance(data, (bytes, bytearray)):
            if len(data) != 8:
                raise ValueError(f"Shift register data must be exactly 8 bytes (got {len(data)})")
            raw_bytes = bytes(data)
        elif isinstance(data, int):
            if not 0 <= data <= 0xFFFFFFFFFFFFFFFF:
                raise ValueError(f"Shift register integer value 0x{data:X} out of 64-bit range")
            raw_bytes = data.to_bytes(8, "big")
        elif isinstance(data, (list, tuple)):
            if len(data) != 8:
                raise ValueError(f"Shift register byte list must have 8 elements (got {len(data)})")
            for b in data:
                if not 0 <= b <= 255:
                    raise ValueError(f"Byte value {b} out of range (0-255)")
            raw_bytes = bytes(data)
        else:
            raise TypeError(
                f"Unsupported data type for shift registers: {type(data).__name__}. "
                "Expected bytes, int, or list of 8 ints."
            )

        hex_str = raw_bytes.hex().upper()
        resp = self.send_command(f"SR_WRITE {hex_str}")
        if resp != "OK":
            raise GH2ProtocolError(f"Unexpected response to SR_WRITE: {resp}")

    def write_shift_register_bank(
        self, bank: int, value: Union[int, str, bytes, bytearray]
    ) -> None:
        """
        Write a 16-bit word to a shift register bank (0-3).

        Updates only the specified 16-bit bank while preserving other banks' states.

        :param bank: Shift register bank index (0, 1, 2, or 3).
        :param value: 16-bit value as an int (0-65535), hex string, or 2-byte bytes/bytearray.
        :raises ValueError: If bank or value is out of range.
        :raises GH2ProtocolError: If firmware returns an error.
        """
        if bank not in (0, 1, 2, 3):
            raise ValueError(f"Invalid bank {bank}. Bank must be 0, 1, 2, or 3.")

        if isinstance(value, (bytes, bytearray)):
            if len(value) != 2:
                raise ValueError(f"Bank bytes must be exactly 2 bytes (got {len(value)})")
            val_int = int.from_bytes(value, "big")
        elif isinstance(value, str):
            val_str = value.strip()
            if val_str.lower().startswith("0x"):
                val_int = int(val_str, 16)
            elif len(val_str) == 4 and all(c in "0123456789abcdefABCDEF" for c in val_str):
                val_int = int(val_str, 16)
            else:
                val_int = int(val_str, 10)
        elif isinstance(value, int):
            val_int = value
        else:
            raise TypeError(
                f"Unsupported value type for bank write: {type(value).__name__}. "
                "Expected int, str, or 2-byte bytes."
            )

        if not 0 <= val_int <= 0xFFFF:
            raise ValueError(f"Bank value {val_int} out of 16-bit range (0-65535)")

        resp = self.send_command(f"SR_WRITE_BANK {bank} 0x{val_int:04X}")
        if resp != "OK":
            raise GH2ProtocolError(f"Unexpected response to SR_WRITE_BANK: {resp}")

    def set_shift_register_channel(
        self, bank: int, channel: int, state: Union[bool, int]
    ) -> None:
        """
        Set a single channel within a bank (0-3) to HIGH (1) or LOW (0).

        Updates only the specified channel while preserving all other channels.

        :param bank: Shift register bank index (0, 1, 2, or 3).
        :param channel: Channel index within bank (0-15).
        :param state: True/1 for HIGH, False/0 for LOW.
        :raises ValueError: If bank or channel is out of range.
        :raises GH2ProtocolError: If firmware returns an error.
        """
        if bank not in (0, 1, 2, 3):
            raise ValueError(f"Invalid bank {bank}. Bank must be 0, 1, 2, or 3.")
        if not 0 <= channel < 16:
            raise ValueError(f"Invalid channel {channel}. Channel must be 0-15.")

        val_arg = "1" if state else "0"
        resp = self.send_command(f"SR_WRITE_CHANNEL {bank} {channel} {val_arg}")
        if resp != "OK":
            raise GH2ProtocolError(f"Unexpected response to SR_WRITE_CHANNEL: {resp}")

    def get_shift_register_state(self) -> int:
        """Read back the 64-bit shift register pattern from firmware."""
        resp = self.send_command("SR_GET")
        tokens = resp.split()
        if len(tokens) == 2 and tokens[0] == "OK":
            return int(tokens[1], 16)
        raise GH2ProtocolError(f"Unexpected response to SR_GET: {resp}")

    def get_shift_register_bank(self, bank: int) -> int:
        """Read back the 16-bit value of a shift register bank."""
        if bank not in (0, 1, 2, 3):
            raise ValueError(f"Invalid bank {bank}. Bank must be 0, 1, 2, or 3.")
        resp = self.send_command(f"SR_GET {bank}")
        tokens = resp.split()
        if len(tokens) == 2 and tokens[0] == "OK":
            parts = tokens[1].split(":", 1)
            return int(parts[1], 16)
        raise GH2ProtocolError(f"Unexpected response to SR_GET: {resp}")

    def get_shift_register_channel(self, bank: int, channel: int) -> int:
        """Read back the state (0 or 1) of a channel within a bank."""
        if bank not in (0, 1, 2, 3):
            raise ValueError(f"Invalid bank {bank}. Bank must be 0, 1, 2, or 3.")
        if not 0 <= channel < 16:
            raise ValueError(f"Invalid channel {channel}. Channel must be 0-15.")
        resp = self.send_command(f"SR_GET {bank} {channel}")
        tokens = resp.split()
        if len(tokens) == 2 and tokens[0] == "OK":
            parts = tokens[1].split(":")
            return int(parts[2])
        raise GH2ProtocolError(f"Unexpected response to SR_GET: {resp}")

    def enable_shift_registers(self, enabled: bool = True) -> None:
        """
        Control the active-low output enable (\\bar{G}) on the TPIC6B595N chain.

        :param enabled: True to enable outputs (pin LOW), False to disable (pin HIGH).
        """
        arg = "1" if enabled else "0"
        resp = self.send_command(f"SR_ENABLE {arg}")
        if resp != "OK":
            raise GH2ProtocolError(f"Unexpected response to SR_ENABLE: {resp}")

    def clear_shift_registers(self) -> None:
        """Pulse \\bar{SRCLR} low to reset internal shift register stages."""
        resp = self.send_command("SR_CLEAR")
        if resp != "OK":
            raise GH2ProtocolError(f"Unexpected response to SR_CLEAR: {resp}")

    def set_dac(
        self,
        channel: Union[int, str],
        value: int,
        gain_2x: bool = False,
        active: bool = True,
    ) -> None:
        """
        Configure and write an 8-bit output voltage to the MCP4802 DAC.

        :param channel: 0 or 'A' for Channel A; 1 or 'B' for Channel B.
        :param value: 8-bit DAC code (0-255).
        :param gain_2x: False for 1x gain (0-2.048V), True for 2x gain (0-4.096V).
        :param active: True for active output, False for high-Z shutdown.
        """
        if isinstance(channel, str):
            ch_str = channel.strip().upper()
            if ch_str == "A":
                ch_num = 0
            elif ch_str == "B":
                ch_num = 1
            else:
                raise ValueError(f"Invalid channel string: {channel}. Expected 'A' or 'B'.")
        else:
            ch_num = int(channel)
            if ch_num not in (0, 1):
                raise ValueError(f"Invalid channel number: {channel}. Expected 0 or 1.")

        if not 0 <= value <= 255:
            raise ValueError(f"DAC value {value} out of range (0-255)")

        gain_arg = "1" if gain_2x else "0"
        active_arg = "1" if active else "0"
        resp = self.send_command(f"DAC_WRITE {ch_num} {value} {gain_arg} {active_arg}")
        if resp != "OK":
            raise GH2ProtocolError(f"Unexpected response to DAC_WRITE: {resp}")

    def set_digital_output(self, pin: int, state: Union[bool, int]) -> None:
        """
        Set the digital state of output channel 0, 1, 2, or 3.

        Pin mapping to RP2040 GPIOs / Pico physical pins:
          - Channel 0: RP2040 GPIO 5 (Pico physical Pin 7)
          - Channel 1: RP2040 GPIO 6 (Pico physical Pin 9)
          - Channel 2: RP2040 GPIO 7 (Pico physical Pin 10)
          - Channel 3: RP2040 GPIO 8 (Pico physical Pin 11)

        :param pin: Digital output channel number (0, 1, 2, or 3).
        :param state: True/1 for HIGH, False/0 for LOW.
        """
        if pin not in self.VALID_PINS:
            raise ValueError(f"Invalid pin {pin}. Valid pins are {self.VALID_PINS}")
        val_arg = "1" if state else "0"
        resp = self.send_command(f"DOUT_SET {pin} {val_arg}")
        if resp != "OK":
            raise GH2ProtocolError(f"Unexpected response to DOUT_SET: {resp}")

    def toggle_digital_output(self, pin: int) -> int:
        """
        Toggle the digital state of output channel 0, 1, 2, or 3.

        Pin mapping to RP2040 GPIOs / Pico physical pins:
          - Channel 0: RP2040 GPIO 5 (Pico physical Pin 7)
          - Channel 1: RP2040 GPIO 6 (Pico physical Pin 9)
          - Channel 2: RP2040 GPIO 7 (Pico physical Pin 10)
          - Channel 3: RP2040 GPIO 8 (Pico physical Pin 11)

        :param pin: Digital output channel number (0, 1, 2, or 3).
        :return: New state (0 or 1).
        """
        if pin not in self.VALID_PINS:
            raise ValueError(f"Invalid pin {pin}. Valid pins are {self.VALID_PINS}")
        resp = self.send_command(f"DOUT_TOGGLE {pin}")
        tokens = resp.split()
        if len(tokens) == 2 and tokens[0] == "OK":
            try:
                return int(tokens[1])
            except ValueError:
                pass
        raise GH2ProtocolError(f"Unexpected response to DOUT_TOGGLE: {resp}")

    def get_digital_output(self, pin: int) -> int:
        """
        Read the state of a single digital output channel (0, 1, 2, or 3).

        Pin mapping to RP2040 GPIOs / Pico physical pins:
          - Channel 0: RP2040 GPIO 5 (Pico physical Pin 7)
          - Channel 1: RP2040 GPIO 6 (Pico physical Pin 9)
          - Channel 2: RP2040 GPIO 7 (Pico physical Pin 10)
          - Channel 3: RP2040 GPIO 8 (Pico physical Pin 11)

        :param pin: Digital output channel number (0, 1, 2, or 3).
        :return: State of the pin (0 or 1).
        """
        if pin not in self.VALID_PINS:
            raise ValueError(f"Invalid pin {pin}. Valid pins are {self.VALID_PINS}")
        resp = self.send_command(f"DOUT_GET {pin}")
        tokens = resp.split()
        if len(tokens) >= 2 and tokens[0] == "OK":
            # Response format: OK <pin>:<val>
            for item in tokens[1:]:
                if ":" in item:
                    p_str, v_str = item.split(":", 1)
                    if int(p_str) == pin:
                        return int(v_str)
        raise GH2ProtocolError(f"Unexpected response to DOUT_GET: {resp}")

    def get_digital_outputs(self) -> Dict[int, int]:
        """
        Read the states of all configured digital output channels (0-3).

        :return: Dictionary mapping channel number to state {channel: state}.
        """
        resp = self.send_command("DOUT_GET")
        tokens = resp.split()
        if tokens and tokens[0] == "OK":
            result: Dict[int, int] = {}
            for item in tokens[1:]:
                if ":" in item:
                    try:
                        p_str, v_str = item.split(":", 1)
                        result[int(p_str)] = int(v_str)
                    except ValueError:
                        continue
            return result
        raise GH2ProtocolError(f"Unexpected response to DOUT_GET: {resp}")


def run_hardware_test(controller: GH2Controller) -> None:
    """Run an interactive smoke test sequence against physical GH2 hardware."""
    print("=== GH2 Hardware Diagnostic Smoke Test ===")
    print("1. Testing connection (PING)...")
    if controller.ping():
        print("   -> Connection OK (PONG received)")
    else:
        print("   -> Ping failed!")
        return

    print("\n2. Testing 4 Discrete Digital Outputs (Channels 0-3)...")
    for pin in controller.VALID_PINS:
        controller.set_digital_output(pin, True)
        time.sleep(0.05)
        new_val = controller.toggle_digital_output(pin)
        time.sleep(0.05)
        controller.set_digital_output(pin, False)
        print(f"   -> Channel {pin}: write HIGH, toggle -> {new_val}, reset to LOW")

    states = controller.get_digital_outputs()
    print(f"   -> Current channel states: {states}")

    print("\n3. Testing MCP4802 Dual DAC...")
    print("   -> Setting DAC Channel A to 1.024V (code=128, 1x gain)...")
    controller.set_dac("A", 128, gain_2x=False, active=True)
    time.sleep(0.1)
    print("   -> Setting DAC Channel B to 2.048V (code=128, 2x gain)...")
    controller.set_dac("B", 128, gain_2x=True, active=True)
    time.sleep(0.1)
    print("   -> Resetting both DAC channels to 0V...")
    controller.set_dac(0, 0)
    controller.set_dac(1, 0)

    print("\n4. Testing 64-bit Shift Register Chain (TPIC6B595N)...")
    print("   -> Clearing registers...")
    controller.clear_shift_registers()
    print("   -> Enabling outputs...")
    controller.enable_shift_registers(True)

    print("   -> Testing 16-bit bank writes...")
    controller.write_shift_register_bank(0, 0xAAAA)
    controller.write_shift_register_bank(1, 0x5555)
    time.sleep(0.05)

    print("   -> Testing single channel update...")
    controller.set_shift_register_channel(0, 0, 1)
    time.sleep(0.05)

    test_patterns = [
        0xAAAAAAAAAAAAAAAA,
        0x5555555555555555,
        0xFFFFFFFFFFFFFFFF,
        0x0000000000000000,
    ]
    for pattern in test_patterns:
        print(f"   -> Shifting pattern: 0x{pattern:016X}")
        controller.write_shift_registers(pattern)
        time.sleep(0.1)

    print("   -> Disabling outputs for safety...")
    controller.enable_shift_registers(False)
    print("\n=== All smoke test checks completed successfully! ===")


@click.command(help="Gantry Hub V2 (GH2) Serial Host Utility")
@click.option("--port", "-p", default="/dev/ttyACM0", show_default=True, help="Serial port (e.g. /dev/ttyACM0, COM3)")
@click.option("--baud", "-b", type=int, default=115200, show_default=True, help="Baud rate")
@click.option("--test", "-t", is_flag=True, help="Run automated peripheral smoke test")
@click.option("--ping", is_flag=True, help="Ping the Pico controller")
@click.option("--get-pins", is_flag=True, help="Query all digital output channels (0-3)")
@click.option(
    "--set-pin",
    type=(int, int),
    metavar="PIN VAL",
    help="Set digital output channel (PIN: 0-3, VAL: 0 or 1)",
)
@click.option("--toggle-pin", type=int, metavar="PIN", help="Toggle digital output channel state (PIN: 0-3)")
@click.option(
    "--dac",
    type=(str, int),
    metavar="CH VAL",
    help="Set DAC channel (0/1 or A/B) and value (0-255)",
)
@click.option("--sr-write", metavar="HEX64", help="Write 16 hex chars (64 bits) to shift registers")
@click.option(
    "--sr-bank",
    type=(int, str),
    metavar="BANK VAL",
    help="Write 16-bit value (hex or int) to shift register bank (0-3)",
)
@click.option(
    "--sr-channel",
    type=(int, int, int),
    metavar="BANK CH VAL",
    help="Set single channel (CH: 0-15) in bank (BANK: 0-3) to state (VAL: 0 or 1)",
)
@click.option("--sr-enable", type=click.Choice(["0", "1"]), help="Enable (1) or disable (0) shift registers")
@click.option("--sr-clear", is_flag=True, help="Clear shift registers")
@click.pass_context
def cli(
    ctx: click.Context,
    port: str,
    baud: int,
    test: bool,
    ping: bool,
    get_pins: bool,
    set_pin: Optional[Tuple[int, int]],
    toggle_pin: Optional[int],
    dac: Optional[Tuple[str, int]],
    sr_write: Optional[str],
    sr_bank: Optional[Tuple[int, str]],
    sr_channel: Optional[Tuple[int, int, int]],
    sr_enable: Optional[str],
    sr_clear: bool,
) -> None:
    """CLI entry point for manual testing and control."""
    has_action = any(
        [
            test,
            ping,
            get_pins,
            set_pin is not None,
            toggle_pin is not None,
            dac is not None,
            sr_write is not None,
            sr_bank is not None,
            sr_channel is not None,
            sr_enable is not None,
            sr_clear,
        ]
    )
    if not has_action:
        click.echo(ctx.get_help())
        return

    try:
        with GH2Controller(port=port, baudrate=baud) as controller:
            if test:
                run_hardware_test(controller)
            elif ping:
                ok = controller.ping()
                click.echo("PONG" if ok else "PING failed")
            elif get_pins:
                states = controller.get_digital_outputs()
                click.echo(f"Digital Outputs: {states}")
            elif set_pin is not None:
                pin, val = set_pin
                controller.set_digital_output(pin, val)
                click.echo(f"Pin {pin} set to {val}")
            elif toggle_pin is not None:
                new_val = controller.toggle_digital_output(toggle_pin)
                click.echo(f"Pin {toggle_pin} toggled to {new_val}")
            elif dac is not None:
                ch, val = dac
                controller.set_dac(ch, val)
                click.echo(f"DAC Channel {ch} set to {val}")
            elif sr_write is not None:
                controller.write_shift_registers(bytes.fromhex(sr_write))
                click.echo(f"Shift registers updated with 0x{sr_write}")
            elif sr_bank is not None:
                bank, val_str = sr_bank
                controller.write_shift_register_bank(bank, val_str)
                click.echo(f"Shift register bank {bank} updated with {val_str}")
            elif sr_channel is not None:
                bank, ch, val = sr_channel
                controller.set_shift_register_channel(bank, ch, val)
                click.echo(f"Shift register bank {bank} channel {ch} set to {1 if val else 0}")
            elif sr_enable is not None:
                controller.enable_shift_registers(sr_enable == "1")
                click.echo(f"Shift register outputs {'enabled' if sr_enable == '1' else 'disabled'}")
            elif sr_clear:
                controller.clear_shift_registers()
                click.echo("Shift registers cleared")
    except GH2Error as e:
        click.echo(f"GH2 Error: {e}", err=True)
        sys.exit(1)


main = cli

if __name__ == "__main__":
    cli()
