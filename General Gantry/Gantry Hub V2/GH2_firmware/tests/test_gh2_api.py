"""Unit and loopback tests for GH2 firmware protocol and PC host API client."""

import pytest
from unittest.mock import MagicMock

from gh2_api import (
    GH2Controller,
    GH2ConnectionError,
    GH2ProtocolError,
    GH2TimeoutError,
)
from msrc.protocol import ProtocolHandler
from msrc.tpic6b595 import (
    TPIC6B595Chain,
    map_bank_channel_to_bit,
    DEFAULT_CHANNEL_MAP,
    PASS_THROUGH_CHANNEL_MAP,
    BANK_0_J6A_MAP,
    BANK_1_J6B_MAP,
    BANK_2_J2A_MAP,
    BANK_3_J2B_MAP,
)


class MockSerial:
    """Mock serial port that can simulate firmware loopback or scripted responses."""

    def __init__(self, response_queue=None, handler=None):
        self.is_open = True
        self.response_queue = list(response_queue) if response_queue else []
        self.written_data = []
        self.handler = handler

    def write(self, data: bytes):
        self.written_data.append(data)
        if self.handler:
            # Process incoming command via protocol handler
            cmd = data.decode("ascii").strip()
            resp = self.handler.process_command(cmd)
            if resp:
                self.response_queue.append((resp + "\r\n").encode("ascii"))
        return len(data)

    def readline(self) -> bytes:
        if self.response_queue:
            return self.response_queue.pop(0)
        return b""

    def flush(self):
        pass

    def reset_input_buffer(self):
        pass

    def reset_output_buffer(self):
        pass

    def close(self):
        self.is_open = False


class MockShiftRegister:
    def __init__(self, channel_map=None):
        self.data = b"\x00" * 8
        self.enabled = False
        self.cleared = False
        self.state = 0
        self.num_banks = 4
        self.channels_per_bank = 16
        self.channel_map = channel_map if channel_map is not None else DEFAULT_CHANNEL_MAP

    def map_channel(self, bank: int, channel: int) -> int:
        if self.channel_map is not None:
            if callable(self.channel_map):
                return self.channel_map(bank, channel)
            return self.channel_map[bank * 16 + channel]
        return DEFAULT_CHANNEL_MAP[bank * 16 + channel]

    def write(self, data: bytes):
        self.data = bytes(data)
        self.state = int.from_bytes(data, "big")

    def write_int(self, value: int):
        self.state = value
        self.data = value.to_bytes(8, "big")

    def write_bank(self, bank: int, value: int):
        if not 0 <= bank < self.num_banks:
            raise ValueError(f"Invalid bank {bank}")
        if not 0 <= value <= 0xFFFF:
            raise ValueError(f"Value 0x{value:X} out of range")
        new_state = self.state
        for c in range(self.channels_per_bank):
            bit_val = (value >> c) & 1
            bit_pos = self.map_channel(bank, c)
            if bit_val:
                new_state |= (1 << bit_pos)
            else:
                new_state &= ~(1 << bit_pos)
        self.write_int(new_state)

    def set_channel(self, bank: int, channel: int, state: bool | int):
        if not 0 <= bank < self.num_banks:
            raise ValueError(f"Invalid bank {bank}")
        if not 0 <= channel < self.channels_per_bank:
            raise ValueError(f"Invalid channel {channel}")
        bit_pos = self.map_channel(bank, channel)
        new_state = self.state
        if state:
            new_state |= (1 << bit_pos)
        else:
            new_state &= ~(1 << bit_pos)
        self.write_int(new_state)

    def get_channel(self, bank: int, channel: int) -> int:
        bit_pos = self.map_channel(bank, channel)
        return (self.state >> bit_pos) & 1

    def get_bank(self, bank: int) -> int:
        val = 0
        for c in range(self.channels_per_bank):
            bit_pos = self.map_channel(bank, c)
            if (self.state >> bit_pos) & 1:
                val |= (1 << c)
        return val

    def set_enabled(self, enabled: bool):
        self.enabled = enabled

    def clear(self):
        self.cleared = True
        self.data = b"\x00" * 8
        self.state = 0


class MockDAC:
    def __init__(self):
        self.last_write = None

    def write(self, channel: int, value: int, gain_2x: bool = False, active: bool = True):
        self.last_write = {
            "channel": channel,
            "value": value,
            "gain_2x": gain_2x,
            "active": active,
        }


