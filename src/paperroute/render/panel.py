"""Draw the panel's settings screens."""

from collections.abc import Sequence
from typing import Final

import numpy as np
from numpy.typing import NDArray

from paperroute.core.device_model import WifiPhase
from paperroute.core.panel_views import (
    CANCEL_LINE,
    MENU_VISIBLE,
    TIMEOUT_LINE,
    ConfirmView,
    DeviceView,
    MenuView,
    PanelView,
    PhoneView,
    WifiSetupView,
)
from paperroute.core.stop_view import DOT
from paperroute.render.canvas import INK, PAPER, WIDTH, Canvas, Face
from paperroute.render.chrome import HEADER
from paperroute.render.qr import qr_image
from paperroute.render.stop_view import finish

ROW_H: Final[int] = 46
QR_PX: Final[int] = 8
MENU_TOP: Final[int] = HEADER + 24
CUE_H: Final[int] = 18
DEVICE_ROWS: Final[int] = 7
MARGIN: Final[int] = 32
FOOTER_Y: Final[int] = 456
VALUE_X: Final[int] = 250
INFO_TOP: Final[int] = 96
INFO_PITCH: Final[int] = 48
LINE_PITCH: Final[int] = 36
QR_BOTTOM: Final[int] = 436
HEADER_FACE: Final[Face] = Face(weight=700, px=20)
FOOTER_FACE: Final[Face] = Face(weight=400, px=20)
ROW_FACE: Final[Face] = Face(weight=400, px=24)
CHOSEN_FACE: Final[Face] = Face(weight=700, px=24)
LABEL_FACE: Final[Face] = Face(weight=700, px=20)
BODY_FACE: Final[Face] = Face(weight=400, px=20)
TITLE_FACE: Final[Face] = Face(weight=700, px=26)
NAME_FACE: Final[Face] = Face(weight=700, px=28)
QUESTION_FACE: Final[Face] = Face(weight=700, px=30)
BUTTON_FACE: Final[Face] = Face(weight=700, px=24)
LOCAL_NAME: Final[str] = 'paperroute.local'
SETUP_NETWORK: Final[str] = 'paperroute-setup'
MENU_FOOTER: Final[str] = (
    f'Bottom: turn to choose, press to open{DOT}top press: back'
)
DEVICE_FOOTER: Final[str] = f'Full details on {LOCAL_NAME}{DOT}top press: back'
PHONE_FOOTER: Final[str] = 'Top press: back'
CANCEL_FOOTER: Final[str] = 'Top press: cancel'
CONFIRM_FOOTER: Final[str] = TIMEOUT_LINE
CONFIRM_BOX: Final[tuple[int, int, int, int]] = (32, 290, 392, 354)
CANCEL_BOX: Final[tuple[int, int, int, int]] = (420, 290, 760, 354)
BUTTON_EDGE: Final[int] = 3
SETUP_TITLE: Final[str] = 'Join a Wi-Fi network'
SETUP_STEPS: Final[tuple[str, ...]] = (
    '1. Scan the code with your phone.',
    f'    It joins the network {SETUP_NETWORK}.',
    '2. Pick your Wi-Fi and type its password.',
    '3. paperroute connects and this screen closes.',
)
FAILED_TITLE: Final[str] = "Couldn't join"
SCANNING_TITLE: Final[str] = 'Looking for Wi-Fi networks...'
JOINING_DETAIL: Final[str] = 'This screen closes once it has joined.'


def panel_header(canvas: Canvas, *, title: str, clock: str) -> None:
    """
    Draw the black header bar with a title on the left and a clock right.

    Parameters
    ----------
    canvas : Canvas
        Target.
    title : str
        Screen title.
    clock : str
        Clock text; empty for none.
    """
    canvas.rect(box=(0, 0, WIDTH, HEADER), grey=INK)
    clock_w = canvas.text(
        at=(WIDTH - 16, 28),
        text=clock,
        face=HEADER_FACE,
        grey=PAPER,
        anchor='rs',
    )
    canvas.text(
        at=(16, 28),
        text=canvas.fit(
            text=title,
            face=HEADER_FACE,
            width=WIDTH - 48 - clock_w,
        ),
        face=HEADER_FACE,
        grey=PAPER,
    )


