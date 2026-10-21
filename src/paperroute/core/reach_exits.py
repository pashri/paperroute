"""Where trips through a strip's From leave its line, and how it reads."""

from collections import Counter
from collections.abc import Collection, Mapping, Sequence
from itertools import chain, takewhile

from paperroute.core.model import StopPlace
from paperroute.core.reach import RunTail
from paperroute.core.reach_layout import LineExit
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


def off_runs(*, tails: Sequence[RunTail], line: Collection[str],
             origin: str) -> list[tuple[str, str, tuple[str, ...], int]]:
    """
    List where each tail leaves a line and where it goes next.

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
    list[tuple[str, str, tuple[str, ...], int]]
        Junction, first station off the line, the stations after it
        until the line again, and trips, for each tail that leaves.
    """
    found = []
    for tail in tails:
        pair = first_off(tail=tail, line=line, origin=origin)
        if pair is not None:
            rest = tail.after[tail.after.index(pair[1]) + 1:]
            found.append((*pair, tuple(
                takewhile(lambda station: station not in line, rest),
            ), tail.trips))
    return found


def exit_leads(
    *, tails: Sequence[RunTail], line: Collection[str], origin: str,
) -> dict[tuple[str, str], tuple[str, str]]:
    """
    Find the turnoff each way off a line is part of.

    A train that runs through a station without stopping leaves the line
    at the same turnoff as one that stops there, but its first stop off
    the line is further on. Such a way off is led by the busiest other
    way off at its junction whose trains later call at its first stop.

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
    dict[tuple[str, str], tuple[str, str]]
        The leading (junction, first station off) by each led one; a way
        off that leads, or stands alone, is absent.
    """
    runs = off_runs(tails=tails, line=line, origin=origin)
    leads: dict[tuple[str, str], tuple[str, str]] = {}
    for junction, off, _, _ in runs:
        votes: Counter[str] = Counter()
        for at, first, rest, trips in runs:
            if at == junction and first != off and off in rest:
                votes[first] += trips
        if votes:
            best = sorted(votes.items(), key=lambda vote: (-vote[1], vote[0]))
            leads[junction, off] = (junction, best[0][0])
    for pair, lead in list(leads.items()):
        for _ in range(len(leads)):
            lead = leads.get(lead, lead)
        leads[pair] = lead
    return leads


def grouped_exits(
    *,
    exits: Sequence[LineExit],
    leads: Mapping[tuple[str, str], tuple[str, str]],
) -> tuple[LineExit, ...]:
    """
    Merge the ways off a line that make one turnoff.

    Parameters
    ----------
    exits : Sequence[LineExit]
        Ways off, by junction and first station off.
    leads : Mapping[tuple[str, str], tuple[str, str]]
        The leading way off of each led one, from `exit_leads`.

    Returns
    -------
    tuple[LineExit, ...]
        One exit per turnoff, at its leading first station, with the
        trips summed and the lines of the leading way first, then those
        of the others, the busiest first, each line once.
    """
    groups: dict[tuple[str, str], list[LineExit]] = {}
    for each in sorted(exits, key=lambda each: -each.trips):
        pair = (each.junction, each.off)
        groups.setdefault(leads.get(pair, pair), []).append(each)
    return tuple(
        LineExit(
            junction=junction,
            off=off,
            lines=tuple(dict.fromkeys(chain.from_iterable(
                each.lines
                for each in (
                    *(each for each in members if each.off == off),
                    *(each for each in members if each.off != off),
                )
            ))),
            trips=sum(each.trips for each in members),
        )
        for (junction, off), members in sorted(groups.items())
    )


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
