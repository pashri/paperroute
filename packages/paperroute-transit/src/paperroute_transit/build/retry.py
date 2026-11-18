"""Build a handed-over refresh again from its next attempt."""

import logging
import os
import sys
from collections.abc import Sequence
from dataclasses import replace
from functools import partial
from pathlib import Path

from paperroute_device.logs import configure_logging
from paperroute_transit.build.handoff import (
    Handoff,
    HandoffRecord,
    RefreshTail,
    attempt_text,
)
from paperroute_transit.build.layout import Layout
from paperroute_transit.build.swap import (
    clear_work,
    guarded,
    holds_lock,
    printed,
    readable,
)
from paperroute_transit.build.timetable import Window, build_timetable

logger = logging.getLogger(__name__)


def build_attempt(
    *, record: HandoffRecord, tail: RefreshTail, index: int, path: Path,
    city_ref: str,
) -> None:
    """
    Load one fallback and exec into the extras program with the rest.

    Parameters
    ----------
    record : HandoffRecord
        The handoff.
    tail : RefreshTail
        Its refresh tail.
    index : int
        Which of the tail's fallbacks to build.
    path : Path
        Handoff file, written again for the extras program.
    city_ref : str
        The city's reference.
    """
    attempt = tail.fallbacks[index]
    logger.warning(
        'building again from %s',
        attempt_text(attempt=attempt, data_dir=tail.data_dir),
    )
    build_timetable(
        sources=list(attempt.sources),
        window=Window(start=attempt.start, days=attempt.days),
        out=record.out,
        city_ref=city_ref,
        handoff=Handoff(path=path, tail=replace(
            tail,
            fetched=attempt.fetched,
            fallbacks=tail.fallbacks[index + 1:],
        )),
    )


def fall_back(
    *, record: HandoffRecord, tail: RefreshTail, path: Path, city_ref: str,
) -> int:
    """
    Build each attempt left in turn, then give up if none gets through.

    Parameters
    ----------
    record : HandoffRecord
        The handoff.
    tail : RefreshTail
        Its refresh tail, with at least one fallback.
    path : Path
        Handoff file.
    city_ref : str
        The city's reference.

    Returns
    -------
    int
        Exit status: 1, as a successful build execs and never returns.
    """
    layout = Layout(data_dir=tail.data_dir)
    held = holds_lock(fd=tail.lock_fd, path=layout.lock)
    configure_logging(error_file=tail.error_log)
    record.out.unlink(missing_ok=True)
    if held:
        for index in range(len(tail.fallbacks)):
            guarded(step=partial(
                build_attempt, record=record, tail=tail, index=index,
                path=path, city_ref=city_ref,
            ))
    else:
        logger.error('refresh lock not held on fd %d', tail.lock_fd)
    clear_work(work=layout.work)
    if held:
        os.close(tail.lock_fd)
    return printed(report=None)


def main(*, argv: Sequence[str]) -> int:
    """
    Build a handed-over refresh again from its next attempt.

    Parameters
    ----------
    argv : Sequence[str]
        The city's reference, then the handoff file.

    Returns
    -------
    int
        Exit status.
    """
    city_ref, path = argv[0], Path(argv[1])
    record = readable(path=path)
    if record is None:
        return 1
    if record.tail is None or not record.tail.fallbacks:
        configure_logging()
        logger.error('handoff has no attempt left')
        return 1
    return fall_back(
        record=record, tail=record.tail, path=path, city_ref=city_ref,
    )


if __name__ == '__main__':
    sys.exit(main(argv=sys.argv[1:]))
