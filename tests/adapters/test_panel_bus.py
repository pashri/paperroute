"""Tests for the Raspberry Pi panel bus helpers."""

import os
from pathlib import Path

from paperroute.adapters.panel_bus import (
    CHUNK,
    SPI_IOC_WR_MAX_SPEED_HZ,
    SPI_IOC_WR_MODE,
    write_all,
)


def test_write_all_chunks(tmp_path: Path) -> None:
    """A large payload is written whole, in pieces of at most CHUNK."""
    sizes: list[int] = []

    def writer(fd: int, data: bytes) -> int:
        sizes.append(len(data))
        return os.write(fd, data[: len(data) - 1] if len(data) > 1 else data)

    payload = bytes(range(256)) * 40
    target = tmp_path / 'out'
    fd = os.open(target, os.O_WRONLY | os.O_CREAT)
    try:
        write_all(fd=fd, payload=payload, writer=writer)
    finally:
        os.close(fd)
    assert target.read_bytes() == payload
    assert max(sizes) <= CHUNK
    assert len(payload) > 2 * CHUNK


def test_ioctl_numbers() -> None:
    """The spidev request numbers are the kernel's."""
    assert SPI_IOC_WR_MODE == 0x40016B01
    assert SPI_IOC_WR_MAX_SPEED_HZ == 0x40046B04
    assert CHUNK == 4096
