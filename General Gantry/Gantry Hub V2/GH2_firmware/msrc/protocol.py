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

            elif cmd == "SR_WRITE":
                if len(args) != 1:
                    return "ERR MISSING_ARGUMENTS"
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
