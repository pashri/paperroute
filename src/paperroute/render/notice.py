"""Draw a notice, such as the shutdown screen."""

from typing import Final

import numpy as np
from numpy.typing import NDArray

from paperroute.core.messages import NoticeView
from paperroute_device.render.canvas import HEIGHT, INK, WIDTH, Canvas, Face
from paperroute_device.render.frame import finish

NOTICE_FACE: Final[Face] = Face(weight=700, px=36)
DETAIL_FACE: Final[Face] = Face(weight=400, px=20)
DETAIL_GAP: Final[int] = 44


def draw_notice_view(*, view: NoticeView) -> NDArray[np.uint8]:
    """
    Draw a notice's title centred on paper, and its detail under it.

    Parameters
    ----------
    view : NoticeView
        The notice.

    Returns
    -------
    NDArray[np.uint8]
        480 by 800 pixels, 0 or 255.
    """
    canvas = Canvas()
    canvas.text(
        at=(WIDTH / 2, HEIGHT / 2),
        text=view.title,
        face=NOTICE_FACE,
        grey=INK,
        anchor='mm',
    )
    if view.detail:
        canvas.text(
            at=(WIDTH / 2, HEIGHT / 2 + DETAIL_GAP),
            text=view.detail,
            face=DETAIL_FACE,
            grey=INK,
            anchor='mm',
        )
    return finish(canvas)
