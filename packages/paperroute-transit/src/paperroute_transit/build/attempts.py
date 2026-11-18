"""Choose the builds a refresh tries in turn, from its download outcome."""

from dataclasses import replace
from datetime import date

from paperroute_transit.build.gtfs_files import Source
from paperroute_transit.build.handoff import Attempt, Fetched
from paperroute_transit.build.layout import Layout
from paperroute_transit.build.source_state import SourceState, load_states
from paperroute_transit.build.sources import BuildParts, file_sha256


def intact_sha(*, layout: Layout, name: str,
               stored: dict[str, SourceState]) -> str | None:
    """
    The stored SHA-256 of a source, if its zip on disk still matches it.

    Parameters
    ----------
    layout : Layout
        Data paths.
    name : str
        Source name.
    stored : dict[str, SourceState]
        Stored states.

    Returns
    -------
    str | None
        The digest, or None when there's no state, no zip, or the zip's
        bytes no longer hash to the stored digest.
    """
    path = layout.sources / f'{name}.zip'
    expected = stored[name].sha256 if name in stored else None
    if expected is None or not path.exists():
        return None
    return expected if file_sha256(path=path) == expected else None


def present_parts(*, parts: BuildParts, fetched: Fetched) -> BuildParts:
    """
    Keep the sources there is a copy of to build from.

    Parameters
    ----------
    parts : BuildParts
        The city's reference and its sources.
    fetched : Fetched
        Outcome of download_changed.

    Returns
    -------
    BuildParts
        The parts without any optional source that has no copy.
    """
    return replace(
        parts,
        sources=tuple(
            spec for spec in parts.sources if spec.name in fetched.states
        ),
    )


def without_new_copies(*, layout: Layout, fetched: Fetched) -> Fetched | None:
    """
    Drop the new copy of every optional source that was just downloaded.

    A source with an intact stored zip goes back to its stored state; one
    without is left out. Each is marked failed.

    Parameters
    ----------
    layout : Layout
        Data paths.
    fetched : Fetched
        Outcome of download_changed.

    Returns
    -------
    Fetched | None
        The outcome without the new copies, or None when no optional
        source has one.
    """
    dropped = tuple(
        name for name in fetched.changed if name in fetched.optional
    )
    if not dropped:
        return None
    stored = load_states(path=layout.states)
    states = {
        name: state
        for name, state in fetched.states.items()
        if name not in dropped
    }
    for name in dropped:
        if intact_sha(layout=layout, name=name, stored=stored) is not None:
            states[name] = stored[name]
    return replace(
        fetched,
        states=states,
        downloaded=tuple(n for n in fetched.downloaded if n not in dropped),
        changed=tuple(n for n in fetched.changed if n not in dropped),
        failed=(*fetched.failed, *dropped),
    )


def without_optional(*, fetched: Fetched) -> Fetched | None:
    """
    Leave out every optional source that has a state.

    Each is marked failed.

    Parameters
    ----------
    fetched : Fetched
        Outcome of download_changed, or of an earlier rung.

    Returns
    -------
    Fetched | None
        The outcome without the optional sources, or None when none has a
        state.
    """
    left = tuple(name for name in fetched.optional if name in fetched.states)
    if not left:
        return None
    return replace(
        fetched,
        states={
            name: state
            for name, state in fetched.states.items()
            if name not in left
        },
        downloaded=tuple(n for n in fetched.downloaded if n not in left),
        changed=tuple(n for n in fetched.changed if n not in left),
        failed=(
            *fetched.failed,
            *(name for name in left if name not in fetched.failed),
        ),
    )


def ladder(*, layout: Layout, fetched: Fetched) -> tuple[Fetched, ...]:
    """
    List the outcomes to build from, most complete first.

    The outcome as downloaded comes first, then without the new copy of
    each optional source, then without any optional source.

    Parameters
    ----------
    layout : Layout
        Data paths.
    fetched : Fetched
        Outcome of download_changed.

    Returns
    -------
    tuple[Fetched, ...]
        One to three outcomes.
    """
    rungs = [fetched]
    kept = without_new_copies(layout=layout, fetched=fetched)
    if kept is not None:
        rungs.append(kept)
    bare = without_optional(fetched=rungs[-1])
    if bare is not None:
        rungs.append(bare)
    return tuple(rungs)


def sources_of(
    *, layout: Layout, parts: BuildParts, fetched: Fetched,
) -> tuple[Source, ...]:
    """
    Name the archives a build loads, in order.

    Parameters
    ----------
    layout : Layout
        Data paths.
    parts : BuildParts
        The city's reference and its sources.
    fetched : Fetched
        Outcome the build is from; a changed source loads from work/ and
        the rest from sources/.

    Returns
    -------
    tuple[Source, ...]
        The sources that have a copy.
    """
    return tuple(
        Source(
            name=spec.name,
            archive=(
                layout.work if spec.name in fetched.changed else layout.sources
            ) / f'{spec.name}.zip',
            optional=spec.optional,
        )
        for spec in present_parts(parts=parts, fetched=fetched).sources
    )


def attempts_of(
    *,
    layout: Layout,
    parts: BuildParts,
    fetched: Fetched,
    start: date,
    days: int,
) -> tuple[Attempt, ...]:
    """
    List the builds to try in turn.

    Parameters
    ----------
    layout : Layout
        Data paths.
    parts : BuildParts
        The city's reference and its sources.
    fetched : Fetched
        Outcome of download_changed.
    start : date
        First date of the timetable.
    days : int
        Days the timetable covers.

    Returns
    -------
    tuple[Attempt, ...]
        One attempt per rung of the ladder.
    """
    return tuple(
        Attempt(
            fetched=rung,
            sources=sources_of(layout=layout, parts=parts, fetched=rung),
            start=start,
            days=days,
        )
        for rung in ladder(layout=layout, fetched=fetched)
    )
