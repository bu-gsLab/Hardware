class ProtocolHandler:
    """ASCII protocol parser and dispatcher for GH2 firmware."""

    def __init__(self, sr_chain, dac, digital_out):
        self.sr = sr_chain
        self.dac = dac
        self.dout = digital_out

    def process_command(self, line: str) -> str:
        """Parse command line and execute corresponding peripheral operation."""
        line = line.strip()
        # Strip optional '$' start sentinel if passed directly
        if line.startswith("$"):
            line = line[1:].strip()
        if not line:
            return ""

        tokens = line.split()
        cmd = tokens[0].upper()
        args = tokens[1:]

        try:
            if cmd == "PING":
                return "PONG"

            elif cmd in ("SR_WRITE_BANK", "SR_BANK"):
                if len(args) != 2:
                    return "ERR MISSING_ARGUMENTS"
                try:
                    bank = int(args[0])
                except ValueError:
                    return "ERR INVALID_BANK"
                if not 0 <= bank < 4:
                    return "ERR INVALID_BANK"

                val_str = args[1]
                try:
                    if val_str.lower().startswith("0x"):
                        val = int(val_str, 16)
                    elif len(val_str) == 4 and all(c in "0123456789abcdefABCDEF" for c in val_str):
                        val = int(val_str, 16)
                    else:
                        val = int(val_str, 10)
                except ValueError:
                    return "ERR INVALID_VALUE"

                if not 0 <= val <= 0xFFFF:
                    return "ERR VALUE_OUT_OF_RANGE"

                if hasattr(self.sr, "write_bank"):
                    self.sr.write_bank(bank, val)
                else:
                    self._fallback_write_bank(bank, val)
                return "OK"

            elif cmd in ("SR_WRITE_CHANNEL", "SR_SET_CHANNEL", "SR_CHANNEL"):
                if len(args) != 3:
                    return "ERR MISSING_ARGUMENTS"
                try:
                    bank = int(args[0])
                except ValueError:
                    return "ERR INVALID_BANK"
                if not 0 <= bank < 4:
                    return "ERR INVALID_BANK"

                try:
                    channel = int(args[1])
                except ValueError:
                    return "ERR INVALID_CHANNEL"
                if not 0 <= channel < 16:
                    return "ERR INVALID_CHANNEL"

                if args[2] not in ("0", "1"):
                    return "ERR INVALID_VALUE"
                val = int(args[2])

                if hasattr(self.sr, "set_channel"):
                    self.sr.set_channel(bank, channel, val)
                else:
                    self._fallback_set_channel(bank, channel, val)
                return "OK"

            elif cmd == "SR_WRITE":
                if len(args) == 0:
                    return "ERR MISSING_ARGUMENTS"
                elif len(args) == 1:
                    hex_str = args[0]
                    if len(hex_str) != 16:
                        return "ERR INVALID_HEX_LENGTH"
                    try:
                        int(hex_str, 16)
                    except ValueError:
                        return "ERR INVALID_HEX"

                    try:
                        data = bytes.fromhex(hex_str)
                    except (AttributeError, ValueError):
                        data = bytes(int(hex_str[i : i + 2], 16) for i in range(0, 16, 2))

                    self.sr.write(data)
                    return "OK"
                elif len(args) == 2:
                    return self.process_command(f"SR_WRITE_BANK {args[0]} {args[1]}")
                elif len(args) == 3:
                    return self.process_command(f"SR_WRITE_CHANNEL {args[0]} {args[1]} {args[2]}")
                else:
                    return "ERR INVALID_ARG"

            elif cmd == "SR_GET":
                if not hasattr(self.sr, "state"):
                    return "ERR NOT_SUPPORTED"
                if len(args) == 0:
                    return f"OK {self.sr.state:016X}"
                elif len(args) == 1:
                    try:
                        bank = int(args[0])
                    except ValueError:
                        return "ERR INVALID_BANK"
                    if not 0 <= bank < 4:
                        return "ERR INVALID_BANK"
                    b_val = self.sr.get_bank(bank) if hasattr(self.sr, "get_bank") else ((self.sr.state >> (bank * 16)) & 0xFFFF)
                    return f"OK {bank}:{b_val:04X}"
                elif len(args) == 2:
                    try:
                        bank = int(args[0])
                        channel = int(args[1])
                    except ValueError:
                        return "ERR INVALID_ARG"
                    if not 0 <= bank < 4:
                        return "ERR INVALID_BANK"
                    if not 0 <= channel < 16:
                        return "ERR INVALID_CHANNEL"
                    ch_val = self.sr.get_channel(bank, channel) if hasattr(self.sr, "get_channel") else ((self.sr.state >> (bank * 16 + channel)) & 1)
                    return f"OK {bank}:{channel}:{ch_val}"
                else:
                    return "ERR INVALID_ARG"

            elif cmd == "SR_ENABLE":
                if len(args) != 1:
                    return "ERR MISSING_ARGUMENTS"
                if args[0] not in ("0", "1"):
                    return "ERR INVALID_ARG"
                self.sr.set_enabled(args[0] == "1")
                return "OK"

            elif cmd == "SR_CLEAR":
                self.sr.clear()
                return "OK"

            elif cmd == "DAC_WRITE":
                if len(args) < 2:
                    return "ERR MISSING_ARGUMENTS"

                ch_arg = args[0].upper()
                if ch_arg in ("0", "A"):
                    channel = 0
                elif ch_arg in ("1", "B"):
                    channel = 1
                else:
                    return "ERR INVALID_CHANNEL"

                try:
                    val = int(args[1])
                except ValueError:
                    return "ERR VALUE_OUT_OF_RANGE"
                if not 0 <= val <= 255:
                    return "ERR VALUE_OUT_OF_RANGE"

                gain_2x = False
                if len(args) >= 3:
                    if args[2] == "1":
                        gain_2x = True
                    elif args[2] == "0":
                        gain_2x = False
                    else:
                        return "ERR INVALID_GAIN"

                active = True
                if len(args) >= 4:
                    if args[3] == "1":
                        active = True
                    elif args[3] == "0":
                        active = False
                    else:
                        return "ERR INVALID_ACTIVE"

                self.dac.write(channel, val, gain_2x=gain_2x, active=active)
                return "OK"

            elif cmd == "DOUT_SET":
                if len(args) != 2:
                    return "ERR MISSING_ARGUMENTS"
                try:
                    pin = int(args[0])
                except ValueError:
                    return "ERR INVALID_PIN"
                if pin not in self.dout.VALID_PINS:
                    return "ERR INVALID_PIN"
                if args[1] not in ("0", "1"):
                    return "ERR INVALID_VALUE"

                self.dout.set(pin, args[1] == "1")
                return "OK"

            elif cmd == "DOUT_TOGGLE":
                if len(args) != 1:
                    return "ERR MISSING_ARGUMENTS"
                try:
                    pin = int(args[0])
                except ValueError:
                    return "ERR INVALID_PIN"
                if pin not in self.dout.VALID_PINS:
                    return "ERR INVALID_PIN"

                new_val = self.dout.toggle(pin)
                return f"OK {new_val}"

            elif cmd == "DOUT_GET":
                if len(args) == 0:
                    states = self.dout.get_all()
                    parts = [f"{pin}:{val}" for pin, val in states.items()]
                    return "OK " + " ".join(parts)
                elif len(args) == 1:
                    try:
                        pin = int(args[0])
                    except ValueError:
                        return "ERR INVALID_PIN"
                    if pin not in self.dout.VALID_PINS:
                        return "ERR INVALID_PIN"
                    val = self.dout.get(pin)
                    return f"OK {pin}:{val}"
                else:
                    return "ERR INVALID_ARG"

            else:
                return f"ERR UNKNOWN_COMMAND {cmd}"

        except Exception as e:
            return f"ERR {e}"

    def _fallback_write_bank(self, bank: int, value: int) -> None:
        current_state = getattr(self.sr, "state", 0)
        map_fn = getattr(self.sr, "map_channel", None)
        for c in range(16):
            bit_val = (value >> c) & 1
            bit_pos = map_fn(bank, c) if map_fn else (bank * 16 + c)
            if bit_val:
                current_state |= (1 << bit_pos)
            else:
                current_state &= ~(1 << bit_pos)
        self.sr.state = current_state
        if hasattr(self.sr, "write"):
            self.sr.write(current_state.to_bytes(8, "big"))

    def _fallback_set_channel(self, bank: int, channel: int, value: int) -> None:
        current_state = getattr(self.sr, "state", 0)
        map_fn = getattr(self.sr, "map_channel", None)
        bit_pos = map_fn(bank, channel) if map_fn else (bank * 16 + channel)
        if value:
            current_state |= (1 << bit_pos)
        else:
            current_state &= ~(1 << bit_pos)
        self.sr.state = current_state
        if hasattr(self.sr, "write"):
            self.sr.write(current_state.to_bytes(8, "big"))