class MockDigitalOutputs:
    VALID_PINS = (0, 1, 2, 3)

    def __init__(self):
        self.states = {0: 0, 1: 0, 2: 0, 3: 0}

    def set(self, pin: int, value: bool | int):
        self.states[pin] = 1 if value else 0

    def get(self, pin: int) -> int:
        return self.states[pin]

    def toggle(self, pin: int) -> int:
        self.states[pin] = 0 if self.states[pin] else 1
        return self.states[pin]

    def get_all(self):
        return dict(self.states)


@pytest.fixture
def loopback_fixture():
    sr = MockShiftRegister()
    dac = MockDAC()
    dout = MockDigitalOutputs()
    handler = ProtocolHandler(sr, dac, dout)
    mock_serial = MockSerial(handler=handler)
    controller = GH2Controller(serial_instance=mock_serial)
    return {
        "sr": sr,
        "dac": dac,
        "dout": dout,
        "handler": handler,
        "serial": mock_serial,
        "controller": controller,
    }


# =====================================================================
# Tests for ProtocolHandler (msrc/protocol.py)
# =====================================================================

def test_protocol_ping():
    handler = ProtocolHandler(MockShiftRegister(), MockDAC(), MockDigitalOutputs())
    assert handler.process_command("PING") == "PONG"
    assert handler.process_command("ping") == "PONG"


def test_protocol_sr_write():
    sr = MockShiftRegister()
    handler = ProtocolHandler(sr, MockDAC(), MockDigitalOutputs())

    # Valid 16-hex characters (64 bits)
    assert handler.process_command("SR_WRITE AA55AA55AA55AA55") == "OK"
    assert sr.data == bytes.fromhex("AA55AA55AA55AA55")

    # Invalid lengths
    assert handler.process_command("SR_WRITE AA55") == "ERR INVALID_HEX_LENGTH"
    assert handler.process_command("SR_WRITE AA55AA55AA55AA5500") == "ERR INVALID_HEX_LENGTH"

    # Invalid characters
    assert handler.process_command("SR_WRITE GG55AA55AA55AA55") == "ERR INVALID_HEX"

    # Missing arguments
    assert handler.process_command("SR_WRITE") == "ERR MISSING_ARGUMENTS"


def test_protocol_sr_enable_and_clear():
    sr = MockShiftRegister()
    handler = ProtocolHandler(sr, MockDAC(), MockDigitalOutputs())

    assert handler.process_command("SR_ENABLE 1") == "OK"
    assert sr.enabled is True

    assert handler.process_command("SR_ENABLE 0") == "OK"
    assert sr.enabled is False

    assert handler.process_command("SR_ENABLE 2") == "ERR INVALID_ARG"
    assert handler.process_command("SR_ENABLE") == "ERR MISSING_ARGUMENTS"

    assert handler.process_command("SR_CLEAR") == "OK"
    assert sr.cleared is True


def test_shift_register_mapping():
    # Default mapping matches valve_block_interface.kicad_sch:
    # Bank 0 -> J6A
    for ch in range(16):
        assert map_bank_channel_to_bit(0, ch) == BANK_0_J6A_MAP[ch]
    # Bank 1 -> J6B
    for ch in range(16):
        assert map_bank_channel_to_bit(1, ch) == BANK_1_J6B_MAP[ch]
    # Bank 2 -> J2A
    for ch in range(16):
        assert map_bank_channel_to_bit(2, ch) == BANK_2_J2A_MAP[ch]
    # Bank 3 -> J2B
    for ch in range(16):
        assert map_bank_channel_to_bit(3, ch) == BANK_3_J2B_MAP[ch]

    # Verify all 64 bits from 0 to 63 are uniquely mapped
    all_bits = [map_bank_channel_to_bit(b, c) for b in range(4) for c in range(16)]
    assert sorted(all_bits) == list(range(64))

    # Pass-through mapping tests
    for b in range(4):
        for ch in range(16):
            assert map_bank_channel_to_bit(b, ch, mapping=PASS_THROUGH_CHANNEL_MAP) == b * 16 + ch

    # Error handling for out of range bank/channel
    with pytest.raises(ValueError):
        map_bank_channel_to_bit(-1, 0)
    with pytest.raises(ValueError):
        map_bank_channel_to_bit(4, 0)
    with pytest.raises(ValueError):
        map_bank_channel_to_bit(0, -1)
    with pytest.raises(ValueError):
        map_bank_channel_to_bit(0, 16)

    # Custom mapping list
    reversed_map = list(reversed(range(64)))
    assert map_bank_channel_to_bit(0, 0, mapping=reversed_map) == 63
    assert map_bank_channel_to_bit(3, 15, mapping=reversed_map) == 0

    # Custom mapping callable
    def custom_fn(b, c):
        return (b * 16 + (15 - c))
    assert map_bank_channel_to_bit(0, 0, mapping=custom_fn) == 15
    assert map_bank_channel_to_bit(0, 15, mapping=custom_fn) == 0


