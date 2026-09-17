# Gantry Hub V2 (GH2) Firmware & Host API

Gantry Hub V2 (GH2) is an industrial peripheral control system powered by a Raspberry Pi Pico running MicroPython. The Pico acts as an intelligent peripheral controller connected to a host PC over USB serial, providing deterministic control over three hardware subsystems:

1. **8-Stage Daisy-Chained TPIC6B595N Shift Registers**: 64 discrete open-drain power DMOS outputs.
2. **Microchip MCP4802 Dual 8-bit DAC**: Dual analog output channels with programmable gain and shutdown modes over SPI.
3. **Four Discrete Digital Outputs**: Push-pull digital outputs (channels 0-3) mapped directly to RP2040 GPIO lines (GPIO 5, 6, 7, and 8, corresponding to physical header pins 7, 9, 10, and 11 on the Pico).

This repository includes both the MicroPython firmware (`msrc/`) and the PC-side Python client library with a Click-powered CLI tool (`gh2_api.py`).

---

## Hardware Pinout & Wiring

### Raspberry Pi Pico Physical Header Pin Mapping

All pin references in the user API, CLI, and commands refer to the **Raspberry Pi Pico physical board header pins (Pins 1–40)**. The firmware translates these physical header pins to RP2040 internal GPIO lines as follows:

| Subsystem | Signal Name | Pico Header Physical Pin | RP2040 GPIO | Description |
| :--- | :--- | :--- | :--- | :--- |
| **TPIC6B595N** | $\overline{\text{SRCLR}}$ | **Pin 1** | GPIO 0 | Active-low shift register clear (held HIGH during operation) |
| | $\overline{\text{G}}$ | **Pin 2** | GPIO 1 | Active-low output enable (HIGH = disabled / high-Z, LOW = enabled) |
| | $\text{S\_ck}$ | **Pin 4** | GPIO 2 | Shift register serial clock (rising edge) |
| | $\text{S\_in}$ | **Pin 5** | GPIO 3 | Serial data input to chain |
| | $\text{R\_ck}$ | **Pin 6** | GPIO 4 | Storage / latch clock (rising edge updates outputs) |
| **Digital Outputs** | DOUT 0 | **Pin 7** | GPIO 5 | Discrete digital output channel 0 |
| | DOUT 1 | **Pin 9** | GPIO 6 | Discrete digital output channel 1 |
| | DOUT 2 | **Pin 10** | GPIO 7 | Discrete digital output channel 2 |
| | DOUT 3 | **Pin 11** | GPIO 8 | Discrete digital output channel 3 |
| **MCP4802 DAC** | $\overline{\text{CS}}$ | **Pin 12** | GPIO 9 | Active-low SPI chip select |
| | $\text{SCK}$ | **Pin 14** | GPIO 10 | SPI1 serial clock |
| | $\text{SDI}$ | **Pin 15** | GPIO 11 | SPI1 TX / MOSI (data to DAC) |
| **Ground** | GND | **Pin 3, 8, 13, 18, 23, 28, 38** | GND | Ground reference pins |

---

## Project Structure

```
GH2_firmware/
├── pyproject.toml             # Python dependencies (pyserial, click, pytest)
├── gh2_api.py                 # PC host client library (GH2Controller) and CLI tool
├── README.md                  # System and API documentation
├── tests/
│   └── test_gh2_api.py        # Automated test suite
└── msrc/                      # MicroPython firmware deployed to Raspberry Pi Pico
    ├── main.py                # Firmware entry point and USB polling loop
    ├── protocol.py            # USB ASCII command processor
    ├── tpic6b595.py           # 8-stage TPIC6B595N daisy chain driver
    ├── mcp4802.py             # MCP4802 SPI dual DAC driver
    └── digital_out.py         # 4-channel digital output driver
```

---

## Installation & Setup

### Host PC Dependencies

The host client library requires Python >= 3.8 and depends on `pyserial` and `click`.

Install dependencies using `uv` or `pip`:

```bash
# Using uv (recommended)
uv sync

# Or using pip
pip install pyserial click
```

### MicroPython Firmware Deployment

Copy all files inside `msrc/` to the root filesystem of your Raspberry Pi Pico running MicroPython:

```bash
# Using mpremote (optional tool)
mpremote connect /dev/ttyACM0 cp msrc/* :
```

On boot, `msrc/main.py` will initialize all outputs safely:
- Shift register outputs are disabled ($\overline{\text{G}} = \text{HIGH}$).
- Shift registers are cleared.
- Digital outputs (channels 0-3) default to `LOW`.
- MCP4802 DAC channels default to 0V active.
- Onboard LED begins a 2 Hz heartbeat pulse (250 ms ON / 250 ms OFF) indicating active firmware execution.

---

## Python API (`gh2_api.py`)

The `GH2Controller` class provides thread-safe communication and supports Python context managers (`with`).

### Quick Start

