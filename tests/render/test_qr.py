"""Tests for QR code images."""

from paperroute.render.canvas import INK, PAPER
from paperroute.render.qr import QUIET, qr_image


def test_qr_size_and_quiet_zone() -> None:
    """A version 2 code is 33 modules square with a paper border."""
    image = qr_image(text='http://paperroute.local/', module_px=8)
    border = QUIET * 8
    assert image.shape == ((25 + 8) * 8, (25 + 8) * 8)
    assert set(image.flatten().tolist()) == {INK, PAPER}
    assert (image[:border] == PAPER).all()
    assert (image[:, :border] == PAPER).all()
    assert (image[-border:] == PAPER).all()
    assert (image[:, -border:] == PAPER).all()
    assert image[border, border] == INK


def test_wifi_qr_fits() -> None:
    """The hotspot code at 8 px fits in 360 px."""
    image = qr_image(
        text='WIFI:T:WPA;S:paperroute-setup;P:7Q4K9W2M;;',
        module_px=8,
    )
    assert image.shape[0] <= 360
