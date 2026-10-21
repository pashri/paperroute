"""Draw a one-line notice, such as the shutdown screen."""

from typing import Final

import numpy as np
from numpy.typing import NDArray

from paperroute.core.messages import NoticeView
from paperroute.render.canvas import HEIGHT, INK, WIDTH, Canvas, Face
from paperroute.render.stop_view import finish

NOTICE_FACE: Final[Face] = Face(weight=700, px=36)


def draw_notice_view(*, view: NoticeView) -> NDArray[np.uint8]:
    """
    Draw a notice's title centred on paper, as a 1-bit frame.

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
    return finish(canvas)
