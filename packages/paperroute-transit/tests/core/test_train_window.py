"""Tests for the dashed slot and the tail of a train Strip."""

from collections.abc import Sequence
from dataclasses import replace

from paperroute_testkit.core.factories import inputs_of
from paperroute_transit.core.model import StopPlace
from paperroute_transit.core.reach import Reach, ReachRow, RowKind
from paperroute_transit.core.strip_model import (
    ROW_H,
    STRIP_TOP,
    Marks,
    StripInputs,
    StripRow,
    Window,
)
from paperroute_transit.core.strip_slots import home_of, ride_of
from paperroute_transit.core.strip_state import scroll_range
from paperroute_transit.core.strip_view import (
    build_strip_view,
    strip_row,
    strip_rows,
)
from paperroute_transit.core.train_window import (
    slot_indices,
    stub_text,
    windowed,
)

SLOTS = 12
MAIN = RowKind.MAIN
PASSED = RowKind.PASSED
EXIT = RowKind.EXIT
EARLIER = RowKind.EARLIER
ORIGIN = RowKind.ORIGIN


def reach_of(
    *, kinds: Sequence[RowKind], outside: frozenset[int] = frozenset(),
) -> Reach:
    """
    Lay out rows R0, R1 ... of the given kinds in a single file.

    Parameters
    ----------
    kinds : Sequence[RowKind]
        The kind of each row, top to bottom, with one ORIGIN.
    outside : frozenset[int]
        Rows whose arriving hop is outside the track.

    Returns
    -------
    Reach
        The rows; those above the From are `above` and parented to the
        row below, the others to the row above.
    """
    origin = kinds.index(ORIGIN)
    return Reach(
        rows=tuple(
            ReachRow(
                place=StopPlace(stop_id=f'R{i}', name=f'R{i}', point=None),
                kind=kind,
                depth=0,
                above=i < origin,
                parent=(
                    None if i == origin else i + 1 if i < origin else i - 1
                ),
                inside=i not in outside,
            )
            for i, kind in enumerate(kinds)
        ),
        stations=sum(kind is MAIN for kind in kinds),
        station_of={},
    )


def window_of_reach(
    *,
    reach: Reach,
    first: int,
    destination: int | None,
    scroll: int = 0,
) -> list[StripRow]:
    """
    Build the slot rows of a window and run them through `windowed`.

    Parameters
    ----------
    reach : Reach
        The reach.
    first : int
        The row in the top slot.
    destination : int | None
        The Destination's row.
    scroll : int
        The Strip's scroll.

    Returns
    -------
    list[StripRow]
        The window's rows.
    """
    marks = Marks(
        origin=next(
            i for i, row in enumerate(reach.rows) if row.kind is ORIGIN
        ),
        target=destination,
        ride=ride_of(reach=reach, target=destination),
        times={},
        cursor=None,
        picking=None,
    )
    window = Window(top=STRIP_TOP, rows=SLOTS)
    rows = [
        strip_row(
            reach=reach, index=index, first=first, marks=marks, window=window,
        )
        for index in slot_indices(
            first=first,
            slots=SLOTS,
            total=len(reach.rows),
            destination=destination,
        )
    ]
    return windowed(
        rows=rows,
        reach=reach,
        first=first,
        scroll=scroll,
        destination=destination,
        slots=SLOTS,
    )


def line_of(*, extra: Sequence[RowKind] = ()) -> list[RowKind]:
    """
    List the kinds of a From and `extra` rows below it.

    Parameters
    ----------
    extra : Sequence[RowKind]
        The kinds after the From.

    Returns
    -------
    list[RowKind]
        The From and the rows.
    """
    return [ORIGIN, *extra]


def test_stub_text() -> None:
    """The stub counts stops and passed stations, dropping zero parts."""
    assert stub_text(stops=0, passed=17, outside=False) == (
        '17 stations passed'
    )
    assert stub_text(stops=1, passed=1, outside=False) == (
        '1 stop, 1 station passed'
    )
    assert stub_text(stops=3, passed=5, outside=True) == '3 stops'
    assert stub_text(stops=0, passed=4, outside=True) == ''


