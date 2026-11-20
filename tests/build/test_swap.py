"""Tests for the handoff from the timetable load to the extras program."""

import json
import logging
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from contextlib import closing
from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import Final

import pytest

from paperroute_sydney.city import SYDNEY_REF
from paperroute_transit.build import extras
from paperroute_transit.build import handoff as handoff_module
from paperroute_transit.build import swap, timetable
from paperroute_transit.build.gtfs_files import Source
from paperroute_transit.build.handoff import (
    Attempt,
    Fetched,
    HandoffError,
    HandoffRecord,
    RefreshTail,
    read_handoff,
    write_handoff,
)
from paperroute_transit.build.layout import Layout
from paperroute_transit.build.source_state import (
    SourceState,
    rule_file,
    save_states,
)
from paperroute_transit.build.swap import TimetableCheckError, check_timetable
from paperroute_transit.build.timetable import Window, build_timetable
from tests.build.network_feed import network_feed

LOCK_PROBE: Final[str] = (
    'import sys\n'
    'from pathlib import Path\n'
    'from paperroute_transit.build.swap import holds_lock\n'
    'sys.exit(0 if holds_lock(fd=int(sys.argv[2]), path=Path(sys.argv[1]))'
    ' else 3)\n'
)
LOCK_AND_EXEC: Final[str] = (
    'import sys\n'
    'from pathlib import Path\n'
    'from paperroute_transit.build.refresh import refresh_lock\n'
    'from paperroute_transit.build.handoff import exec_python\n'
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
            timetable, 'build_extras', lambda *, path, city_ref, tiles: None,
        )
        build_timetable(city_ref=SYDNEY_REF, sources=[source],
                        window=Window(start=date(2026, 10, 5), days=3),
                        out=folder / 'timetable.db')
    return folder / 'timetable.db'


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
                downloaded=('metro',), changed=(), failed=(),
                optional=('rail',),
            ),
            started=time.monotonic(),
            error_log=data_dir / 'log' / 'errors.jsonl',
            lock_fd=lock_fd,
        ),
    )


def attempt_in(*, data_dir: Path) -> Attempt:
    """
    An attempt that builds from the stored metro zip without rail.

    Parameters
    ----------
    data_dir : Path
        Data dir.

    Returns
    -------
    Attempt
        The attempt.
    """
    return Attempt(
        fetched=Fetched(
            states={'metro': SourceState(
                etag='"1"', last_modified=None, sha256='cd' * 32,
            )},
            downloaded=(), changed=(), failed=('rail',),
            optional=('rail',),
        ),
        sources=(Source(
            name='metro', archive=data_dir / 'sources' / 'metro.zip',
        ),),
        start=date(2026, 10, 16),
        days=9,
    )


def with_fallback(*, record: HandoffRecord, data_dir: Path) -> HandoffRecord:
    """
    The record with one attempt left after it.

    Parameters
    ----------
    record : HandoffRecord
        A refresh record.
    data_dir : Path
        Data dir.

    Returns
    -------
    HandoffRecord
        The record, its tail holding the attempt.
    """
    assert record.tail is not None
    return replace(record, tail=replace(
        record.tail, fallbacks=(attempt_in(data_dir=data_dir),),
    ))


def test_handoff_round_trip(data_dir: Path) -> None:
    """A record reads back equal and holds only paths, states and counts."""
    record = with_fallback(
        record=refresh_record(data_dir=data_dir, lock_fd=7),
        data_dir=data_dir,
    )
    path = data_dir / 'work' / 'handoff.json'
    write_handoff(path=path, record=record)
    assert read_handoff(path=path) == record
    raw = json.loads(path.read_text(encoding='utf-8'))
    assert set(raw) == {
        'scratch', 'out', 'dropped', 'partial', 'overnight', 'peak_mb',
        'tiles', 'tail',
    }
    assert set(raw['tail']) == {
        'data_dir', 'states', 'downloaded', 'changed', 'failed',
        'optional', 'started', 'error_log', 'lock_fd', 'fallbacks',
    }
    plain = replace(record, tail=None)
    write_handoff(path=path, record=plain)
    assert read_handoff(path=path) == plain