```python
from gh2_api import GH2Controller

# Connect via context manager (automatically opens and closes port)
with GH2Controller(port="/dev/ttyACM0", baudrate=115200) as gh2:
    # 1. Connection check
    if gh2.ping():
        print("Connected to GH2 controller!")

    # 2. Control Discrete Digital Outputs (Channels 0-3)
    gh2.set_digital_output(pin=0, state=True)       # Channel 0 HIGH (GP5 / Pin 7)
    gh2.toggle_digital_output(pin=1)                # Invert Channel 1 (GP6 / Pin 9)
    pin_states = gh2.get_digital_outputs()          # {0: 1, 1: 1, 2: 0, 3: 0}
    print("Pin states:", pin_states)

    # 3. Control MCP4802 DAC
    # Set Channel A to 1.024V (code 128, 1x gain: 0-2.048V)
    gh2.set_dac(channel="A", value=128, gain_2x=False, active=True)

    # Set Channel B to 2.048V (code 128, 2x gain: 0-4.096V)
    gh2.set_dac(channel="B", value=128, gain_2x=True, active=True)

    # 4. Control 64-bit Shift Registers
    # Enable outputs (\bar{G} driven LOW)
    gh2.enable_shift_registers(True)

    # Write 64 bits (supports int, 8-byte bytes, or list of 8 ints)
    gh2.write_shift_registers(0xAAAAAAAAAAAAAAAA)

    # Clear shift registers (\bar{SRCLR} pulsed)
    gh2.clear_shift_registers()
```

### API Method Reference

#### Connection Management
- `GH2Controller(port=None, baudrate=115200, timeout=2.0, serial_instance=None)`: Initialize controller instance.
- `open()`: Opens the serial connection and flushes serial buffers.
- `close()`: Closes the serial connection.
- `__enter__()` / `__exit__()`: Context manager interface.
- `ping() -> bool`: Sends `PING` and returns `True` if `PONG` is received.
- `send_command(command: str) -> str`: Sends a raw ASCII command and returns the response string.

#### TPIC6B595N Shift Register Methods
- `write_shift_registers(data: Union[bytes, bytearray, int, Sequence[int]]) -> None`:
  Shifts 64 bits (8 bytes, MSB first) across the 8-stage chain and pulses latch clock $R\_ck$.
  - `int`: 64-bit unsigned integer (e.g. `0x0123456789ABCDEF`).
  - `bytes` / `bytearray`: Exactly 8 bytes in length.
  - `Sequence[int]`: List/tuple of 8 integers (each `0-255`).
- `enable_shift_registers(enabled: bool = True) -> None`:
  Controls active-low output enable ($\overline{\text{G}}$). Passing `True` pulls $\overline{\text{G}}$ LOW (outputs enabled). Passing `False` pulls $\overline{\text{G}}$ HIGH (outputs disabled / high-impedance).
- `clear_shift_registers() -> None`:
  Pulses $\overline{\text{SRCLR}}$ LOW then HIGH to clear internal shift register stages.

#### MCP4802 DAC Methods
- `set_dac(channel: Union[int, str], value: int, gain_2x: bool = False, active: bool = True) -> None`:
  - `channel`: `0` or `'A'` for Channel A; `1` or `'B'` for Channel B.
  - `value`: 8-bit DAC code (`0` to `255`).
  - `gain_2x`: `False` for 1x gain ($V_{\text{ref}} = 2.048\text{V}$, output 0–2.048V), `True` for 2x gain ($2 \times V_{\text{ref}} = 4.096\text{V}$, output 0–4.096V subject to VDD headroom).
  - `active`: `True` for active buffered output, `False` for high-impedance shutdown.

#### Discrete Digital Output Methods
- `set_digital_output(pin: int, state: Union[bool, int]) -> None`:
  Sets the state of a digital output channel (`0`, `1`, `2`, or `3`) to `1` (HIGH) or `0` (LOW).
  - Channel 0: RP2040 GPIO 5 (Pico physical Pin 7)
  - Channel 1: RP2040 GPIO 6 (Pico physical Pin 9)
  - Channel 2: RP2040 GPIO 7 (Pico physical Pin 10)
  - Channel 3: RP2040 GPIO 8 (Pico physical Pin 11)
- `toggle_digital_output(pin: int) -> int`:
  Inverts the state of the specified channel (`0`, `1`, `2`, or `3`) and returns the new state (`0` or `1`).
- `get_digital_output(pin: int) -> int`:
  Queries the current state (`0` or `1`) of a single channel.
- `get_digital_outputs() -> Dict[int, int]`:
  Queries all 4 digital output channels and returns a dictionary, e.g. `{0: 0, 1: 1, 2: 0, 3: 0}`.

#### Exceptions
All library exceptions inherit from `GH2Error`:
- `GH2ConnectionError`: Raised when the serial port cannot be opened or writing/reading fails.
- `GH2TimeoutError`: Raised when the controller does not respond within the configured timeout period.
- `GH2ProtocolError`: Raised when the firmware returns an error response (`ERR <reason>`) or an invalid payload.

---

## Command Line Interface (CLI)

`gh2_api.py` includes a command-line interface built with Click for interactive testing and manual peripheral control.

### Usage

```bash
python gh2_api.py [OPTIONS]
```

### Options and Commands