def test_the_tail_goes_once_the_destination_reaches_the_last_slot() -> None:
    """Row 11 from row 0 fits; row 12 gets a stub and a pinned row."""
    reach = reach_of(kinds=line_of(extra=[MAIN] * 14))
    fits = window_of_reach(reach=reach, first=0, destination=11)
    assert RowKind.DASHED not in {row.kind for row in fits}
    assert fits[-1].station == 'R11'
    far = window_of_reach(reach=reach, first=0, destination=12)
    assert far[SLOTS - 2].kind is RowKind.DASHED
    assert far[SLOTS - 1].station == 'R12'
    assert [row.station for row in far[:SLOTS - 2]] == [
        f'R{i}' for i in range(SLOTS - 2)
    ]


def test_the_pinned_destination_sits_in_the_last_slot() -> None:
    """The Destination keeps its name, bold and ride, below the stub."""
    reach = reach_of(kinds=line_of(extra=[MAIN] * 14))
    far = window_of_reach(reach=reach, first=0, destination=14)
    stub, pinned = far[SLOTS - 2], far[SLOTS - 1]
    assert (pinned.name, pinned.bold, pinned.ride) == ('R14', True, True)
    assert pinned.y == STRIP_TOP + (SLOTS - 1) * ROW_H + ROW_H / 2
    assert pinned.parent_y == stub.y
    assert stub.parent_y == far[SLOTS - 3].y
    assert (stub.station, stub.time, stub.bold) == ('', '', False)


def test_a_tail_hides_exits_and_counts_stops_and_passed() -> None:
    """Stops and passed stations count from the stub's slot; exits do not."""
    kinds = line_of(extra=[MAIN] * 9) + [
        MAIN, PASSED, EXIT, MAIN, PASSED, PASSED, MAIN, MAIN,
    ]
    reach = reach_of(kinds=kinds)
    far = window_of_reach(reach=reach, first=0, destination=16)
    assert far[SLOTS - 2].name == '2 stops, 3 stations passed'
    assert far[SLOTS - 1].station == 'R16'


def test_a_tail_over_outside_hops_drops_the_passed_part() -> None:
    """A hidden hop outside the track leaves only the stop count."""
    kinds = line_of(extra=[MAIN] * 9) + [
        MAIN, PASSED, EXIT, MAIN, PASSED, PASSED, MAIN, MAIN,
    ]
    reach = reach_of(kinds=kinds, outside=frozenset({13}))
    far = window_of_reach(reach=reach, first=0, destination=16)
    assert far[SLOTS - 2].name == '2 stops'


def test_the_hop_into_the_destination_counts_for_outside() -> None:
    """The Destination row's own hop being outside drops the part too."""
    kinds = line_of(extra=[MAIN] * 9) + [
        MAIN, PASSED, MAIN, PASSED, PASSED, MAIN,
    ]
    reach = reach_of(kinds=kinds, outside=frozenset({15}))
    far = window_of_reach(reach=reach, first=0, destination=15)
    assert far[SLOTS - 2].name == '2 stops'
    inside = reach_of(kinds=kinds)
    far = window_of_reach(reach=inside, first=0, destination=15)
    assert far[SLOTS - 2].name == '2 stops, 3 stations passed'


def test_a_bare_stub_has_no_name() -> None:
    """With nothing to say the stub is a bare dashed row."""
    kinds = line_of(extra=[MAIN] * 9) + [EXIT, EXIT, MAIN]
    reach = reach_of(kinds=kinds)
    far = window_of_reach(reach=reach, first=0, destination=12)
    assert far[SLOTS - 2].name == ''
    assert far[SLOTS - 2].kind is RowKind.DASHED


def test_no_tail_without_a_destination() -> None:
    """Rows far down are cut at the window's edge, with no stub."""
    reach = reach_of(kinds=line_of(extra=[MAIN] * 20))
    rows = window_of_reach(reach=reach, first=0, destination=None)
    assert [row.station for row in rows] == [f'R{i}' for i in range(SLOTS)]
    assert RowKind.DASHED not in {row.kind for row in rows}


def test_two_earlier_calls_show_one_and_a_dash() -> None:
    """At rest, the top of two earlier calls is a bare dashed slot."""
    reach = reach_of(kinds=[EARLIER, EARLIER, *line_of(extra=[MAIN] * 12)])
    rows = window_of_reach(reach=reach, first=0, destination=None)
    assert rows[0].kind is RowKind.DASHED
    assert (rows[0].name, rows[0].time, rows[0].station) == ('', '', '')
    assert rows[1].kind is EARLIER and rows[1].station == 'R1'


def test_one_earlier_call_has_no_dash() -> None:
    """A single earlier call is drawn as it is."""
    reach = reach_of(kinds=[EARLIER, *line_of(extra=[MAIN] * 12)])
    rows = window_of_reach(reach=reach, first=0, destination=None)
    assert rows[0].kind is EARLIER