def test_handoff_without_tiles_reads_as_none(data_dir: Path) -> None:
    """A record written before the tiles key existed has no tiles."""
    record = refresh_record(data_dir=data_dir, lock_fd=7)
    path = data_dir / 'work' / 'handoff.json'
    write_handoff(path=path, record=replace(record, tiles=str(data_dir / 'tiles.db')))
    raw = json.loads(path.read_text(encoding='utf-8'))
    del raw['tiles']
    path.write_text(json.dumps(raw), encoding='utf-8')
    assert read_handoff(path=path).tiles == ''


def test_handoff_refuses_tiles_outside_the_data_dir(
    data_dir: Path, tmp_path: Path,
) -> None:
    """A refresh's tiles file must be inside its data dir."""
    record = refresh_record(data_dir=data_dir, lock_fd=7)
    path = data_dir / 'work' / 'handoff.json'
    inside = replace(record, tiles=str(data_dir / 'tiles.db'))
    write_handoff(path=path, record=inside)
    assert read_handoff(path=path) == inside
    with pytest.raises(HandoffError):
        write_handoff(
            path=path, record=replace(record, tiles=str(tmp_path / 't.db')),
        )


def test_handoff_without_failed_reads_as_none_failed(
    data_dir: Path,
) -> None:
    """A handoff written before failed existed still loads."""
    record = refresh_record(data_dir=data_dir, lock_fd=7)
    path = data_dir / 'work' / 'handoff.json'
    raw = handoff_module.record_json(record=record)
    tail = raw['tail']
    assert isinstance(tail, dict)
    del tail['failed']
    path.write_text(json.dumps(raw), encoding='utf-8')
    assert read_handoff(path=path) == record


def test_handoff_without_fallbacks_reads_as_none_left(
    data_dir: Path,
) -> None:
    """A handoff written before fallbacks existed has no attempt left."""
    record = refresh_record(data_dir=data_dir, lock_fd=7)
    path = data_dir / 'work' / 'handoff.json'
    raw = handoff_module.record_json(record=record)
    tail = raw['tail']
    assert isinstance(tail, dict)
    del tail['fallbacks']
    path.write_text(json.dumps(raw), encoding='utf-8')
    assert read_handoff(path=path) == record


def test_handoff_refuses_a_fallback_outside_the_data_dir(
    data_dir: Path, tmp_path: Path,
) -> None:
    """Every archive an attempt names must be inside the data dir."""
    record = with_fallback(
        record=refresh_record(data_dir=data_dir, lock_fd=7),
        data_dir=data_dir,
    )
    assert record.tail is not None
    away = replace(
        record.tail.fallbacks[0],
        sources=(Source(name='metro', archive=tmp_path / 'metro.zip'),),
    )
    with pytest.raises(HandoffError):
        write_handoff(
            path=data_dir / 'work' / 'handoff.json',
            record=replace(
                record, tail=replace(record.tail, fallbacks=(away,)),
            ),
        )


def test_handoff_refuses_malformed_fallbacks(data_dir: Path) -> None:
    """A fallback that is not a well-typed attempt is refused."""
    raw = handoff_module.record_json(record=with_fallback(
        record=refresh_record(data_dir=data_dir, lock_fd=7),
        data_dir=data_dir,
    ))
    tail = raw['tail']
    assert isinstance(tail, dict)
    (attempt,) = tail['fallbacks']
    path = data_dir / 'work' / 'handoff.json'
    for broken in (
        [3],
        [{**attempt, 'start': '16 Oct'}],
        [{**attempt, 'sources': [3]}],
        [{**attempt, 'days': '9'}],
    ):
        path.write_text(
            json.dumps({**raw, 'tail': {**tail, 'fallbacks': broken}}),
            encoding='utf-8',
        )
        with pytest.raises(HandoffError):
            read_handoff(path=path)


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
    assert extras.main(argv=[SYDNEY_REF, str(record.scratch), '', str(handoff)]) == 0
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
    assert report['failed'] == [] and not report['all_failed']
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
    assert extras.main(argv=[SYDNEY_REF, str(record.scratch), '', str(handoff)]) == 1
    assert (data_dir / 'timetable.db').read_bytes() == b'old timetable'
    assert not (data_dir / 'sources' / 'sources.json').exists()
    assert not list((data_dir / 'work').iterdir())
    lines = (data_dir / 'log' / 'errors.jsonl').read_text(
        encoding='utf-8',
    ).splitlines()
    assert json.loads(lines[0])['msg'] == 'refresh failed'