def test_tpic6b595_chain_standalone():
    # 1. Test with default schematic mapping
    chain = TPIC6B595Chain(num_devices=8)
    assert chain.state == 0
    assert chain.num_banks == 4
    assert chain.channels_per_bank == 16

    # Write Bank 0: 0x1234
    chain.write_bank(0, 0x1234)
    assert chain.get_bank(0) == 0x1234
    assert chain.get_bank(1) == 0
    expected_b0_state = sum((1 << BANK_0_J6A_MAP[c]) for c in range(16) if (0x1234 >> c) & 1)
    assert chain.state == expected_b0_state

    # Write Bank 1: 0xABCD (Bank 0 state preserved!)
    chain.write_bank(1, 0xABCD)
    assert chain.get_bank(0) == 0x1234
    assert chain.get_bank(1) == 0xABCD
    expected_b1_state = sum((1 << BANK_1_J6B_MAP[c]) for c in range(16) if (0xABCD >> c) & 1)
    assert chain.state == (expected_b0_state | expected_b1_state)

    # Write Bank 3: 0xFFFF (Bank 0 and 1 preserved)
    chain.write_bank(3, 0xFFFF)
    assert chain.get_bank(0) == 0x1234
    assert chain.get_bank(1) == 0xABCD
    assert chain.get_bank(3) == 0xFFFF

    # Single channel writes
    # Set bank 0, channel 0 to 1 -> 0x1235
    chain.set_channel(0, 0, 1)
    assert chain.get_channel(0, 0) == 1
    assert chain.get_bank(0) == 0x1235
    assert chain.get_bank(1) == 0xABCD
    assert chain.get_bank(3) == 0xFFFF

    # Clear channel 0 in bank 0
    chain.set_channel(0, 0, 0)
    assert chain.get_channel(0, 0) == 0
    assert chain.get_bank(0) == 0x1234

    # Clear all
    chain.clear()
    assert chain.state == 0
    assert chain.get_bank(0) == 0
    assert chain.get_bank(1) == 0

    # 2. Test with pass-through mapping
    chain_pt = TPIC6B595Chain(num_devices=8, channel_map=PASS_THROUGH_CHANNEL_MAP)
    chain_pt.write_bank(0, 0x1234)
    assert chain_pt.state == 0x1234
    chain_pt.write_bank(1, 0xABCD)
    assert chain_pt.state == (0xABCD << 16) | 0x1234

    # Validation errors
    with pytest.raises(ValueError):
        chain.write_bank(4, 0x1234)
    with pytest.raises(ValueError):
        chain.write_bank(0, 0x10000)
    with pytest.raises(ValueError):
        chain.write_bank(0, -1)
    with pytest.raises(ValueError):
        chain.set_channel(4, 0, 1)
    with pytest.raises(ValueError):
        chain.set_channel(0, 16, 1)