def test_scrolled_to_the_top_shows_every_earlier_call() -> None:
    """Once scrolled back, the slot shows the earlier call itself."""
    reach = reach_of(kinds=[EARLIER, EARLIER, *line_of(extra=[MAIN] * 12)])
    rows = window_of_reach(reach=reach, first=0, destination=None, scroll=-1)
    assert [row.kind for row in rows[:2]] == [EARLIER, EARLIER]
    assert rows[0].station == 'R0'


def test_two_earlier_calls_scroll_back_to_both() -> None:
    """One turn back is allowed even when home is the top row."""
    reach = reach_of(kinds=[EARLIER, EARLIER, *line_of(extra=[MAIN] * 12)])
    assert home_of(
        reach=reach, destination='R5', rows=SLOTS, end_points=True,
    ) == 0
    assert scroll_range(
        reach=reach, destination='R5', banner=False, end_points=True,
    )[0] == -1
    many = reach_of(kinds=[EARLIER] * 4 + line_of(extra=[MAIN] * 12))
    assert scroll_range(
        reach=many, destination='R5', banner=False, end_points=True,
    )[0] == -2


def test_no_earlier_calls_keep_the_old_range() -> None:
    """A train starting at the From scrolls no further up than 0."""
    reach = reach_of(kinds=line_of(extra=[MAIN] * 14))
    assert scroll_range(
        reach=reach, destination='R5', banner=False, end_points=True,
    )[0] == 0


def test_two_sides_put_the_from_in_the_sixth_slot() -> None:
    """With rows above and no Destination, five rows sit over the From."""
    kinds = [MAIN] * 8 + line_of(extra=[MAIN] * 8)
    reach = reach_of(kinds=kinds)
    assert home_of(
        reach=reach, destination=None, rows=SLOTS, end_points=True,
    ) == 3
    short = reach_of(kinds=[MAIN] * 3 + line_of(extra=[MAIN] * 8))
    assert home_of(
        reach=short, destination=None, rows=SLOTS, end_points=True,
    ) == 0


def test_one_side_puts_the_from_on_top() -> None:
    """A terminus has nothing above, so the From is the top slot."""
    reach = reach_of(kinds=line_of(extra=[MAIN] * 8))
    assert home_of(
        reach=reach, destination=None, rows=SLOTS, end_points=True,
    ) == 0


def test_a_destination_puts_the_from_by_the_earlier_calls() -> None:
    """The From sits below at most two earlier calls."""
    reach = reach_of(kinds=[EARLIER] * 3 + line_of(extra=[MAIN] * 8))
    assert home_of(
        reach=reach, destination='R5', rows=SLOTS, end_points=True,
    ) == 1
    one = reach_of(kinds=[EARLIER, *line_of(extra=[MAIN] * 8)])
    assert home_of(
        reach=one, destination='R5', rows=SLOTS, end_points=True,
    ) == 0


def test_an_exit_row_carries_its_names() -> None:
    """A Strip row keeps the places its exit row names."""
    reach = reach_of(kinds=line_of(extra=[MAIN, EXIT]))
    exit_row = replace(reach.rows[2], names=('Ely', 'Norwich'))
    shown = replace(reach, rows=(*reach.rows[:2], exit_row))
    marks = Marks(
        origin=0, target=None, ride=frozenset(), times={}, cursor=None,
        picking=None,
    )
    row = strip_row(
        reach=shown,
        index=2,
        first=0,
        marks=marks,
        window=Window(top=STRIP_TOP, rows=SLOTS),
    )
    assert row.names == ('Ely', 'Norwich')
    assert reach.rows[1].names == ()


def test_a_metro_strip_is_built_as_before() -> None:
    """With no end points, a far Destination gets no stub and no dash."""
    reach = reach_of(kinds=[EARLIER, EARLIER, *line_of(extra=[MAIN] * 20)])
    inputs = inputs_of(reach=reach, destination='R20')
    assert not inputs.end_points
    view = build_strip_view(inputs=inputs)
    kinds = {row.kind for row in view.rows}
    assert RowKind.DASHED not in kinds
    assert [row.name for row in view.rows] == [
        f'R{i}' for i in range(2, 2 + len(view.rows))
    ]
    assert all(row.names == () for row in view.rows)
    assert scroll_range(
        reach=reach, destination='R20', banner=False, end_points=False,
    ) == (-2, 9)