@pytest.mark.usefixtures('restored_logging')
def test_extras_check_counts_only_the_required_sources(
    data_dir: Path, loaded: Path, lock_fd: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After a hand over, an optional source's trips do not count."""
    monkeypatch.setattr(swap, 'MIN_TRIPS', 1)
    record = refresh_record(data_dir=data_dir, lock_fd=lock_fd)
    assert record.tail is not None
    fetched = replace(record.tail.fetched, optional=('sydneytrains',))
    record = replace(record, tail=replace(record.tail, fetched=fetched))
    shutil.copy(loaded, record.scratch)
    handoff = data_dir / 'work' / 'handoff.json'
    write_handoff(path=handoff, record=record)
    assert extras.main(
        argv=[SYDNEY_REF, str(record.scratch), '', str(handoff)],
    ) == 1
    assert (data_dir / 'timetable.db').read_bytes() == b'old timetable'
    assert 'TimetableCheckError' in (
        data_dir / 'log' / 'errors.jsonl'
    ).read_text(encoding='utf-8')


def test_check_counts_only_the_trips_of_required_sources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rail trips cannot lift a short bus timetable over the floor."""
    monkeypatch.setattr(swap, 'MIN_TRIPS', 3)
    path = tmp_path / 'timetable.db'
    with closing(sqlite3.connect(path)) as conn:
        conn.execute('CREATE TABLE trips (trip_id TEXT, source TEXT)')
        conn.executemany('INSERT INTO trips VALUES (?, ?)', [
            ('b1', 'bods'), ('b2', 'bods'),
            *((f'r{n}', 'rail') for n in range(5)),
        ])
        conn.commit()
    check_timetable(path=path)
    with pytest.raises(TimetableCheckError, match='2 trips, want 3'):
        check_timetable(path=path, optional=('rail',))


@pytest.mark.usefixtures('restored_logging')
def test_lost_lock_stops_the_swap(data_dir: Path, loaded: Path) -> None:
    """Without the lock after the exec, nothing is swapped in."""
    record = refresh_record(data_dir=data_dir, lock_fd=999)
    shutil.copy(loaded, record.scratch)
    handoff = data_dir / 'work' / 'handoff.json'
    write_handoff(path=handoff, record=record)
    assert extras.main(argv=[SYDNEY_REF, str(record.scratch), '', str(handoff)]) == 1
    assert (data_dir / 'timetable.db').read_bytes() == b'old timetable'
    assert 'LockLostError' in (data_dir / 'log' / 'errors.jsonl').read_text(
        encoding='utf-8',
    )


class Execed(Exception):
    """Raised by the stand-in for os.execv."""


@pytest.mark.usefixtures('restored_logging')
def test_failed_extras_hand_back_to_the_retry_program(
    data_dir: Path, lock_fd: int, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With an attempt left, a failure execs into retry, lock kept."""
    seen: list[list[str]] = []

    def execv(path: str, argv: list[str]) -> None:
        """
        Note the arguments instead of replacing the process.

        Parameters
        ----------
        path : str
            Interpreter.
        argv : list[str]
            Its arguments.

        Raises
        ------
        Execed
            Always.
        """
        seen.append([path, *argv[1:]])
        raise Execed

    monkeypatch.setattr(os, 'execv', execv)
    record = with_fallback(
        record=refresh_record(data_dir=data_dir, lock_fd=lock_fd),
        data_dir=data_dir,
    )
    record.scratch.write_bytes(b'not a timetable')
    handoff = data_dir / 'work' / 'handoff.json'
    write_handoff(path=handoff, record=record)
    with pytest.raises(Execed):
        extras.main(argv=[SYDNEY_REF, str(record.scratch), '', str(handoff)])
    assert seen == [[
        sys.executable, '-m', 'paperroute_transit.build.retry',
        SYDNEY_REF, str(handoff),
    ]]
    assert handoff.exists()
    assert os.get_inheritable(lock_fd)
    assert (data_dir / 'timetable.db').read_bytes() == b'old timetable'


@pytest.mark.usefixtures('restored_logging')
def test_lost_lock_never_hands_back(
    data_dir: Path, loaded: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without the lock, an attempt left is not tried."""
    monkeypatch.setattr(os, 'execv', None)
    record = with_fallback(
        record=refresh_record(data_dir=data_dir, lock_fd=999),
        data_dir=data_dir,
    )
    shutil.copy(loaded, record.scratch)
    handoff = data_dir / 'work' / 'handoff.json'
    write_handoff(path=handoff, record=record)
    assert extras.main(
        argv=[SYDNEY_REF, str(record.scratch), '', str(handoff)],
    ) == 1
    assert not list((data_dir / 'work').iterdir())


@pytest.mark.usefixtures('restored_logging')
def test_failure_inside_the_swap_never_hands_back(
    data_dir: Path, loaded: Path, lock_fd: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A fault after the swap's first move is a plain failure."""
    monkeypatch.setattr(swap, 'MIN_TRIPS', 1)
    monkeypatch.setattr(os, 'execv', None)

    def broken(*, path: Path) -> None:
        """
        Fail the way a full disk would, after the first move.

        Parameters
        ----------
        path : Path
            Folder to sync.

        Raises
        ------
        OSError
            Always.
        """
        raise OSError(f'cannot sync {path}')

    monkeypatch.setattr(swap, 'fsync_dir', broken)
    record = with_fallback(
        record=refresh_record(data_dir=data_dir, lock_fd=lock_fd),
        data_dir=data_dir,
    )
    shutil.copy(loaded, record.scratch)
    handoff = data_dir / 'work' / 'handoff.json'
    write_handoff(path=handoff, record=record)
    assert extras.main(
        argv=[SYDNEY_REF, str(record.scratch), '', str(handoff)],
    ) == 1
    assert not list((data_dir / 'work').iterdir())
    with pytest.raises(OSError):
        os.fstat(lock_fd)


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
        extras.main(argv=[SYDNEY_REF, str(tmp_path / 'other.new'), '', str(handoff)])
    assert not handoff.exists()
    write_handoff(path=handoff, record=record)
    assert extras.main(argv=[SYDNEY_REF, str(record.scratch), '', str(handoff)]) == 0
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
        assert extras.main(argv=[SYDNEY_REF, str(tmp_path / 'x.new'), '', str(path)]) == 1
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
    assert extras.main(argv=[SYDNEY_REF, other, '', str(handoff)]) == 1
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
        assert extras.main(argv=[SYDNEY_REF, str(record.scratch), '', str(handoff)]) == 1
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


@pytest.mark.parametrize(
    ('failed', 'all_failed', 'code'),
    [((), False, 0), (('metro',), False, 0), (('metro',), True, 1)],
)
def test_printed_fails_only_when_every_source_failed(
    failed: tuple[str, ...], all_failed: bool, code: int,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The report is printed; the exit is 1 when no source refreshed."""
    report = swap.RefreshReport(
        downloaded=(), changed=(), failed=failed, all_failed=all_failed,
        rebuilt=True, seconds=1.0, max_rss_mb=1.0,
    )
    assert swap.printed(report=report) == code
    assert json.loads(capsys.readouterr().out)['failed'] == list(failed)


def test_printed_without_a_report_fails() -> None:
    """A refresh that raised has no report and exits 1."""
    assert swap.printed(report=None) == 1


def test_fetched_all_failed_needs_every_source() -> None:
    """Some failed sources are not all of them."""
    state = SourceState(etag=None, last_modified=None, sha256='ab' * 32)
    states = {'metro': state, 'bundle': state}
    some = Fetched(
        states=states, downloaded=(), changed=(), failed=('metro',),
    )
    every = replace(some, failed=('metro', 'bundle'))
    none = replace(some, states={}, failed=())
    assert not some.all_failed
    assert every.all_failed
    assert not none.all_failed


def test_swap_in_removes_the_rule_file_of_a_forgotten_copy(
    tmp_path: Path,
) -> None:
    """A source left out of the stored states loses its rule file."""
    layout = Layout(data_dir=tmp_path)
    layout.work.mkdir()
    layout.sources.mkdir()
    state = SourceState(etag=None, last_modified=None, sha256='ab' * 32)
    save_states(path=layout.states, states={'rail': state, 'bods': state})
    for name in ('rail', 'bods'):
        rule_file(directory=layout.sources, name=name).write_text('A', encoding='utf-8')
    built = layout.work / 'timetable.db'
    built.write_bytes(b'db')
    fetched = Fetched(states={'bods': state}, downloaded=(), changed=())
    swap.swap_in(layout=layout, built=built, fetched=fetched)
    assert not rule_file(directory=layout.sources, name='rail').exists()
    assert rule_file(directory=layout.sources, name='bods').exists()
