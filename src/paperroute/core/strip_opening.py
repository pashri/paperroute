"""Open a strip's folds as they reach the screen; shut them on leaving."""

from collections.abc import Callable
from dataclasses import replace

from paperroute.core.chooser import Stage, to_row
from paperroute.core.chooser_steps import chooser_reach, picked_stop_view
from paperroute.core.places import shown
from paperroute.core.reach import Reach, RowKind
from paperroute.core.saved import LiveView
from paperroute.core.state_model import FoldContext, State
from paperroute.core.strip_model import PANE_FROM_ROW, PANE_ROWS
from paperroute.core.strip_state import (
    STRIP_VIEWS,
    destination_id,
    picking_to,
    shown_reach,
    strip_key,
)
from paperroute.core.strip_view import first_row, home_of, origin_of, window_of

type Opener = Callable[[State, bool], State | None]


def fold_context(*, state: State) -> FoldContext | None:
    """
    Say which strip is on screen, for keeping its opened folds.

    Parameters
    ----------
    state : State
        State.

    Returns
    -------
    FoldContext | None
        The chooser's stage and strip while it is open; else the view,
        the shown place's strip and its Destination on a strip view;
        else None.
    """
    chooser = state.chooser
    if chooser is not None:
        return f'chooser:{chooser.stage}', chooser.key, None
    if state.view not in STRIP_VIEWS:
        return None
    return (
        state.view.value,
        strip_key(pin=shown(state=state)),
        destination_id(state=state),
    )


def refolded(*, state: State) -> State:
    """
    Shut every fold when the strip on screen changes.

    Parameters
    ----------
    state : State
        State.

    Returns
    -------
    State
        State as it is while the same strip is on screen; else with no
        fold open, noting the strip now on screen.
    """
    context = fold_context(state=state)
    if context == state.strip.context:
        return state
    return replace(state, strip=replace(
        state.strip, opened=frozenset(), context=context,
    ))


def fold_in(*, reach: Reach, first: int, rows: int) -> int | None:
    """
    Find a closed fold among the rows on screen.

    Parameters
    ----------
    reach : Reach
        The reach as shown.
    first : int
        The row in the top slot.
    rows : int
        Row slots.

    Returns
    -------
    int | None
        The first fold row on screen, or None.
    """
    return next(
        (
            index
            for index in range(first, min(first + rows, len(reach.rows)))
            if reach.rows[index].kind is RowKind.FOLD
        ),
        None,
    )


def with_opened(*, state: State, reach: Reach, index: int) -> State:
    """
    Open the fold on a row.

    Parameters
    ----------
    state : State
        State.
    reach : Reach
        The reach as shown.
    index : int
        The fold row.

    Returns
    -------
    State
        State with the fold's key among the opened ones.
    """
    key = reach.rows[index].place.stop_id
    return replace(state, strip=replace(
        state.strip, opened=state.strip.opened | {key},
    ))


def grown(*, row: int, reach: Reach, fold: int) -> int:
    """
    Find where a row moves to once a fold above it opens.

    Parameters
    ----------
    row : int
        The row.
    reach : Reach
        The reach as shown, fold closed.
    fold : int
        The fold row.

    Returns
    -------
    int
        The row moved down by the stations the fold adds, if it is below
        the fold.
    """
    return row + reach.rows[fold].folded - 1 if row > fold else row


def strip_opened(state: State, banner: bool) -> State | None:
    """
    Open one fold the Strip shows, keeping what is on screen in place.

    Parameters
    ----------
    state : State
        State on the Strip.
    banner : bool
        True while a live data banner shortens the strip.

    Returns
    -------
    State | None
        State with the fold open and scrolled so the rows on screen stay
        put; a fold in the top slot opens to its nearest station there.
        None when no fold is on screen.
    """
    reach = shown_reach(state=state)
    rows = window_of(banner=banner).rows
    destination = destination_id(state=state)
    if reach is None:
        return None
    first = first_row(
        scroll=state.strip.scroll,
        cursor=None,
        total=len(reach.rows),
        home=home_of(reach=reach, destination=destination, rows=rows),
        rows=rows,
    )
    fold = fold_in(reach=reach, first=first, rows=rows)
    if fold is None:
        return None
    grows = reach.rows[fold].folded - 1 if fold == first else 0
    return rescrolled(
        state=with_opened(state=state, reach=reach, index=fold),
        first=first + grows,
        rows=rows,
    )


