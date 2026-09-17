from machine import Pin


class DigitalOutputs:
    """
    Driver for 4-channel discrete digital outputs (channels 0-3).
    Maps channel index (0-3) directly to RP2040 GPIO lines:
      - Channel 0 -> RP2040 GPIO 5 (Raspberry Pi Pico physical Pin 7)
      - Channel 1 -> RP2040 GPIO 6 (Raspberry Pi Pico physical Pin 9)
      - Channel 2 -> RP2040 GPIO 7 (Raspberry Pi Pico physical Pin 10)
      - Channel 3 -> RP2040 GPIO 8 (Raspberry Pi Pico physical Pin 11)
    """

    # Map channel numbers 0-3 directly to RP2040 GPIO numbers.
    # Comments denote the corresponding physical board header pins on the Pico.
    PIN_MAP = {
        0: 5,  # Channel 0 -> RP2040 GPIO 5 (Pico physical Pin 7)
        1: 6,  # Channel 1 -> RP2040 GPIO 6 (Pico physical Pin 9)
        2: 7,  # Channel 2 -> RP2040 GPIO 7 (Pico physical Pin 10)
        3: 8,  # Channel 3 -> RP2040 GPIO 8 (Pico physical Pin 11)
    }
    VALID_PINS = (0, 1, 2, 3)

    def __init__(self, pins: tuple[int, ...] = VALID_PINS):
        self.pins: dict[int, Pin] = {}
        for pin_num in pins:
            if pin_num not in self.PIN_MAP:
                raise ValueError(
                    f"Pin {pin_num} is not a valid GH2 digital output pin {self.VALID_PINS}"
                )
            gpio_num = self.PIN_MAP[pin_num]
            # Default state: LOW
            self.pins[pin_num] = Pin(gpio_num, mode=Pin.OUT, value=0)

    def set(self, pin: int, value: int | bool) -> None:
        """Set output state of specified digital pin."""
        if pin not in self.pins:
            raise ValueError(
                f"Invalid pin: {pin}. Valid pins are {sorted(self.pins.keys())}"
            )
        self.pins[pin].value(1 if value else 0)

    def get(self, pin: int) -> int:
        """Get output state of specified digital pin."""
        if pin not in self.pins:
            raise ValueError(
                f"Invalid pin: {pin}. Valid pins are {sorted(self.pins.keys())}"
            )
        return self.pins[pin].value()

    def toggle(self, pin: int) -> int:
        """Toggle output state of specified digital pin and return new state."""
        if pin not in self.pins:
            raise ValueError(
                f"Invalid pin: {pin}. Valid pins are {sorted(self.pins.keys())}"
            )
        current = self.pins[pin].value()
        new_val = 0 if current else 1
        self.pins[pin].value(new_val)
        return new_val

    def get_all(self) -> dict[int, int]:
        """Return states of all configured digital output pins."""
        return {pin: self.pins[pin].value() for pin in sorted(self.pins.keys())}