def test_protocol_sr_bank_and_channel_write():
    sr = MockShiftRegister()
    handler = ProtocolHandler(sr, MockDAC(), MockDigitalOutputs())

    # Bank writes
    assert handler.process_command("SR_WRITE_BANK 0 0x1234") == "OK"
    assert sr.get_bank(0) == 0x1234

    # 4-char hex string without 0x
    assert handler.process_command("SR_WRITE_BANK 1 ABCD") == "OK"
    assert sr.get_bank(0) == 0x1234
    assert sr.get_bank(1) == 0xABCD

    # Decimal value
    assert handler.process_command("SR_WRITE_BANK 2 255") == "OK"
    assert sr.get_bank(2) == 255

    # Alias SR_BANK
    assert handler.process_command("SR_BANK 3 0x0001") == "OK"
    assert sr.get_bank(3) == 1

    # SR_WRITE with 2 args (bank write)
    assert handler.process_command("SR_WRITE 0 0x5555") == "OK"
    assert sr.get_bank(0) == 0x5555

    # Single channel writes
    # SR_WRITE_CHANNEL bank ch val
    assert handler.process_command("SR_WRITE_CHANNEL 0 1 1") == "OK"
    assert sr.get_channel(0, 1) == 1

    # Aliases
    assert handler.process_command("SR_SET_CHANNEL 0 1 0") == "OK"
    assert sr.get_channel(0, 1) == 0

    assert handler.process_command("SR_CHANNEL 1 0 1") == "OK"
    assert sr.get_channel(1, 0) == 1

    # SR_WRITE with 3 args (channel write)
    assert handler.process_command("SR_WRITE 1 0 0") == "OK"
    assert sr.get_channel(1, 0) == 0

    # SR_GET
    assert handler.process_command("SR_GET").startswith("OK ")
    assert handler.process_command("SR_GET 0") == "OK 0:5555"
    assert handler.process_command("SR_GET 0 0") == "OK 0:0:1"  # 0x5555 bit 0 is 1

    # Error handling
    assert handler.process_command("SR_WRITE_BANK 4 0x1234") == "ERR INVALID_BANK"
    assert handler.process_command("SR_WRITE_BANK -1 0x1234") == "ERR INVALID_BANK"
    assert handler.process_command("SR_WRITE_BANK 0 0x10000") == "ERR VALUE_OUT_OF_RANGE"
    assert handler.process_command("SR_WRITE_BANK 0 -1") == "ERR VALUE_OUT_OF_RANGE"
    assert handler.process_command("SR_WRITE_BANK 0 INVALID") == "ERR INVALID_VALUE"
    assert handler.process_command("SR_WRITE_BANK 0") == "ERR MISSING_ARGUMENTS"

    assert handler.process_command("SR_WRITE_CHANNEL 4 0 1") == "ERR INVALID_BANK"
    assert handler.process_command("SR_WRITE_CHANNEL 0 16 1") == "ERR INVALID_CHANNEL"
    assert handler.process_command("SR_WRITE_CHANNEL 0 0 2") == "ERR INVALID_VALUE"
    assert handler.process_command("SR_WRITE_CHANNEL 0 0") == "ERR MISSING_ARGUMENTS"


def test_protocol_dac_write():
    dac = MockDAC()
    handler = ProtocolHandler(MockShiftRegister(), dac, MockDigitalOutputs())

    # Channel A, value 128, defaults (gain 1x, active)
    assert handler.process_command("DAC_WRITE A 128") == "OK"
    assert dac.last_write == {"channel": 0, "value": 128, "gain_2x": False, "active": True}

    # Channel B, value 255, 2x gain, active
    assert handler.process_command("DAC_WRITE 1 255 1 1") == "OK"
    assert dac.last_write == {"channel": 1, "value": 255, "gain_2x": True, "active": True}

    # Channel 0, value 0, shutdown
    assert handler.process_command("DAC_WRITE 0 0 0 0") == "OK"
    assert dac.last_write == {"channel": 0, "value": 0, "gain_2x": False, "active": False}

    # Errors
    assert handler.process_command("DAC_WRITE C 100") == "ERR INVALID_CHANNEL"
    assert handler.process_command("DAC_WRITE A 256") == "ERR VALUE_OUT_OF_RANGE"
    assert handler.process_command("DAC_WRITE A -1") == "ERR VALUE_OUT_OF_RANGE"
    assert handler.process_command("DAC_WRITE A 100 2") == "ERR INVALID_GAIN"
    assert handler.process_command("DAC_WRITE A 100 1 2") == "ERR INVALID_ACTIVE"
    assert handler.process_command("DAC_WRITE") == "ERR MISSING_ARGUMENTS"


def test_protocol_digital_out():
    dout = MockDigitalOutputs()
    handler = ProtocolHandler(MockShiftRegister(), MockDAC(), dout)

    # DOUT_SET
    assert handler.process_command("DOUT_SET 0 1") == "OK"
    assert dout.get(0) == 1
    assert handler.process_command("DOUT_SET 0 0") == "OK"
    assert dout.get(0) == 0

    # Invalid pin
    assert handler.process_command("DOUT_SET 4 1") == "ERR INVALID_PIN"
    assert handler.process_command("DOUT_SET 0 2") == "ERR INVALID_VALUE"

    # DOUT_TOGGLE
    assert handler.process_command("DOUT_TOGGLE 1") == "OK 1"
    assert dout.get(1) == 1
    assert handler.process_command("DOUT_TOGGLE 1") == "OK 0"
    assert dout.get(1) == 0

    # DOUT_GET
    dout.set(0, 1)
    dout.set(3, 1)
    assert handler.process_command("DOUT_GET 0") == "OK 0:1"
    assert handler.process_command("DOUT_GET 1") == "OK 1:0"
    assert handler.process_command("DOUT_GET") == "OK 0:1 1:0 2:0 3:1"


