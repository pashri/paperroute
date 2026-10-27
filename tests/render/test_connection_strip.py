"""Facts about the drawn connection Strip (no golden images)."""

from dataclasses import replace
from typing import Final

import numpy as np

from paperroute.core.connection_strip import (
    ConnectionStripView,
    LegSection,
    LegStop,
    StopMark,
    build_connection_strip,
)
from paperroute.render.canvas import HEIGHT, INK, PAPER, Canvas
from paperroute.render.chrome import HEADER
from paperroute.render.connection_strip import (
    HEAD_FACE,
    HEAD_GAP,
    HEAD_STEP,
    NAME_FACE,
    PAIR_STEP,
    PAIRS_LEFT,
    PAIRS_RIGHT,
    SIDE,
    STRIP_RIGHT,
    draw_connection_strip,
    draw_strip_sections,
    heading_parts,
    name_room,
    row_step,
)
from tests.core.connection_factories import WORK, at
from tests.core.test_connection_strip import CHAINS, PATTERNS, STOP

VIEW: Final[ConnectionStripView] = build_connection_strip(
    stop_view=STOP, pin=WORK, chains=CHAINS, selected=0, patterns=PATTERNS,
    positions={},
    now=at(7, 30),
)
LONG: Final[str] = 'Constitution Hill Interchange, North West Transitway ' * 2


def test_frame_is_one_bit_800_by_480() -> None:
    """Frames are 480 by 800 and use only ink and paper."""
    frame = draw_connection_strip(view=VIEW)
    assert frame.shape == (480, 800)
    assert set(frame.flatten().tolist()) <= {0, 255}


def test_rule_at_strip_right() -> None:
    """The column at x 440 is ink from the header down."""
    frame = draw_connection_strip(view=VIEW)
    assert frame[HEADER + 2:HEIGHT, STRIP_RIGHT + 1].max() == 0


def test_heading_bands_mostly_ink() -> None:
    """Each section heading is drawn as an ink band."""
    frame = draw_connection_strip(view=VIEW)
    heads = np.flatnonzero(
        (frame[HEADER:, 10:STRIP_RIGHT - 10] == INK).mean(axis=1) > 0.6,
    )
    starts = [
        y
        for y in heads.tolist()
        if not set(range(y - 6, y)) & set(heads.tolist())
    ]
    assert len(starts) == 2


def test_selected_pair_inverted() -> None:
    """The selected pair's band is inverted."""
    frame = draw_connection_strip(view=replace(VIEW, selected=1))
    first = frame[HEADER + 8:HEADER + PAIR_STEP - 8, PAIRS_LEFT:PAIRS_RIGHT]
    second = frame[
        HEADER + PAIR_STEP + 8:HEADER + 2 * PAIR_STEP - 8,
        PAIRS_LEFT:PAIRS_RIGHT,
    ]
    assert (second == INK).mean() > 0.5
    assert (first == INK).mean() < 0.5


def test_selected_pair_kept_on_screen() -> None:
    """A selected pair below the fold scrolls the list up."""
    rows = VIEW.pairs * 3
    frame = draw_connection_strip(
        view=replace(VIEW, pairs=rows, selected=len(rows) - 1),
    )
    inked = (frame[HEADER:, PAIRS_LEFT + 2] == INK).mean()
    assert inked > 0.1


def test_no_pairs_draws_empty_text() -> None:
    """With no pair, the empty text is drawn left of the rule."""
    view = replace(
        VIEW, sections=(), pairs=(), empty='No connection in the next 3 hours',
    )
    frame = draw_connection_strip(view=view)
    assert frame[HEADER + 4:HEIGHT, :STRIP_RIGHT].min() == 0
    assert frame[HEADER + 4:HEIGHT, STRIP_RIGHT + 4:].min() == PAPER


def test_loading_section_draws_text() -> None:
    """A section still loading draws its text under the heading."""
    section = replace(VIEW.sections[1], stops=(), empty='Loading the line…')
    canvas = Canvas()
    draw_strip_sections(
        canvas, sections=(VIEW.sections[0], section), left=0,
        right=STRIP_RIGHT,
    )
    grey = canvas.grey()
    assert grey[HEADER:HEIGHT, :STRIP_RIGHT].min() < 128


def test_pane_cuts_long_names() -> None:
    """Long names and headings are cut to the pane and end in '...'."""
    section = LegSection(
        route='665',
        mode=VIEW.sections[0].mode,
        title=LONG,
        note='walk 150 m, 7 min',
        stops=(
            LegStop(name=LONG, time='7:46', mark=StopMark.FIRST),
            LegStop(name=LONG, time='7:57', mark=StopMark.LAST),
        ),
        empty=None,
        vehicle=f'665 3 stops before {LONG}',
    )
    canvas = Canvas()
    draw_strip_sections(canvas, sections=(section,), left=483, right=786)
    grey = canvas.grey()
    assert grey[:, 787:].min() == PAPER
    assert grey[HEADER:, 483:786].min() < 128
    room = name_room(left=483, right=786)
    assert canvas.fit(text=LONG, face=NAME_FACE, width=room).endswith('...')
    assert canvas.fit(text=LONG, face=HEAD_FACE, width=room).endswith('...')


def test_heading_arrow_drawn_as_shape() -> None:
    """A heading's arrow is split out so it can be drawn as a shape."""
    assert heading_parts(title='A → B · walk') == ('A', 'B · walk')
    assert heading_parts(title='Loading') == ('Loading', None)


def test_fold_row_drawn() -> None:
    """A fold row draws its dotted track and its text."""
    fold = LegStop(name='5 stops', time='', mark=StopMark.FOLD)
    section = replace(VIEW.sections[0], stops=(fold,))
    canvas = Canvas()
    draw_strip_sections(canvas, sections=(section,), left=0, right=440)
    grey = canvas.grey()
    below = HEADER + 40
    assert grey[below:below + 30, 10:30].min() < 128
    assert grey[below:below + 30, 40:120].min() < 128


def test_three_sections_fit_strip() -> None:
    """Three folded sections with vehicle notes fit above the bottom."""
    section = replace(
        VIEW.sections[0],
        stops=(
            LegStop(name='A', time='7:33', mark=StopMark.FIRST),
            LegStop(name='9 stops', time='', mark=StopMark.FOLD),
            LegStop(name='B', time='7:50', mark=StopMark.LAST),
        ),
        vehicle='606 2 stops before A',
    )
    sections = (section,) * 3
    step = row_step(sections=sections, top=HEADER)
    used = 3 * (HEAD_STEP + HEAD_GAP) + 12 * step
    assert HEADER + used <= HEIGHT - SIDE
    canvas = Canvas()
    draw_strip_sections(canvas, sections=sections, left=0, right=STRIP_RIGHT)
    grey = canvas.grey()
    heads = np.flatnonzero(
        (grey[HEADER:, 10:STRIP_RIGHT - 10] < 128).mean(axis=1) > 0.6,
    ).tolist()
    starts = [y for y in heads if not set(range(y - 6, y)) & set(heads)]
    assert len(starts) == 3
