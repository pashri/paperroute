"""Tests for the panel's settings screens."""

import numpy as np
import pytest
from numpy.typing import NDArray

from paperroute.core.device_model import WifiPhase
from paperroute.core.panel_views import (
    ConfirmView,
    DeviceView,
    InfoRow,
    MenuView,
    PhoneView,
    WifiSetupView,
)
from paperroute.render.canvas import HEIGHT, INK, WIDTH
from paperroute.render.chrome import HEADER
from paperroute.render.panel import (
    CUE_H,
    FOOTER_Y,
    MENU_TOP,
    MENU_VISIBLE,
    QR_PX,
    ROW_H,
    draw_panel_view,
)
from paperroute.render.qr import qr_image

WIFI_QR = 'WIFI:T:WPA;S:paperroute-setup;P:7Q4K9W2M;;'
ROWS = (
    'Wi-Fi',
    'Device',
    'Open on your phone',
    'Sleep now',
    'Rebuild timetable now',
    'Restart',
    'Shut down',
    'Reset settings',
)


def finder_run(frame: NDArray[np.uint8]) -> bool:
    """
    Say whether the right half holds a finder pattern's top edge.

    Parameters
    ----------
    frame : NDArray[np.uint8]
        A panel frame.

    Returns
    -------
    bool
        True when some row has 7 modules of ink in a row.
    """
    ink = (frame[HEADER:, WIDTH // 2:] == INK).astype(np.int32)
    width = 7 * QR_PX
    sums = np.cumsum(np.pad(ink, ((0, 0), (1, 0))), axis=1)
    return bool((sums[:, width:] - sums[:, :-width] == width).any())


def row_band(*, frame: NDArray[np.uint8], slot: int) -> NDArray[np.uint8]:
    """
    Cut out one menu slot.

    Parameters
    ----------
    frame : NDArray[np.uint8]
        A menu frame.
    slot : int
        Visible slot, from 0.

    Returns
    -------
    NDArray[np.uint8]
        The slot's pixels.
    """
    top = MENU_TOP + slot * ROW_H
    return frame[top + 2:top + ROW_H - 2]


def test_menu_inverts_chosen_row() -> None:
    """The cursor's slot is mostly ink and the others mostly paper."""
    frame = draw_panel_view(
        view=MenuView(clock='21:52', rows=ROWS, cursor=2, first=0),
    )
    assert frame.shape == (HEIGHT, WIDTH)
    for slot in range(MENU_VISIBLE):
        inked = (row_band(frame=frame, slot=slot) == INK).mean()
        assert (inked > 0.5) == (slot == 2)


def test_menu_scrolls_to_last_row() -> None:
    """With first at 1, Reset settings fills the last slot, chosen."""
    top = draw_panel_view(
        view=MenuView(clock='21:52', rows=ROWS, cursor=7, first=1),
    )
    alone = draw_panel_view(
        view=MenuView(
            clock='21:52', rows=ROWS[1:], cursor=6, first=0,
        ),
    )
    assert (top[MENU_TOP:] == alone[MENU_TOP:]).all()
    assert (row_band(frame=top, slot=MENU_VISIBLE - 1) == INK).mean() > 0.5


def test_confirm_frame() -> None:
    """The confirm draws its header, text and buttons below the header."""
    frame = draw_panel_view(
        view=ConfirmView(
            title='Shut down?',
            question='Shut down paperroute?',
            lines=('Asked for from a phone on your Wi-Fi.',),
            confirm='Bottom press: shut down',
        ),
    )
    assert (frame[:HEADER] == INK).mean() > 0.8
    assert (frame[HEADER + 40:HEIGHT // 2] == INK).any()
    assert (frame[280:370] == INK).mean() > 0.1


def test_device_frame() -> None:
    """Each info row inks its label and value columns."""
    rows = (
        InfoRow(label='Wi-Fi', value='Home · strong'),
        InfoRow(label='Clock', value='synced'),
    )
    frame = draw_panel_view(view=DeviceView(clock='21:52', rows=rows))
    body = frame[HEADER + 20:HEADER + 120]
    assert (body[:, :200] == INK).any()
    assert (body[:, 250:] == INK).any()
    assert (frame[440:] == INK).any()


def test_phone_frame_has_qr() -> None:
    """The phone screen has a QR code in the right half."""
    frame = draw_panel_view(
        view=PhoneView(
            clock='21:52',
            url='http://paperroute.local/',
            addresses=('192.168.1.40',),
            network='Home',
        ),
    )
    assert finder_run(frame)


def test_wifi_setup_frame_has_qr() -> None:
    """The hotspot screen has a QR code in the right half."""
    frame = draw_panel_view(
        view=WifiSetupView(
            clock='21:52',
            phase=WifiPhase.HOTSPOT,
            qr='WIFI:T:WPA;S:paperroute-setup;P:7Q4K9W2M;;',
            password='7Q4K9W2M',
            address='10.42.0.1',
            minutes_left=10,
            ssid=None,
            error=None,
        ),
    )
    assert finder_run(frame)


def test_wifi_setup_hides_password_in_repr() -> None:
    """The password and the code carrying it stay out of the repr."""
    view = WifiSetupView(
        clock='21:52',
        phase=WifiPhase.SCANNING,
        qr='WIFI:T:WPA;S:paperroute-setup;P:7Q4K9W2M;;',
        password='7Q4K9W2M',
        address='10.42.0.1',
        minutes_left=10,
        ssid=None,
        error=None,
    )
    assert '7Q4K9W2M' not in repr(view)
    assert not finder_run(draw_panel_view(view=view))


def cue_ink(*, frame: NDArray[np.uint8], top: int) -> bool:
    """
    Say whether a scroll cue is drawn at the list's right edge.

    Parameters
    ----------
    frame : NDArray[np.uint8]
        A menu frame.
    top : int
        Top row of the cue's band.

    Returns
    -------
    bool
        True when the band has ink.
    """
    return bool((frame[top:top + CUE_H, WIDTH - 48:] == INK).any())


def test_menu_shows_scroll_cues() -> None:
    """Hidden rows below or above get a cue; a full view gets none."""
    below = MENU_TOP + MENU_VISIBLE * ROW_H
    above = MENU_TOP - CUE_H
    first = draw_panel_view(
        view=MenuView(clock='', rows=ROWS, cursor=0, first=0),
    )
    last = draw_panel_view(
        view=MenuView(clock='', rows=ROWS, cursor=7, first=1),
    )
    short = draw_panel_view(
        view=MenuView(clock='', rows=ROWS[:7], cursor=0, first=0),
    )
    assert cue_ink(frame=first, top=below)
    assert not cue_ink(frame=first, top=above)
    assert cue_ink(frame=last, top=above)
    assert not cue_ink(frame=last, top=below)
    assert not cue_ink(frame=short, top=below)


def test_device_draws_seven_rows() -> None:
    """An eighth row is not drawn over the footer."""
    rows = tuple(
        InfoRow(label=f'Row {index}', value='value')
        for index in range(8)
    )
    assert (
        draw_panel_view(view=DeviceView(clock='', rows=rows))
        == draw_panel_view(view=DeviceView(clock='', rows=rows[:7]))
    ).all()


def test_wifi_failed_fits_long_ssid() -> None:
    """A failed join says so shortly and fits a long network name."""
    ssid = 'A' * 32
    frame = draw_panel_view(
        view=WifiSetupView(
            clock='21:52',
            phase=WifiPhase.FAILED,
            qr=WIFI_QR,
            password='7Q4K9W2M',
            address='10.42.0.1',
            minutes_left=10,
            ssid=ssid,
            error=f'Wrong password for {ssid}',
        ),
    )
    assert finder_run(frame)
    size = qr_image(text=WIFI_QR, module_px=QR_PX).shape[0]
    quiet_left = WIDTH - 32 - size
    gap = frame[HEADER:FOOTER_Y - 30, quiet_left:quiet_left + 32]
    assert not (gap == INK).any()


def test_qr_too_big_raises() -> None:
    """A code that would not fit its half is a builder bug."""
    with pytest.raises(ValueError, match='QR'):
        draw_panel_view(
            view=PhoneView(
                clock='', url='x' * 200, addresses=(), network=None,
            ),
        )