def footer(canvas: Canvas, *, text: str) -> None:
    """
    Draw the knob hint along the bottom.

    Parameters
    ----------
    canvas : Canvas
        Target.
    text : str
        The hint.
    """
    canvas.text(
        at=(MARGIN, FOOTER_Y),
        text=canvas.fit(text=text, face=FOOTER_FACE, width=WIDTH - 2 * MARGIN),
        face=FOOTER_FACE,
        grey=INK,
    )


def lines(
    canvas: Canvas,
    *,
    texts: Sequence[str],
    top: float,
    width: float,
    face: Face = BODY_FACE,
) -> None:
    """
    Draw left-aligned lines of text, one under the other.

    Parameters
    ----------
    canvas : Canvas
        Target.
    texts : Sequence[str]
        The lines.
    top : float
        Baseline of the first line.
    width : float
        Widest a line may be; longer ones are shortened.
    face : Face
        Font.
    """
    for index, text in enumerate(texts):
        canvas.text(
            at=(MARGIN, top + index * LINE_PITCH),
            text=canvas.fit(text=text, face=face, width=width),
            face=face,
            grey=INK,
        )


def chevron(canvas: Canvas, *, top: float, grey: int) -> None:
    """
    Draw the right-pointing chevron on a menu row.

    Parameters
    ----------
    canvas : Canvas
        Target.
    top : float
        The row's top edge.
    grey : int
        Stroke level.
    """
    mid = top + ROW_H / 2
    canvas.line(
        points=((760, mid - 10), (772, mid), (760, mid + 10)),
        width=4,
        grey=grey,
    )


def menu_row(canvas: Canvas, *, text: str, top: float, chosen: bool) -> None:
    """
    Draw one menu row, inverted with a chevron when chosen.

    Parameters
    ----------
    canvas : Canvas
        Target.
    text : str
        The row's label.
    top : float
        The row's top edge.
    chosen : bool
        Whether the cursor is on it.
    """
    grey = PAPER if chosen else INK
    if chosen:
        canvas.rect(box=(0, top, WIDTH, top + ROW_H), grey=INK)
        chevron(canvas, top=top, grey=PAPER)
    else:
        canvas.line(
            points=((0, top + ROW_H - 1), (WIDTH, top + ROW_H - 1)),
            width=1,
            grey=INK,
        )
    canvas.text(
        at=(MARGIN, top + ROW_H / 2),
        text=text,
        face=CHOSEN_FACE if chosen else ROW_FACE,
        grey=grey,
        anchor='lm',
    )


def scroll_cues(canvas: Canvas, *, view: MenuView) -> None:
    """
    Draw an up or down triangle where menu rows are hidden.

    Parameters
    ----------
    canvas : Canvas
        Target.
    view : MenuView
        The menu.
    """
    x = WIDTH - 30
    half = CUE_H / 2
    if view.first > 0:
        tip = MENU_TOP - CUE_H + 2
        canvas.polygon(
            points=((x - half, tip + half), (x + half, tip + half), (x, tip)),
            grey=INK,
        )
    if view.first + MENU_VISIBLE < len(view.rows):
        top = MENU_TOP + MENU_VISIBLE * ROW_H + 4
        canvas.polygon(
            points=((x - half, top), (x + half, top), (x, top + half)),
            grey=INK,
        )


def draw_menu(*, view: MenuView) -> NDArray[np.uint8]:
    """
    Draw the Settings menu, the visible rows from ``first``.

    Parameters
    ----------
    view : MenuView
        The menu.

    Returns
    -------
    NDArray[np.uint8]
        480 by 800 pixels, 0 or 255.
    """
    canvas = Canvas()
    panel_header(canvas, title='Settings', clock=view.clock)
    shown = view.rows[view.first:view.first + MENU_VISIBLE]
    for slot, text in enumerate(shown):
        menu_row(
            canvas,
            text=text,
            top=MENU_TOP + slot * ROW_H,
            chosen=view.first + slot == view.cursor,
        )
    scroll_cues(canvas, view=view)
    footer(canvas, text=MENU_FOOTER)
    return finish(canvas)


