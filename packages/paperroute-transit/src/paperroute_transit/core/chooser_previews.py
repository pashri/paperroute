"""Reading what every row of a chooser column opens, in one job."""

import dataclasses
from typing import Final, Protocol

from paperroute_transit.core.chooser import (
    COLLAPSIBLE,
    ChooserPath,
    ChooserState,
    Column,
    Level,
)
from paperroute_transit.core.chooser_lists import (
    previews_failed,
    with_previews,
)
from paperroute_transit.core.messages import (
    ColumnFailed,
    ColumnLoaded,
    LoadColumn,
    LoadPreviews,
    PreviewsFailed,
    PreviewsLoaded,
)

MAX_COLLAPSED: Final[int] = 3  # single-row columns a preview passes


class ColumnReader(Protocol):
    """Something that reads the chooser column after a path."""

    def choices(self, *, path: ChooserPath) -> Column | None:
        """
        Read the column after a path.

        Parameters
        ----------
        path : ChooserPath
            Steps chosen so far.

        Returns
        -------
        Column | None
            The column, or None when the timetable has none.
        """


def read_chain(
    *, network: ColumnReader, path: ChooserPath,
) -> tuple[Column, ...]:
    """
    Read the column after a path and the single-row columns it passes.

    Parameters
    ----------
    network : ColumnReader
        Chooser catalogue.
    path : ChooserPath
        Steps chosen so far.

    Returns
    -------
    tuple[Column, ...]
        Each single-row GROUP, ROUTE or LINE column stored collapsed,
        up to ``MAX_COLLAPSED``, then the column drawn. Stops at a
        missing column, returning what it has.
    """
    chain: list[Column] = []
    while (column := network.choices(path=path)) is not None:
        single = column.level in COLLAPSIBLE and len(column.items) == 1
        if not single or len(chain) >= MAX_COLLAPSED:
            chain.append(column)
            break
        chain.append(dataclasses.replace(column, collapsed=True))
        row = column.items[0]
        path = (*path, (row.level or column.level, row.key))
    return tuple(chain)


def read_previews(
    *,
    network: ColumnReader,
    path: ChooserPath,
    steps: tuple[tuple[Level, str], ...],
) -> tuple[tuple[Column, ...], ...]:
    """
    Read what each row of a column opens.

    Parameters
    ----------
    network : ColumnReader
        Chooser catalogue.
    path : ChooserPath
        Steps leading to the column.
    steps : tuple[tuple[Level, str], ...]
        Each row's own step, in row order.

    Returns
    -------
    tuple[tuple[Column, ...], ...]
        One chain per row, in row order.
    """
    return tuple(
        read_chain(network=network, path=(*path, step)) for step in steps
    )


def previews_read(
    *, network: ColumnReader, effect: LoadPreviews,
) -> PreviewsLoaded:
    """
    Read every row's preview for a request.

    Parameters
    ----------
    network : ColumnReader
        Chooser catalogue.
    effect : LoadPreviews
        Request.

    Returns
    -------
    PreviewsLoaded
        The chains, with the request's timetable echoed.
    """
    return PreviewsLoaded(
        path=effect.path,
        previews=read_previews(
            network=network, path=effect.path, steps=effect.steps,
        ),
        timetable=effect.timetable,
    )


def chooser_read(
    *, network: ColumnReader, effect: LoadColumn | LoadPreviews,
) -> ColumnLoaded | PreviewsLoaded:
    """
    Read a chooser column, or every row's preview.

    Parameters
    ----------
    network : ColumnReader
        Chooser catalogue.
    effect : LoadColumn | LoadPreviews
        Request.

    Returns
    -------
    ColumnLoaded | PreviewsLoaded
        The column, or the previews.
    """
    if isinstance(effect, LoadPreviews):
        return previews_read(network=network, effect=effect)
    return ColumnLoaded(
        path=effect.path,
        column=network.choices(path=effect.path),
        timetable=effect.timetable,
    )


def chooser_failure(
    *, effect: LoadColumn | LoadPreviews,
) -> ColumnFailed | PreviewsFailed:
    """
    Give the event posted when a chooser read fails.

    Parameters
    ----------
    effect : LoadColumn | LoadPreviews
        Request.

    Returns
    -------
    ColumnFailed | PreviewsFailed
        Its failure event, tagged with the request's timetable version.
    """
    if isinstance(effect, LoadPreviews):
        return PreviewsFailed(path=effect.path, timetable=effect.timetable)
    return ColumnFailed(path=effect.path, timetable=effect.timetable)


def previews_replied(
    *, chooser: ChooserState, event: PreviewsLoaded | PreviewsFailed,
) -> ChooserState:
    """
    Take a previews reply into the chooser.

    Parameters
    ----------
    chooser : ChooserState
        Chooser.
    event : PreviewsLoaded | PreviewsFailed
        The reply.

    Returns
    -------
    ChooserState
        The chooser with the previews kept, or the failure shown.
    """
    if isinstance(event, PreviewsFailed):
        return previews_failed(chooser=chooser, path=event.path)
    return with_previews(
        chooser=chooser, path=event.path, previews=event.previews,
    )