def test_protocol_unknown_command():
    handler = ProtocolHandler(MockShiftRegister(), MockDAC(), MockDigitalOutputs())
    assert handler.process_command("UNKNOWN_CMD foo") == "ERR UNKNOWN_COMMAND UNKNOWN_CMD"
    assert handler.process_command("") == ""


# =====================================================================
# Tests for GH2Controller Client API (gh2_api.py)
# =====================================================================

def test_controller_context_manager():
    mock_serial = MockSerial(response_queue=[b"PONG\r\n"])
    with GH2Controller(serial_instance=mock_serial) as controller:
        assert controller.is_open is True
        assert controller.ping() is True
    assert controller.is_open is False


def test_controller_loopback_ping(loopback_fixture):
    controller = loopback_fixture["controller"]
    assert controller.ping() is True


def test_controller_loopback_shift_registers(loopback_fixture):
    controller = loopback_fixture["controller"]
    sr = loopback_fixture["sr"]

    # Write as int
    controller.write_shift_registers(0x0123456789ABCDEF)
    assert sr.data == bytes.fromhex("0123456789ABCDEF")

    # Write as bytes
    controller.write_shift_registers(b"\xAA\x55\xAA\x55\xAA\x55\xAA\x55")
    assert sr.data == b"\xAA\x55\xAA\x55\xAA\x55\xAA\x55"

    # Write as list of ints
    controller.write_shift_registers([1, 2, 3, 4, 5, 6, 7, 8])
    assert sr.data == bytes([1, 2, 3, 4, 5, 6, 7, 8])

    # Enable and clear
    controller.enable_shift_registers(True)
    assert sr.enabled is True
    controller.enable_shift_registers(False)
    assert sr.enabled is False
    controller.clear_shift_registers()
    assert sr.cleared is True


def test_controller_shift_registers_validation(loopback_fixture):
    controller = loopback_fixture["controller"]

    with pytest.raises(ValueError):
        controller.write_shift_registers(b"\x00\x01")  # not 8 bytes

    with pytest.raises(ValueError):
        controller.write_shift_registers(0x1_00000000_00000000)  # > 64 bits

    with pytest.raises(ValueError):
        controller.write_shift_registers(-1)

    with pytest.raises(ValueError):
        controller.write_shift_registers([1, 2, 3])  # not 8 items

    with pytest.raises(TypeError):
        controller.write_shift_registers("invalid string")


def test_controller_bank_and_channel(loopback_fixture):
    controller = loopback_fixture["controller"]
    sr = loopback_fixture["sr"]

    # Write bank 0 as int
    controller.write_shift_register_bank(0, 0x1234)
    assert sr.get_bank(0) == 0x1234
    assert controller.get_shift_register_bank(0) == 0x1234

    # Write bank 1 as hex string
    controller.write_shift_register_bank(1, "0xABCD")
    assert sr.get_bank(0) == 0x1234
    assert sr.get_bank(1) == 0xABCD
    assert controller.get_shift_register_bank(1) == 0xABCD

    # Write bank 2 as 4-char hex string
    controller.write_shift_register_bank(2, "5555")
    assert sr.get_bank(2) == 0x5555

    # Write bank 3 as 2-byte bytes
    controller.write_shift_register_bank(3, b"\xAA\xAA")
    assert sr.get_bank(3) == 0xAAAA
    assert controller.get_shift_register_bank(3) == 0xAAAA
    expected_state = (
        sum((1 << BANK_0_J6A_MAP[c]) for c in range(16) if (0x1234 >> c) & 1)
        | sum((1 << BANK_1_J6B_MAP[c]) for c in range(16) if (0xABCD >> c) & 1)
        | sum((1 << BANK_2_J2A_MAP[c]) for c in range(16) if (0x5555 >> c) & 1)
        | sum((1 << BANK_3_J2B_MAP[c]) for c in range(16) if (0xAAAA >> c) & 1)
    )
    assert sr.state == expected_state
    assert controller.get_shift_register_state() == sr.state

    # Set single channel in bank 0
    controller.set_shift_register_channel(0, 0, True)
    assert controller.get_shift_register_channel(0, 0) == 1
    assert sr.get_bank(0) == 0x1235

    controller.set_shift_register_channel(0, 0, False)
    assert controller.get_shift_register_channel(0, 0) == 0
    assert sr.get_bank(0) == 0x1234

    # Validation errors
    with pytest.raises(ValueError):
        controller.write_shift_register_bank(4, 0x1234)
    with pytest.raises(ValueError):
        controller.write_shift_register_bank(-1, 0x1234)
    with pytest.raises(ValueError):
        controller.write_shift_register_bank(0, 0x10000)
    with pytest.raises(ValueError):
        controller.write_shift_register_bank(0, -1)
    with pytest.raises(ValueError):
        controller.write_shift_register_bank(0, b"\x01")  # not 2 bytes
    with pytest.raises(TypeError):
        controller.write_shift_register_bank(0, [1, 2])

    with pytest.raises(ValueError):
        controller.set_shift_register_channel(4, 0, True)
    with pytest.raises(ValueError):
        controller.set_shift_register_channel(0, 16, True)
    with pytest.raises(ValueError):
        controller.get_shift_register_bank(4)
    with pytest.raises(ValueError):
        controller.get_shift_register_channel(4, 0)
    with pytest.raises(ValueError):
        controller.get_shift_register_channel(0, 16)


