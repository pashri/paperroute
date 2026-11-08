"""Tests for the handoff from the timetable load to the extras program."""

import fcntl
import json
import logging
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from collections.abc import Iterator
from contextlib import closing
from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import Final

import pytest

from paperroute.build import extras
from paperroute.build import handoff as handoff_module
from paperroute.build import swap, timetable
from paperroute.build.handoff import (
    Fetched,
    HandoffError,
    HandoffRecord,
    RefreshTail,
    read_handoff,
    write_handoff,
)
from paperroute.build.source_state import SourceState
from paperroute.build.timetable import Window, build_timetable
from paperroute.city import SYDNEY_REF
from paperroute_transit.build.gtfs_files import Source
from tests.build.network_feed import network_feed

LOCK_PROBE: Final[str] = (
    'import sys\n'
    'from pathlib import Path\n'
    'from paperroute.build.swap import holds_lock\n'
    'sys.exit(0 if holds_lock(fd=int(sys.argv[2]), path=Path(sys.argv[1]))'
    ' else 3)\n'
)
LOCK_AND_EXEC: Final[str] = (
    'import sys\n'
    'from pathlib import Path\n'
    'from paperroute.build.refresh import refresh_lock\n'
    'from paperroute.build.handoff import exec_python\n'
    'lock = Path(sys.argv[1])\n'
    'with refresh_lock(path=lock) as fd:\n'
    '    exec_python(\n'
    "        args=['-c', sys.argv[2], str(lock), str(fd)],\n"
    "        keep=[fd] if sys.argv[3] == 'keep' else [],\n"
    '    )\n'
)