| Option | Argument | Description |
| :--- | :--- | :--- |
| `-p`, `--port` | `TEXT` | Serial port (e.g. `/dev/ttyACM0` on Linux, `COM3` on Windows). Default: `/dev/ttyACM0`. |
| `-b`, `--baud` | `INTEGER` | Serial baud rate. Default: `115200`. |
| `-t`, `--test` | Flag | Run the automated hardware diagnostic smoke test suite. |
| `--ping` | Flag | Ping the Pico controller and verify connection (`PONG`). |
| `--get-pins` | Flag | Query and display states of all discrete digital output channels (0-3). |
| `--set-pin` | `PIN VAL` | Set digital output channel state (`VAL`: `0` or `1`). Allowed channels: `0`, `1`, `2`, `3`. |
| `--toggle-pin` | `PIN` | Invert the state of the specified digital output channel (`0-3`). |
| `--dac` | `CH VAL` | Write an 8-bit value (`0-255`) to DAC channel `0`/`1` or `A`/`B`. |
| `--sr-write` | `HEX64` | Write 64 bits to the shift registers as 16 hexadecimal characters. |
| `--sr-enable` | `[0\|1]` | Enable (`1`) or disable (`0`) shift register open-drain outputs. |
| `--sr-clear` | Flag | Pulse $\overline{\text{SRCLR}}$ low to reset internal shift registers. |
| `--help` | Flag | Show help message and exit. |

### CLI Examples

```bash
# 1. Ping the controller
python gh2_api.py --port /dev/ttyACM0 --ping

# 2. Run automated hardware diagnostics
python gh2_api.py --port /dev/ttyACM0 --test

# 3. Read all digital output states
python gh2_api.py --port /dev/ttyACM0 --get-pins

# 4. Turn on Channel 0 (HIGH)
python gh2_api.py --port /dev/ttyACM0 --set-pin 0 1

# 5. Toggle Channel 1
python gh2_api.py --port /dev/ttyACM0 --toggle-pin 1

# 6. Set DAC Channel A to mid-scale (approx 1.024V)
python gh2_api.py --port /dev/ttyACM0 --dac A 128

# 7. Clear shift registers, enable outputs, and shift a 64-bit pattern
python gh2_api.py --port /dev/ttyACM0 --sr-clear
python gh2_api.py --port /dev/ttyACM0 --sr-enable 1
python gh2_api.py --port /dev/ttyACM0 --sr-write AA55AA55AA55AA55
```

---

## USB Serial ASCII Protocol Specification

The Pico firmware and host communicate using ASCII strings terminated with `\r\n` or `\n`.

### Command Framing & Sentinel Character (`$`)

To make serial communication resilient against partial, interrupted, or noisy transmissions:
- Every command may be prefixed with the start sentinel character `$` (e.g. `$PING` or `$SR_WRITE ...`).
- **Buffer Clearing on Sentinel**: When the Pico firmware detects the `$` sentinel character, it **immediately flushes and clears any partial or stale data** currently accumulated in its receive buffer. This guarantees that if a previous command was interrupted or corrupted, subsequent commands will parse cleanly without framing desynchronization.
- **Client Framing**: The Python client library (`GH2Controller.send_command`) automatically prefixes outgoing commands with `$` and flushes any stale data from the serial input buffer prior to sending.
- **Interactive Compatibility**: The start sentinel is optional in interactive serial terminals (commands can be entered as either `PING` or `$PING`).

### Protocol Commands Reference

| Command | Arguments | Success Response | Error Response | Description |
| :--- | :--- | :--- | :--- | :--- |
| `PING` | *None* | `PONG` | `ERR ...` | Connection heartbeat |
| `SR_WRITE` | `<hex_64>` | `OK` | `ERR INVALID_HEX_LENGTH` | Shifts 8 bytes (16 hex chars) and pulses $R\_ck$ |
| `SR_ENABLE`| `<0\|1>` | `OK` | `ERR INVALID_ENABLE_ARG` | Controls $\overline{\text{G}}$ (`1` = LOW / enabled, `0` = HIGH / disabled) |
| `SR_CLEAR` | *None* | `OK` | `ERR ...` | Pulses $\overline{\text{SRCLR}}$ LOW then HIGH |
| `DAC_WRITE`| `<ch> <val> [gain] [act]` | `OK` | `ERR VALUE_OUT_OF_RANGE` | Writes 16-bit word to MCP4802 via SPI1 |
| `DOUT_SET` | `<pin:0-3> <val>` | `OK` | `ERR INVALID_PIN` | Sets output state of digital channel 0, 1, 2, or 3 |
| `DOUT_TOGGLE`| `<pin:0-3>` | `OK <new_val>` | `ERR INVALID_PIN` | Inverts specified output channel (0-3) |
| `DOUT_GET` | `[pin:0-3]` | `OK <pin:val ...>` | `ERR INVALID_PIN` | Reads state of specified channel or all channels |

---

## Running Automated Tests

Host-side tests verify protocol serialization, parameter constraints, mock loopback communication, Click CLI commands, and error handling without requiring physical hardware:

```bash
uv run pytest -v
```