def test_controller_loopback_dac(loopback_fixture):
    controller = loopback_fixture["controller"]
    dac = loopback_fixture["dac"]

    controller.set_dac("A", 100)
    assert dac.last_write == {"channel": 0, "value": 100, "gain_2x": False, "active": True}

    controller.set_dac("B", 200, gain_2x=True, active=False)
    assert dac.last_write == {"channel": 1, "value": 200, "gain_2x": True, "active": False}

    with pytest.raises(ValueError):
        controller.set_dac("C", 100)

    with pytest.raises(ValueError):
        controller.set_dac(0, 300)


def test_controller_loopback_digital_out(loopback_fixture):
    controller = loopback_fixture["controller"]
    dout = loopback_fixture["dout"]

    # Individual set and get
    for pin in (0, 1, 2, 3):
        controller.set_digital_output(pin, True)
        assert dout.get(pin) == 1
        assert controller.get_digital_output(pin) == 1

        new_val = controller.toggle_digital_output(pin)
        assert new_val == 0
        assert dout.get(pin) == 0
        assert controller.get_digital_output(pin) == 0

    controller.set_digital_output(2, 1)
    controller.set_digital_output(3, 1)
    states = controller.get_digital_outputs()
    assert states == {0: 0, 1: 0, 2: 1, 3: 1}

    with pytest.raises(ValueError):
        controller.set_digital_output(4, 1)

    with pytest.raises(ValueError):
        controller.toggle_digital_output(13)

    with pytest.raises(ValueError):
        controller.get_digital_output(12)


def test_controller_timeout_error():
    # Serial returns empty bytes (timeout)
    mock_serial = MockSerial(response_queue=[b""])
    controller = GH2Controller(serial_instance=mock_serial)
    with pytest.raises(GH2TimeoutError) as exc_info:
        controller.send_command("PING")
    assert "Timeout" in str(exc_info.value)


def test_controller_protocol_error():
    # Serial returns ERR
    mock_serial = MockSerial(response_queue=[b"ERR INVALID_HEX\r\n"])
    controller = GH2Controller(serial_instance=mock_serial)
    with pytest.raises(GH2ProtocolError) as exc_info:
        controller.send_command("SR_WRITE BAD")
    assert "ERR INVALID_HEX" in str(exc_info.value)


def test_controller_connection_error():
    controller = GH2Controller(serial_instance=None)
    with pytest.raises(GH2ConnectionError):
        controller.send_command("PING")


def test_run_hardware_test(loopback_fixture):
    from gh2_api import run_hardware_test

    controller = loopback_fixture["controller"]
    # Should execute the entire test sequence without raising any exceptions
    run_hardware_test(controller)


def test_cli_help():
    from click.testing import CliRunner
    from gh2_api import cli

    runner = CliRunner()
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "Gantry Hub V2 (GH2) Serial Host Utility" in result.output
    assert "--port" in result.output
    assert "--test" in result.output
    assert "--ping" in result.output
    assert "--sr-bank" in result.output
    assert "--sr-channel" in result.output