def test_an_end_point_strip_applies_the_window() -> None:
    """For an end-point kind `strip_rows` dashes the top and pins the end."""
    reach = reach_of(kinds=[EARLIER, EARLIER, *line_of(extra=[MAIN] * 20)])
    inputs = replace(
        inputs_of(reach=reach, destination='R21'), end_points=True,
    )
    rows, band = strip_rows(inputs=inputs, reach=reach)
    assert band is None
    assert rows[0].kind is RowKind.DASHED
    assert rows[-1].station == 'R21'
    assert rows[-2].kind is RowKind.DASHED


def end_point_inputs(
    *, reach: Reach, destination: str | None, scroll: int,
) -> StripInputs:
    """
    Make end-point Strip inputs on a reach at a scroll.

    Parameters
    ----------
    reach : Reach
        The reach.
    destination : str | None
        The Destination station ID.
    scroll : int
        The Strip's scroll.

    Returns
    -------
    StripInputs
        The inputs.
    """
    return replace(
        inputs_of(reach=reach, destination=destination),
        end_points=True,
        scroll=scroll,
    )


def test_a_short_run_leaves_the_window_at_home() -> None:
    """The window is not pulled up to fill itself; every turn moves it."""
    reach = reach_of(kinds=[EARLIER] * 8 + line_of(extra=[MAIN] * 5))
    low, high = scroll_range(
        reach=reach, destination='R13', banner=True, end_points=True,
    )
    assert (low, high) == (-6, 0)
    rest, _ = strip_rows(
        inputs=end_point_inputs(reach=reach, destination='R13', scroll=0),
        reach=reach,
    )
    assert [row.kind for row in rest] == [
        RowKind.DASHED, EARLIER, ORIGIN, *[MAIN] * 5,
    ]
    assert rest[1].station == 'R7'
    tops = []
    for scroll in range(0, low - 1, -1):
        shown, _ = strip_rows(
            inputs=end_point_inputs(
                reach=reach, destination='R13', scroll=scroll,
            ),
            reach=reach,
        )
        tops.append((shown[0].kind, shown[0].station))
    assert len(set(tops)) == len(tops)
    assert tops[-1] == (EARLIER, 'R0')


def test_two_sides_with_a_short_lower_side_keep_the_from_sixth() -> None:
    """The From stays in the sixth slot with few rows below it."""
    reach = reach_of(kinds=[MAIN] * 8 + line_of(extra=[MAIN] * 2))
    rows, _ = strip_rows(
        inputs=end_point_inputs(reach=reach, destination=None, scroll=0),
        reach=reach,
    )
    assert rows[5].station == 'R8'
    assert rows[0].station == 'R3'


def test_no_tail_while_the_from_is_below_the_window() -> None:
    """The bottom band takes the last slot; no stub or pinned end."""
    reach = reach_of(kinds=[EARLIER] * 20 + line_of(extra=[MAIN] * 20))
    rows, band = strip_rows(
        inputs=end_point_inputs(reach=reach, destination='R40', scroll=-12),
        reach=reach,
    )
    assert band is not None and not band.top
    assert RowKind.DASHED not in {row.kind for row in rows}
    assert all(row.kind is EARLIER for row in rows)
    ids = [int(row.station[1:]) for row in rows]
    assert ids == list(range(ids[0], ids[0] + len(ids)))


def bottom_band_rows(
    *, end_points: bool,
) -> tuple[list[StripRow], Reach]:
    """
    Lay out a Strip whose From is below the window, Destination inside.

    Parameters
    ----------
    end_points : bool
        Whether the kind is an end-point kind.

    Returns
    -------
    tuple[list[StripRow], Reach]
        The shown rows and the reach.
    """
    reach = reach_of(kinds=[MAIN] * 30 + line_of(extra=[MAIN] * 3))
    inputs = replace(
        inputs_of(reach=reach, destination='R10'),
        end_points=end_points,
        scroll=-20,
    )
    rows, band = strip_rows(inputs=inputs, reach=reach)
    assert band is not None and not band.top
    return list(rows), reach


def test_the_destination_stays_bold_under_a_bottom_band() -> None:
    """A Destination inside the window is bold, for every kind."""
    for end_points in (False, True):
        rows, _ = bottom_band_rows(end_points=end_points)
        by_station = {row.station: row for row in rows}
        assert by_station['R10'].bold, end_points
        assert RowKind.DASHED not in {row.kind for row in rows}