@pytest.fixture(name='loaded', scope='module')
def fixture_loaded(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """
    Load the network feed without its run tables.

    Parameters
    ----------
    tmp_path_factory : pytest.TempPathFactory
        Temporary directories.

    Returns
    -------
    Path
        Timetable file with no run tables.
    """
    folder = tmp_path_factory.mktemp('loaded')
    source = Source(name='sydneytrains',
                    archive=network_feed(path=folder / 'network.zip'))
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(
            timetable, 'build_extras', lambda *, path, city_ref: None,
        )
        build_timetable(city_ref=SYDNEY_REF, sources=[source],
                        window=Window(start=date(2026, 10, 5), days=3),
                        out=folder / 'timetable.db')
    return folder / 'timetable.db'


@pytest.fixture(name='restored_logging')
def _restored_logging() -> Iterator[None]:
    """Put the root logger back after a test that configures it."""
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    yield
    for handler in list(root.handlers):
        if handler not in handlers:
            handler.close()
    root.handlers[:] = handlers
    root.setLevel(level)


@pytest.fixture(name='data_dir')
def fixture_data_dir(tmp_path: Path) -> Path:
    """
    A data dir holding an old timetable and an empty work dir.

    Parameters
    ----------
    tmp_path : Path
        Temporary directory.

    Returns
    -------
    Path
        The data dir.
    """
    data_dir = tmp_path / 'data'
    (data_dir / 'work').mkdir(parents=True)
    (data_dir / 'sources').mkdir()
    (data_dir / 'timetable.db').write_bytes(b'old timetable')
    return data_dir


@pytest.fixture(name='lock_fd')
def fixture_lock_fd(data_dir: Path) -> Iterator[int]:
    """
    Hold the data dir's refresh lock on a raw descriptor.

    Parameters
    ----------
    data_dir : Path
        Data dir.

    Yields
    ------
    int
        The descriptor; the tail closes it.
    """
    fd = os.open(data_dir / 'refresh.lock', os.O_WRONLY | os.O_CREAT, 0o644)
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    yield fd
    try:
        os.close(fd)
    except OSError:
        pass


def refresh_record(*, data_dir: Path, lock_fd: int) -> HandoffRecord:
    """
    Describe a refresh handed over in a data dir.

    Parameters
    ----------
    data_dir : Path
        Data dir.
    lock_fd : int
        Descriptor holding the lock.

    Returns
    -------
    HandoffRecord
        Record with a refresh tail.
    """
    work = data_dir / 'work'
    return HandoffRecord(
        scratch=work / 'timetable.new', out=work / 'timetable.db',
        dropped=2, partial=1, overnight=3, peak_mb=99.5,
        tail=RefreshTail(
            data_dir=data_dir,
            fetched=Fetched(
                states={'metro': SourceState(
                    etag='"2"', last_modified=None, sha256='ab' * 32,
                )},
                downloaded=('metro',), changed=(),
            ),
            started=time.monotonic(),
            error_log=data_dir / 'log' / 'errors.jsonl',
            lock_fd=lock_fd,
        ),
    )


def test_handoff_round_trip(data_dir: Path) -> None:
    """A record reads back equal and holds only paths, states and counts."""
    record = refresh_record(data_dir=data_dir, lock_fd=7)
    path = data_dir / 'work' / 'handoff.json'
    write_handoff(path=path, record=record)
    assert read_handoff(path=path) == record
    raw = json.loads(path.read_text(encoding='utf-8'))
    assert set(raw) == {
        'scratch', 'out', 'dropped', 'partial', 'overnight', 'peak_mb',
        'tail',
    }
    assert set(raw['tail']) == {
        'data_dir', 'states', 'downloaded', 'changed', 'started',
        'error_log', 'lock_fd',
    }
    plain = replace(record, tail=None)
    write_handoff(path=path, record=plain)
    assert read_handoff(path=path) == plain


def test_handoff_refuses_paths_outside_work(
    data_dir: Path, tmp_path: Path,
) -> None:
    """Every path must lie in the handoff's work dir or data dir."""
    record = refresh_record(data_dir=data_dir, lock_fd=7)
    path = data_dir / 'work' / 'handoff.json'
    assert record.tail is not None
    bad = [
        replace(record, scratch=tmp_path / 'elsewhere.new'),
        replace(record, out=data_dir / 'timetable.db'),
        replace(record, tail=replace(record.tail, data_dir=tmp_path)),
        replace(record, tail=replace(
            record.tail, error_log=tmp_path / 'errors.jsonl',
        )),
    ]
    for each in bad:
        with pytest.raises(HandoffError):
            write_handoff(path=path, record=each)
    raw = handoff_module.record_json(record=bad[0])
    path.write_text(json.dumps(raw), encoding='utf-8')
    with pytest.raises(HandoffError):
        read_handoff(path=path)


def test_handoff_refuses_malformed_files(data_dir: Path) -> None:
    """A file that is not a well-typed record is refused."""
    path = data_dir / 'work' / 'handoff.json'
    raw = handoff_module.record_json(
        record=refresh_record(data_dir=data_dir, lock_fd=7),
    )
    tail = raw['tail']
    assert isinstance(tail, dict)
    malformed: list[object] = [
        [],
        {**raw, 'dropped': '2'},
        {**raw, 'tail': {**tail, 'changed': [3]}},
        {**raw, 'tail': {**tail, 'states': {'metro': {'etag': 'x'}}}},
    ]
    for broken in malformed:
        path.write_text(json.dumps(broken), encoding='utf-8')
        with pytest.raises(HandoffError):
            read_handoff(path=path)


@pytest.mark.parametrize(('keep', 'code'), [('keep', 0), ('drop', 3)])
def test_lock_fd_survives_exec(tmp_path: Path, keep: str, code: int) -> None:
    """A kept lock descriptor is still open and locked after the exec."""
    result = subprocess.run(
        [
            sys.executable, '-c', LOCK_AND_EXEC,
            str(tmp_path / 'refresh.lock'), LOCK_PROBE, keep,
        ],
        check=False, capture_output=True, text=True,
    )
    assert result.returncode == code, result.stderr


@pytest.mark.usefixtures('restored_logging')
def test_extras_program_runs_the_tail_and_swaps(
    data_dir: Path, loaded: Path, lock_fd: int,
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    """After a hand over, extras builds, checks, swaps and reports."""
    monkeypatch.setattr(swap, 'MIN_TRIPS', 1)
    record = refresh_record(data_dir=data_dir, lock_fd=lock_fd)
    shutil.copy(loaded, record.scratch)
    handoff = data_dir / 'work' / 'handoff.json'
    write_handoff(path=handoff, record=record)
    assert extras.main(argv=[SYDNEY_REF, str(record.scratch), str(handoff)]) == 0
    with closing(sqlite3.connect(data_dir / 'timetable.db')) as conn:
        assert conn.execute('SELECT count(*) FROM runs').fetchone()[0] > 0
    assert not list((data_dir / 'work').iterdir())
    with pytest.raises(OSError):
        os.fstat(lock_fd)
    states = json.loads(
        (data_dir / 'sources' / 'sources.json').read_text(encoding='utf-8'),
    )
    assert states['metro']['etag'] == '"2"'
    report = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert report['rebuilt'] and report['downloaded'] == ['metro']
    assert report['max_rss_mb'] >= 99.5


@pytest.mark.usefixtures('restored_logging')
def test_failed_extras_keep_the_old_timetable(
    data_dir: Path, lock_fd: int,
) -> None:
    """A failing extras step logs to errors.jsonl and swaps nothing in."""
    record = refresh_record(data_dir=data_dir, lock_fd=lock_fd)
    record.scratch.write_bytes(b'not a timetable')
    handoff = data_dir / 'work' / 'handoff.json'
    write_handoff(path=handoff, record=record)
    assert extras.main(argv=[SYDNEY_REF, str(record.scratch), str(handoff)]) == 1
    assert (data_dir / 'timetable.db').read_bytes() == b'old timetable'
    assert not (data_dir / 'sources' / 'sources.json').exists()
    assert not list((data_dir / 'work').iterdir())
    lines = (data_dir / 'log' / 'errors.jsonl').read_text(
        encoding='utf-8',
    ).splitlines()
    assert json.loads(lines[0])['msg'] == 'refresh failed'


@pytest.mark.usefixtures('restored_logging')
def test_lost_lock_stops_the_swap(data_dir: Path, loaded: Path) -> None:
    """Without the lock after the exec, nothing is swapped in."""
    record = refresh_record(data_dir=data_dir, lock_fd=999)
    shutil.copy(loaded, record.scratch)
    handoff = data_dir / 'work' / 'handoff.json'
    write_handoff(path=handoff, record=record)
    assert extras.main(argv=[SYDNEY_REF, str(record.scratch), str(handoff)]) == 1
    assert (data_dir / 'timetable.db').read_bytes() == b'old timetable'
    assert 'LockLostError' in (data_dir / 'log' / 'errors.jsonl').read_text(
        encoding='utf-8',
    )


def test_plain_build_hand_over_replaces_out(
    tmp_path: Path, loaded: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    """Without a refresh tail, extras finishes the file and the merge line."""
    out = tmp_path / 'timetable.db'
    record = HandoffRecord(
        scratch=tmp_path / 'timetable.new', out=out, dropped=4, partial=0,
        overnight=0, peak_mb=1.0, tail=None,
    )
    shutil.copy(loaded, record.scratch)
    handoff = tmp_path / 'timetable.handoff.json'
    write_handoff(path=handoff, record=record)
    with pytest.raises(HandoffError):
        extras.main(argv=[SYDNEY_REF, str(tmp_path / 'other.new'), str(handoff)])
    assert not handoff.exists()
    write_handoff(path=handoff, record=record)
    assert extras.main(argv=[SYDNEY_REF, str(record.scratch), str(handoff)]) == 0
    assert not handoff.exists() and not record.scratch.exists()
    with closing(sqlite3.connect(out)) as conn:
        assert conn.execute('SELECT count(*) FROM runs').fetchone()[0] > 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[-1] == 'progress done dropped=4 partial=0 overnight=0'


def test_holds_lock_needs_the_lock_file_itself(
    data_dir: Path, lock_fd: int,
) -> None:
    """A descriptor on another file does not count, even while locked."""
    assert swap.holds_lock(fd=lock_fd, path=data_dir / 'refresh.lock')
    with (data_dir / 'other').open('w') as other:
        assert not swap.holds_lock(
            fd=other.fileno(), path=data_dir / 'refresh.lock',
        )


def test_holds_lock_is_false_when_nothing_is_locked(data_dir: Path) -> None:
    """An open but unlocked descriptor does not hold the lock."""
    with (data_dir / 'refresh.lock').open('a') as unlocked:
        assert not swap.holds_lock(
            fd=unlocked.fileno(), path=data_dir / 'refresh.lock',
        )


def test_guarded_logs_unexpected_errors_and_passes_some_through(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Any failure is logged and gives None, except a passthrough type."""
    def broken() -> int:
        raise ZeroDivisionError

    with caplog.at_level(logging.ERROR):
        assert swap.guarded(step=broken) is None
    assert caplog.records[-1].message == 'refresh failed unexpectedly'
    with pytest.raises(ZeroDivisionError):
        swap.guarded(step=broken, passthrough=(ZeroDivisionError,))


@pytest.mark.usefixtures('restored_logging')
def test_unreadable_handoff_is_logged(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    """A missing or malformed handoff exits 1 with a logged error."""
    handoff = tmp_path / 'handoff.json'
    handoff.write_text('[]', encoding='utf-8')
    for path in (tmp_path / 'missing.json', handoff):
        assert extras.main(argv=[SYDNEY_REF, str(tmp_path / 'x.new'), str(path)]) == 1
    assert capsys.readouterr().err.count('handoff unreadable') == 2


@pytest.mark.usefixtures('restored_logging')
def test_wrong_scratch_is_logged_and_work_cleared(
    data_dir: Path, lock_fd: int,
) -> None:
    """A refresh handoff for another scratch file fails like any step."""
    record = refresh_record(data_dir=data_dir, lock_fd=lock_fd)
    record.scratch.write_bytes(b'scratch')
    handoff = data_dir / 'work' / 'handoff.json'
    write_handoff(path=handoff, record=record)
    other = str(data_dir / 'work' / 'other.new')
    assert extras.main(argv=[SYDNEY_REF, other, str(handoff)]) == 1
    assert not list((data_dir / 'work').iterdir())
    assert (data_dir / 'timetable.db').read_bytes() == b'old timetable'
    assert 'HandoffError' in (data_dir / 'log' / 'errors.jsonl').read_text(
        encoding='utf-8',
    )


@pytest.mark.usefixtures('restored_logging')
def test_unchecked_lock_fd_is_left_open(
    data_dir: Path, loaded: Path,
) -> None:
    """A lock fd that turns out to be another file is never closed."""
    with (data_dir / 'unrelated').open('w') as unrelated:
        record = refresh_record(data_dir=data_dir, lock_fd=unrelated.fileno())
        shutil.copy(loaded, record.scratch)
        handoff = data_dir / 'work' / 'handoff.json'
        write_handoff(path=handoff, record=record)
        assert extras.main(argv=[SYDNEY_REF, str(record.scratch), str(handoff)]) == 1
        os.fstat(unrelated.fileno())


def test_handoff_refuses_bools_and_bad_state_values(data_dir: Path) -> None:
    """A bool is not a count, and state values are strings or null."""
    path = data_dir / 'work' / 'handoff.json'
    raw = handoff_module.record_json(
        record=refresh_record(data_dir=data_dir, lock_fd=7),
    )
    tail = raw['tail']
    assert isinstance(tail, dict)
    state = {'etag': 1, 'last_modified': None, 'sha256': None}
    for broken in (
        {**raw, 'dropped': True},
        {**raw, 'tail': {**tail, 'states': {'metro': state}}},
        {**raw, 'tail': {**tail, 'states': {'metro': {**state, 'x': 1}}}},
    ):
        path.write_text(json.dumps(broken), encoding='utf-8')
        with pytest.raises(HandoffError):
            read_handoff(path=path)