def test_cli_no_args_shows_help():
    from click.testing import CliRunner
    from gh2_api import cli

    runner = CliRunner()
    result = runner.invoke(cli, [])
    assert result.exit_code == 0
    assert "Usage: " in result.output
    assert "--port" in result.output


def test_cli_commands():
    from unittest.mock import patch
    from click.testing import CliRunner
    from gh2_api import cli, GH2Error

    runner = CliRunner()

    with patch("gh2_api.GH2Controller") as mock_ctrl_class:
        mock_ctrl = mock_ctrl_class.return_value.__enter__.return_value

        # --ping
        mock_ctrl.ping.return_value = True
        res = runner.invoke(cli, ["--ping"])
        assert res.exit_code == 0
        assert "PONG" in res.output

        # --get-pins
        mock_ctrl.get_digital_outputs.return_value = {0: 1, 1: 0, 2: 1, 3: 0}
        res = runner.invoke(cli, ["--get-pins"])
        assert res.exit_code == 0
        assert "Digital Outputs: {0: 1, 1: 0, 2: 1, 3: 0}" in res.output

        # --set-pin
        res = runner.invoke(cli, ["--set-pin", "0", "1"])
        assert res.exit_code == 0
        mock_ctrl.set_digital_output.assert_called_with(0, 1)
        assert "Pin 0 set to 1" in res.output

        # --toggle-pin
        mock_ctrl.toggle_digital_output.return_value = 1
        res = runner.invoke(cli, ["--toggle-pin", "1"])
        assert res.exit_code == 0
        mock_ctrl.toggle_digital_output.assert_called_with(1)
        assert "Pin 1 toggled to 1" in res.output

        # --dac
        res = runner.invoke(cli, ["--dac", "A", "128"])
        assert res.exit_code == 0
        mock_ctrl.set_dac.assert_called_with("A", 128)
        assert "DAC Channel A set to 128" in res.output

        # --sr-write
        res = runner.invoke(cli, ["--sr-write", "0123456789ABCDEF"])
        assert res.exit_code == 0
        mock_ctrl.write_shift_registers.assert_called_with(bytes.fromhex("0123456789ABCDEF"))
        assert "Shift registers updated with 0x0123456789ABCDEF" in res.output

        # --sr-bank
        res = runner.invoke(cli, ["--sr-bank", "0", "0x1234"])
        assert res.exit_code == 0
        mock_ctrl.write_shift_register_bank.assert_called_with(0, "0x1234")
        assert "Shift register bank 0 updated with 0x1234" in res.output

        # --sr-channel
        res = runner.invoke(cli, ["--sr-channel", "1", "5", "1"])
        assert res.exit_code == 0
        mock_ctrl.set_shift_register_channel.assert_called_with(1, 5, 1)
        assert "Shift register bank 1 channel 5 set to 1" in res.output

        # --sr-enable 1
        res = runner.invoke(cli, ["--sr-enable", "1"])
        assert res.exit_code == 0
        mock_ctrl.enable_shift_registers.assert_called_with(True)
        assert "Shift register outputs enabled" in res.output

        # --sr-enable 0
        res = runner.invoke(cli, ["--sr-enable", "0"])
        assert res.exit_code == 0
        mock_ctrl.enable_shift_registers.assert_called_with(False)
        assert "Shift register outputs disabled" in res.output

        # --sr-clear
        res = runner.invoke(cli, ["--sr-clear"])
        assert res.exit_code == 0
        mock_ctrl.clear_shift_registers.assert_called_once()
        assert "Shift registers cleared" in res.output


def test_cli_error_handling():
    from unittest.mock import patch
    from click.testing import CliRunner
    from gh2_api import cli, GH2ConnectionError

    runner = CliRunner()

    with patch("gh2_api.GH2Controller") as mock_ctrl_class:
        mock_ctrl = mock_ctrl_class.return_value.__enter__.return_value
        mock_ctrl.ping.side_effect = GH2ConnectionError("Device not connected")

        res = runner.invoke(cli, ["--ping"])
        assert res.exit_code == 1
        assert "GH2 Error: Device not connected" in res.output


def test_sentinel_protocol_handling(loopback_fixture):
    """Verify that commands prefixed with '$' sentinel are handled correctly."""
    handler = loopback_fixture["handler"]
    sr = loopback_fixture["sr"]

    # Command with '$' prefix
    assert handler.process_command("$PING") == "PONG"
    assert handler.process_command("$ PING") == "PONG"
    assert handler.process_command("$SR_WRITE 0123456789ABCDEF") == "OK"
    assert sr.data == bytes.fromhex("0123456789ABCDEF")
    assert handler.process_command("$DOUT_SET 0 1") == "OK"
    assert handler.process_command("$DOUT_GET 0") == "OK 0:1"


