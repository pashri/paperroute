"""Where trips through a strip's From leave its line, and how it reads."""

from collections import Counter
from collections.abc import Collection, Sequence

from paperroute.core.model import StopPlace
from paperroute.core.reach import RunTail
from paperroute.core.stop_view import DOT


def first_off(*, tail: RunTail, line: Collection[str], origin: str
              ) -> tuple[str, str] | None:
    """
    Find where a tail leaves a line.

    Parameters
    ----------
    tail : RunTail
        A tail through the From.
    line : Collection[str]
        Station IDs on the line, with the stations merged into them.
    origin : str
        The From's station ID.

    Returns
    -------
    tuple[str, str] | None
        The last station on the line and the first one after the From
        off it; None when the tail stays on the line.
    """
    return next(
        (
            (back, here)
            for back, here in zip((origin, *tail.after), tail.after)
            if here not in line
        ),
        None,
    )


def found_exits(
    *, tails: Sequence[RunTail], line: Collection[str], origin: str,
) -> dict[tuple[str, str], int]:
    """
    Find the ways trips through the From leave its line.

    Parameters
    ----------
    tails : Sequence[RunTail]
        Tails through the From, not cut to the line.
    line : Collection[str]
        Station IDs on the line, with the stations merged into them.
    origin : str
        The From's station ID.

    Returns
    -------
    dict[tuple[str, str], int]
        Trips by (junction, first station off the line). An off station
        reached from two junctions keeps only the one with more trips;
        on a tie, the lower junction ID.
    """
    pairs: Counter[tuple[str, str]] = Counter()
    for tail in tails:
        pair = first_off(tail=tail, line=line, origin=origin)
        if pair is not None:
            pairs[pair] += tail.trips
    best: dict[str, tuple[str, str]] = {}
    for pair in sorted(pairs, key=lambda each: (-pairs[each], each)):
        best.setdefault(pair[1], pair)
    return {pair: pairs[pair] for pair in best.values()}


def exit_name(*, place: StopPlace, lines: Sequence[str]) -> str:
    """
    Name an exit row.

    Parameters
    ----------
    place : StopPlace
        First station off the line.
    lines : Sequence[str]
        Route numbers making the hop.

    Returns
    -------
    str
        '<name> · <lines>', the name without ' Station' and the lines
        space-separated; just the name with no lines.
    """
    name = place.name.removesuffix(' Station')
    return f'{name}{DOT}{" ".join(lines)}' if lines else name
