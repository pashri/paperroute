"""Tests for building a handed-over refresh again."""

import json
import os
from dataclasses import replace
from pathlib import Path

import pytest

from paperroute_sydney.city import SYDNEY_REF
from paperroute_transit.build import retry
from paperroute_transit.build.handoff import read_handoff, write_handoff
from tests.build.network_feed import network_feed
from tests.build.test_swap import attempt_in, refresh_record


class Execed(BaseException):
    """Raised by the stand-in for os.execv; guarded must not catch it."""


def logged(*, data_dir: Path) -> list[str]:
    """
    Read the messages in the error log.

    Parameters
    ----------
    data_dir : Path
        Data dir.

    Returns
    -------
    list[str]
        The messages, oldest first.
    """
    return [
        json.loads(line)['msg']
        for line in (data_dir / 'log' / 'errors.jsonl').read_text(
            encoding='utf-8',
        ).splitlines()
    ]


@pytest.mark.usefixtures('restored_logging')
def test_retry_hands_the_next_attempt_over(
    data_dir: Path, lock_fd: int, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The next attempt is loaded and handed to extras with the rest."""
    network_feed(path=data_dir / 'sources' / 'metro.zip')
    first = attempt_in(data_dir=data_dir)
    second = replace(first, sources=())
    record = refresh_record(data_dir=data_dir, lock_fd=lock_fd)
    assert record.tail is not None
    tail = replace(record.tail, fallbacks=(first, second))
    path = data_dir / 'work' / 'handoff.json'
    write_handoff(path=path, record=replace(record, tail=tail))
    seen: dict[str, object] = {}

    def execv(exe: str, argv: list[str]) -> None:
        """
        Read the new handoff instead of replacing the process.

        Parameters
        ----------
        exe : str
            Interpreter.
        argv : list[str]
            Its arguments.

        Raises
        ------
        Execed
            Always.
        """
        del exe
        seen['argv'] = argv[1:4]
        seen['tail'] = read_handoff(path=Path(argv[-1])).tail
        raise Execed

    monkeypatch.setattr(os, 'execv', execv)
    with pytest.raises(Execed):
        retry.main(argv=[SYDNEY_REF, str(path)])
    assert seen['argv'] == [
        '-m', 'paperroute_transit.build.extras', SYDNEY_REF,
    ]
    assert seen['tail'] == replace(
        tail, fetched=first.fetched, fallbacks=(second,),
    )


@pytest.mark.usefixtures('restored_logging')
def test_retry_with_every_attempt_failing_exits_1(
    data_dir: Path, lock_fd: int,
) -> None:
    """Attempts that cannot load are each logged; work and lock go."""
    gone = attempt_in(data_dir=data_dir)
    record = refresh_record(data_dir=data_dir, lock_fd=lock_fd)
    assert record.tail is not None
    record = replace(
        record, tail=replace(record.tail, fallbacks=(gone, gone)),
    )
    path = data_dir / 'work' / 'handoff.json'
    write_handoff(path=path, record=record)
    assert retry.main(argv=[SYDNEY_REF, str(path)]) == 1
    assert not list((data_dir / 'work').iterdir())
    with pytest.raises(OSError):
        os.fstat(lock_fd)
    assert logged(data_dir=data_dir).count('refresh failed') == 2


@pytest.mark.usefixtures('restored_logging')
def test_retry_refuses_an_unreadable_handoff(tmp_path: Path) -> None:
    """A missing handoff file is an exit of 1."""
    assert retry.main(argv=[SYDNEY_REF, str(tmp_path / 'none.json')]) == 1


@pytest.mark.usefixtures('restored_logging')
def test_retry_without_the_lock_builds_nothing(data_dir: Path) -> None:
    """Without the lock, no attempt is built; work is cleared."""
    network_feed(path=data_dir / 'sources' / 'metro.zip')
    record = refresh_record(data_dir=data_dir, lock_fd=999)
    assert record.tail is not None
    record = replace(record, tail=replace(
        record.tail, fallbacks=(attempt_in(data_dir=data_dir),),
    ))
    path = data_dir / 'work' / 'handoff.json'
    write_handoff(path=path, record=record)
    assert retry.main(argv=[SYDNEY_REF, str(path)]) == 1
    assert not list((data_dir / 'work').iterdir())
    assert logged(data_dir=data_dir) == ['refresh lock not held on fd 999']


@pytest.mark.usefixtures('restored_logging')
def test_retry_with_no_attempt_left_exits_1(data_dir: Path) -> None:
    """A handoff with no fallback is refused."""
    path = data_dir / 'work' / 'handoff.json'
    write_handoff(
        path=path, record=refresh_record(data_dir=data_dir, lock_fd=7),
    )
    assert retry.main(argv=[SYDNEY_REF, str(path)]) == 1