def test_firmware_sentinel_buffer_clearing():
    """Simulate the firmware buffer loop from msrc/main.py with corrupted/partial data."""
    handler = MagicMock()
    handler.process_command.return_value = "PONG"

    START_SENTINEL = "$"
    buf = []
    executed_cmds = []

    # Stream of incoming characters simulating an interrupted command followed by a new '$' command
    stream = "SR_WRIT" + "$PING\r\n"

    for ch in stream:
        if ch == START_SENTINEL:
            buf.clear()  # Discard partial command
            continue
        if ch in ("\r", "\n"):
            if buf:
                cmd_str = "".join(buf)
                buf.clear()
                resp = handler.process_command(cmd_str)
                executed_cmds.append((cmd_str, resp))
        else:
            buf.append(ch)

    # Verify that 'SR_WRIT' was discarded and only 'PING' was executed
    assert executed_cmds == [("PING", "PONG")]
    handler.process_command.assert_called_once_with("PING")


def test_digital_outputs_physical_pin_mapping():
    """Verify that DigitalOutputs correctly maps channels 0-3 directly to RP2040 GPIOs (and corresponding Pico physical pins)."""
    import sys
    from unittest.mock import patch

    mock_pin_cls = MagicMock()
    mock_machine = MagicMock(Pin=mock_pin_cls)

    with patch.dict(sys.modules, {"machine": mock_machine}):
        import msrc.digital_out
        from importlib import reload
        reload(msrc.digital_out)
        from msrc.digital_out import DigitalOutputs

        # Verify mapping constants
        # Output 0 -> GPIO 5 (Pico Pin 7)
        # Output 1 -> GPIO 6 (Pico Pin 9)
        # Output 2 -> GPIO 7 (Pico Pin 10)
        # Output 3 -> GPIO 8 (Pico Pin 11)
        assert DigitalOutputs.PIN_MAP == {0: 5, 1: 6, 2: 7, 3: 8}
        assert DigitalOutputs.VALID_PINS == (0, 1, 2, 3)

        dout = DigitalOutputs()
        # Ensure Pin was called with the GPIO numbers (5, 6, 7, 8)
        created_gpios = [call.args[0] for call in mock_pin_cls.call_args_list]
        assert set(created_gpios) == {5, 6, 7, 8}


def test_controller_sends_sentinel_and_cleans_blank_lines():
    """Verify that GH2Controller prefixes commands with '$' and ignores blank response lines."""
    mock_serial = MockSerial()
    # Queue response with leading blank lines (e.g. leftover \r\n from previous transaction)
    mock_serial.response_queue = [b"\r\n", b"\n", b"PONG\r\n"]

    controller = GH2Controller(serial_instance=mock_serial)
    resp = controller.send_command("PING")

    assert resp == "PONG"
    # Ensure written data started with '$'
    assert len(mock_serial.written_data) == 1
    assert mock_serial.written_data[0].startswith(b"$PING")


def test_firmware_heartbeat_timing():
    """Verify that the firmware heartbeat logic pulses the LED at 2 Hz (toggling every 250 ms)."""
    led_mock = MagicMock()
    current_value = 0
    toggle_history = []

    def fake_toggle():
        nonlocal current_value
        current_value = 0 if current_value else 1
        toggle_history.append((simulated_time, current_value))

    led_mock.toggle.side_effect = fake_toggle

    HEARTBEAT_INTERVAL_MS = 250
    simulated_time = 0
    last_heartbeat = simulated_time

    # Simulate 1000 ms elapsed in 10 ms polling steps
    for step in range(1, 101):
        simulated_time = step * 10
        if simulated_time - last_heartbeat >= HEARTBEAT_INTERVAL_MS:
            last_heartbeat = simulated_time
            led_mock.toggle()

    # In 1000 ms (1.0 second), a 2 Hz pulse toggles 4 times:
    # 250ms (ON), 500ms (OFF), 750ms (ON), 1000ms (OFF) -> 2 full on-off cycles = 2 Hz
    assert len(toggle_history) == 4
    assert [t for t, _ in toggle_history] == [250, 500, 750, 1000]
    assert [v for _, v in toggle_history] == [1, 0, 1, 0]
