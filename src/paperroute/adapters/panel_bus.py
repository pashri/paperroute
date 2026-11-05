"""The Raspberry Pi wires to the e-paper HAT: gpiod lines and spidev."""

import fcntl
import os
import struct
import time
from collections.abc import Callable
from typing import Any, Final

RST: Final[int] = 17
DC: Final[int] = 25
BUSY: Final[int] = 24
PWR: Final[int] = 18
SPI_IOC_WR_MODE: Final[int] = 0x40016B01
SPI_IOC_WR_MAX_SPEED_HZ: Final[int] = 0x40046B04
SPI_SPEED: Final[int] = 4_000_000  # Hz
CHUNK: Final[int] = 4096  # bytes
RESET_HIGH: Final[float] = 0.02  # seconds
RESET_LOW: Final[float] = 0.002  # seconds


def write_all(
    *,
    fd: int,
    payload: bytes,
    writer: Callable[[int, bytes], int] = os.write,
) -> None:
    """
    Write bytes to a file descriptor in pieces of at most CHUNK bytes.

    Parameters
    ----------
    fd : int
        The open file descriptor.
    payload : bytes
        The bytes to write.
    writer : Callable[[int, bytes], int]
        Writes some bytes and returns how many it took.

    Raises
    ------
    OSError
        If the writer takes no bytes.
    """
    view = memoryview(payload)
    while view:
        written = writer(fd, bytes(view[:CHUNK]))
        if written <= 0:
            raise OSError('write made no progress')
        view = view[written:]


class LinuxPanelBus:  # pragma: no cover
    """
    The panel's pins and SPI device on a Raspberry Pi.

    Parameters
    ----------
    lines : Any
        The gpiod line request.
    fd : int
        The open SPI device.
    value : Any
        The gpiod line Value enum.
    """

    def __init__(self, *, lines: Any, fd: int, value: Any) -> None:
        self.lines = lines
        self.fd = fd
        self.value = value

    @classmethod
    def open(
        cls,
        *,
        chip: str = '/dev/gpiochip0',
        spi: str = '/dev/spidev0.0',
    ) -> 'LinuxPanelBus':
        """
        Claim the GPIO lines and the SPI device.

        Parameters
        ----------
        chip : str
            The GPIO character device.
        spi : str
            The SPI character device.

        Returns
        -------
        LinuxPanelBus
            The open bus.

        Raises
        ------
        OSError
            If the lines or device are unavailable, for example EBUSY
            when another process holds them.
        """
        # pylint: disable=import-error,import-outside-toplevel
        import gpiod
        from gpiod import line

        # pylint: enable=import-error,import-outside-toplevel

        lines = gpiod.request_lines(
            chip,
            consumer='paperroute',
            config={
                (RST, DC, PWR): gpiod.LineSettings(
                    direction=line.Direction.OUTPUT,
                    output_value=line.Value.INACTIVE,
                ),
                BUSY: gpiod.LineSettings(direction=line.Direction.INPUT),
            },
        )
        fd = -1
        try:
            fd = os.open(spi, os.O_WRONLY)
            fcntl.ioctl(fd, SPI_IOC_WR_MODE, struct.pack('B', 0))
            fcntl.ioctl(
                fd, SPI_IOC_WR_MAX_SPEED_HZ, struct.pack('I', SPI_SPEED)
            )
        except OSError:
            if fd >= 0:
                os.close(fd)
            lines.release()
            raise
        return cls(lines=lines, fd=fd, value=line.Value)

    def set_pin(self, *, pin: int, high: bool) -> None:
        """
        Drive an output pin.

        Parameters
        ----------
        pin : int
            The BCM line offset.
        high : bool
            True for 1, False for 0.
        """
        level = self.value.ACTIVE if high else self.value.INACTIVE
        self.lines.set_value(pin, level)

    def reset(self) -> None:
        """Pulse the reset pin: high 20 ms, low 2 ms, high 20 ms."""
        self.set_pin(pin=RST, high=True)
        time.sleep(RESET_HIGH)
        self.set_pin(pin=RST, high=False)
        time.sleep(RESET_LOW)
        self.set_pin(pin=RST, high=True)
        time.sleep(RESET_HIGH)

    def command(self, *, code: int, data: bytes = b'') -> None:
        """
        Send a command byte, then its data.

        Parameters
        ----------
        code : int
            The command byte, sent with the data/command pin low.
        data : bytes
            The data bytes, sent with the pin high.
        """
        self.set_pin(pin=DC, high=False)
        write_all(fd=self.fd, payload=bytes([code]))
        if data:
            self.set_pin(pin=DC, high=True)
            write_all(fd=self.fd, payload=data)

    def idle(self) -> bool:
        """
        Read the busy pin.

        Returns
        -------
        bool
            True when the pin reads 1, which means the panel is idle.
        """
        return bool(self.lines.get_value(BUSY) == self.value.ACTIVE)

    def pause(self, *, seconds: float) -> None:
        """
        Wait.

        Parameters
        ----------
        seconds : float
            How long to wait.
        """
        time.sleep(seconds)

    def power(self, *, on: bool) -> None:
        """
        Switch the panel's power pin.

        Parameters
        ----------
        on : bool
            True to power the panel.
        """
        self.set_pin(pin=PWR, high=on)

    def close(self) -> None:
        """Release the lines and close the SPI device."""
        os.close(self.fd)
        self.lines.release()