def rescrolled(*, state: State, first: int, rows: int) -> State:
    """
    Scroll the Strip so a row is in its top slot.

    Parameters
    ----------
    state : State
        State on the Strip.
    first : int
        The row for the top slot.
    rows : int
        Row slots.

    Returns
    -------
    State
        State with the scroll that puts the row on top.
    """
    reach = shown_reach(state=state)
    home = 0 if reach is None else home_of(
        reach=reach, destination=destination_id(state=state), rows=rows,
    )
    return replace(state, strip=replace(state.strip, scroll=first - home))


def pane_opened(state: State, banner: bool) -> State | None:
    """
    Open one fold the Strip + map pane shows.

    Parameters
    ----------
    state : State
        State on Strip + map.
    banner : bool
        True while a live data banner shortens the pane.

    Returns
    -------
    State | None
        State with the fold open, or None when no fold is on screen.
    """
    reach = shown_reach(state=state)
    origin = None if reach is None else origin_of(reach=reach)
    if reach is None or origin is None:
        return None
    fold = fold_in(
        reach=reach,
        first=max(0, origin - PANE_FROM_ROW),
        rows=window_of(banner=banner, rows=PANE_ROWS).rows,
    )
    return None if fold is None else with_opened(
        state=state, reach=reach, index=fold,
    )


def chooser_opened(state: State, banner: bool) -> State | None:
    """
    Open one fold the TO stage's strip shows, the cursor staying put.

    Parameters
    ----------
    state : State
        State with the chooser in the TO stage.
    banner : bool
        Unused; the TO stage's own board says whether a banner shows.

    Returns
    -------
    State | None
        State with the fold open and the cursor on the same station, or
        None when no fold is on screen.
    """
    del banner
    chooser = picking_to(state=state)
    reach = None if chooser is None else chooser_reach(
        chooser=chooser, state=state,
    )
    origin = None if reach is None else origin_of(reach=reach)
    if chooser is None or reach is None or origin is None:
        return None
    cursor = to_row(chooser=chooser, reach=reach, origin=origin)
    fold = chooser_fold(state=state, reach=reach, cursor=cursor)
    return None if fold is None else replace(
        with_opened(state=state, reach=reach, index=fold),
        chooser=replace(chooser, to_cursor=grown(
            row=cursor, reach=reach, fold=fold,
        ) - grown(row=origin, reach=reach, fold=fold)),
    )


def chooser_fold(*, state: State, reach: Reach, cursor: int) -> int | None:
    """
    Find a closed fold on the TO stage's screen.

    Parameters
    ----------
    state : State
        State with the chooser in the TO stage.
    reach : Reach
        Its strip as shown.
    cursor : int
        The cursor's row; the screen follows it.

    Returns
    -------
    int | None
        The first fold row on screen, or None.
    """
    chooser = picking_to(state=state)
    banner = chooser is not None and picked_stop_view(
        state=state, chooser=chooser,
    ).banner is not None
    rows = window_of(banner=banner).rows
    first = first_row(
        scroll=0,
        cursor=cursor,
        total=len(reach.rows),
        home=home_of(reach=reach, destination=None, rows=rows),
        rows=rows,
    )
    return fold_in(reach=reach, first=first, rows=rows)


def opener_of(*, context: FoldContext | None) -> Opener | None:
    """
    Choose how folds open on the strip on screen.

    Parameters
    ----------
    context : FoldContext | None
        The strip on screen.

    Returns
    -------
    Opener | None
        The Strip's, the pane's or the TO stage's opener; None elsewhere.
    """
    openers: dict[str, Opener] = {
        LiveView.STRIP.value: strip_opened,
        LiveView.STRIP_MAP.value: pane_opened,
        f'chooser:{Stage.TO}': chooser_opened,
    }
    return None if context is None else openers.get(context[0])


def with_folds(*, state: State, banner: bool) -> State:
    """
    Open every fold the strip on screen shows, after shutting stale ones.

    Parameters
    ----------
    state : State
        State.
    banner : bool
        True while a live data banner shortens the Strip and the pane.

    Returns
    -------
    State
        State with the folds shut when the strip on screen changed, and
        then each fold on screen opened, until none shows.
    """
    state = refolded(state=state)
    opener = opener_of(context=state.strip.context)
    while opener is not None and (
        opened := opener(state, banner)
    ) is not None:
        state = opened
    return state