def draw_device(*, view: DeviceView) -> NDArray[np.uint8]:
    """
    Draw the Device summary, a label and value per line.

    Parameters
    ----------
    view : DeviceView
        The summary.

    Returns
    -------
    NDArray[np.uint8]
        480 by 800 pixels, 0 or 255.
    """
    canvas = Canvas()
    panel_header(canvas, title='Device', clock=view.clock)
    for index, row in enumerate(view.rows[:DEVICE_ROWS]):
        y = INFO_TOP + index * INFO_PITCH
        canvas.text(at=(MARGIN, y), text=row.label, face=LABEL_FACE, grey=INK)
        canvas.text(
            at=(VALUE_X, y),
            text=canvas.fit(
                text=row.value,
                face=BODY_FACE,
                width=WIDTH - VALUE_X - MARGIN,
            ),
            face=BODY_FACE,
            grey=INK,
        )
    footer(canvas, text=DEVICE_FOOTER)
    return finish(canvas)


def place_qr(canvas: Canvas, *, text: str) -> int:
    """
    Draw a QR code in the right half, centred between header and footer.

    Parameters
    ----------
    canvas : Canvas
        Target.
    text : str
        What the code holds.

    Returns
    -------
    int
        The code's left edge, quiet zone included.

    Raises
    ------
    ValueError
        When the code is too big for the right half.
    """
    image = qr_image(text=text, module_px=QR_PX)
    size = int(image.shape[0])
    if size > min(WIDTH // 2, QR_BOTTOM - HEADER):
        raise ValueError(f'QR code of {size} px does not fit its half')
    left = WIDTH - MARGIN - size
    top = HEADER + (QR_BOTTOM - HEADER - size) // 2
    canvas.image(grey=image, at=(left, top))
    return left


def phone_lines(*, view: PhoneView) -> tuple[str, ...]:
    """
    Say the addresses and network under the phone screen's name.

    Parameters
    ----------
    view : PhoneView
        The screen.

    Returns
    -------
    tuple[str, ...]
        One line per address, then the network line.
    """
    network = f' ({view.network})' if view.network else ''
    return (
        *(
            f'or {address}'
            for address in view.addresses[:2]
            if address != LOCAL_NAME
        ),
        f'Your phone must be on the same Wi-Fi{network}.',
    )


def draw_phone(*, view: PhoneView) -> NDArray[np.uint8]:
    """
    Draw the Open on your phone screen: the address and a code to it.

    Parameters
    ----------
    view : PhoneView
        The screen.

    Returns
    -------
    NDArray[np.uint8]
        480 by 800 pixels, 0 or 255.
    """
    canvas = Canvas()
    panel_header(canvas, title='Open on your phone', clock=view.clock)
    width = place_qr(canvas, text=view.url) - MARGIN
    lines(
        canvas,
        texts=('Open paperroute on your phone',),
        top=110,
        width=width,
        face=TITLE_FACE,
    )
    lines(canvas, texts=('Scan the code, or open:',), top=160, width=width)
    lines(
        canvas, texts=(LOCAL_NAME,), top=204, width=width, face=NAME_FACE,
    )
    lines(canvas, texts=phone_lines(view=view), top=246, width=width)
    footer(canvas, text=PHONE_FOOTER)
    return finish(canvas)


def spaced(password: str) -> str:
    """
    Split a password into groups of four to read it out.

    Parameters
    ----------
    password : str
        The password.

    Returns
    -------
    str
        Groups of four characters joined by spaces.
    """
    return ' '.join(
        password[start:start + 4]
        for start in range(0, len(password), 4)
    )


def setup_lines(*, view: WifiSetupView) -> tuple[str, ...]:
    """
    Say the hotspot steps and the no-camera fallback.

    Parameters
    ----------
    view : WifiSetupView
        The screen.

    Returns
    -------
    tuple[str, ...]
        The lines, a blank line before the fallback.
    """
    return (
        *SETUP_STEPS,
        '',
        f'No camera? Join {SETUP_NETWORK},',
        f'password {spaced(view.password)}, open {view.address}',
    )


def failed_line(*, view: WifiSetupView) -> str:
    """
    Say why the last join failed.

    Parameters
    ----------
    view : WifiSetupView
        The screen, in the failed phase.

    Returns
    -------
    str
        The error, else the network's name, else empty.
    """
    return view.error or view.ssid or ''


def draw_hotspot(canvas: Canvas, *, view: WifiSetupView) -> None:
    """
    Draw the hotspot's code, steps and timeout.

    Parameters
    ----------
    canvas : Canvas
        Target.
    view : WifiSetupView
        The screen, in the hotspot or failed phase.
    """
    width = place_qr(canvas, text=view.qr) - MARGIN
    failed = view.phase is WifiPhase.FAILED
    title = FAILED_TITLE if failed else SETUP_TITLE
    lines(canvas, texts=(title,), top=110, width=width, face=TITLE_FACE)
    if failed:
        lines(canvas, texts=(failed_line(view=view),), top=150, width=width)
    lines(
        canvas,
        texts=setup_lines(view=view),
        top=190 if failed else 160,
        width=width,
    )
    footer(
        canvas,
        text=(
            f'New password each time{DOT}closes in {view.minutes_left} min'
            f'{DOT}top press: cancel'
        ),
    )


def draw_waiting(canvas: Canvas, *, view: WifiSetupView) -> None:
    """
    Draw the scanning or joining message.

    Parameters
    ----------
    canvas : Canvas
        Target.
    view : WifiSetupView
        The screen, in the scanning or joining phase.
    """
    width = WIDTH - 2 * MARGIN
    joining = view.phase is WifiPhase.JOINING
    title = (
        f'Joining {view.ssid or "the network"}...'
        if joining
        else SCANNING_TITLE
    )
    lines(canvas, texts=(title,), top=110, width=width, face=TITLE_FACE)
    if joining:
        lines(canvas, texts=(JOINING_DETAIL,), top=160, width=width)
    footer(canvas, text=CANCEL_FOOTER)


def draw_wifi_setup(*, view: WifiSetupView) -> NDArray[np.uint8]:
    """
    Draw the Wi-Fi setup screen for its phase.

    Parameters
    ----------
    view : WifiSetupView
        The screen.

    Returns
    -------
    NDArray[np.uint8]
        480 by 800 pixels, 0 or 255.
    """
    canvas = Canvas()
    panel_header(canvas, title='Wi-Fi setup', clock=view.clock)
    if view.phase in (WifiPhase.HOTSPOT, WifiPhase.FAILED):
        draw_hotspot(canvas, view=view)
    else:
        draw_waiting(canvas, view=view)
    return finish(canvas)


def button(
    canvas: Canvas,
    *,
    box: tuple[int, int, int, int],
    text: str,
    filled: bool,
) -> None:
    """
    Draw a knob button: filled ink, or outlined.

    Parameters
    ----------
    canvas : Canvas
        Target.
    box : tuple[int, int, int, int]
        Left, top, right, bottom.
    text : str
        The label.
    filled : bool
        Whether the button is solid ink.
    """
    left, top, right, bottom = box
    canvas.rect(box=box, grey=INK)
    if not filled:
        edge = BUTTON_EDGE
        canvas.rect(
            box=(left + edge, top + edge, right - edge, bottom - edge),
            grey=PAPER,
        )
    canvas.text(
        at=((left + right) / 2, (top + bottom) / 2),
        text=canvas.fit(text=text, face=BUTTON_FACE, width=right - left - 16),
        face=BUTTON_FACE,
        grey=PAPER if filled else INK,
        anchor='mm',
    )


def draw_confirm(*, view: ConfirmView) -> NDArray[np.uint8]:
    """
    Draw a confirm: the question, its lines and the two knob buttons.

    Parameters
    ----------
    view : ConfirmView
        The question.

    Returns
    -------
    NDArray[np.uint8]
        480 by 800 pixels, 0 or 255.
    """
    canvas = Canvas()
    panel_header(canvas, title=view.title, clock='')
    width = WIDTH - 2 * MARGIN
    lines(
        canvas,
        texts=(view.question,),
        top=140,
        width=width,
        face=QUESTION_FACE,
    )
    body = tuple(
        line
        for line in view.lines
        if line not in (CANCEL_LINE, TIMEOUT_LINE)
    )
    lines(canvas, texts=body[:2], top=190, width=width)
    button(canvas, box=CONFIRM_BOX, text=view.confirm, filled=True)
    button(canvas, box=CANCEL_BOX, text=CANCEL_LINE, filled=False)
    footer(canvas, text=CONFIRM_FOOTER)
    return finish(canvas)


def draw_panel_view(*, view: PanelView) -> NDArray[np.uint8]:
    """
    Draw any panel screen.

    Parameters
    ----------
    view : PanelView
        The screen.

    Returns
    -------
    NDArray[np.uint8]
        480 by 800 pixels, 0 or 255.
    """
    match view:
        case MenuView():
            return draw_menu(view=view)
        case DeviceView():
            return draw_device(view=view)
        case PhoneView():
            return draw_phone(view=view)
        case WifiSetupView():
            return draw_wifi_setup(view=view)
    return draw_confirm(view=view)
