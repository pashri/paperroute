"""Tests for the state.json repository."""

import json
import logging
import os
import stat
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from paperroute.adapters.state_file import (
    SCHEMA_VERSION,
    JsonStateRepository,
    regional_to_coach,
    write_atomic,
)
from paperroute.core.model import Pin, RouteKind, StopPlace
from paperroute.core.saved import DEFAULT_SAVED, LiveView, Saved
from paperroute.core.tiles import ZOOMS

NAMES = st.text(
    alphabet=st.characters(exclude_categories=['Cs']),
    max_size=20,
) | st.sampled_from(['Café 🚌', 'A · B'])
COORDINATES = st.floats(allow_nan=False, allow_infinity=False)
POINTS = st.none() | st.tuples(COORDINATES, COORDINATES)
PLACES = st.builds(
    StopPlace,
    stop_id=st.text(min_size=1, max_size=8),
    name=NAMES,
    point=POINTS,
)
PINS = st.builds(
    Pin,
    origin=PLACES,
    destination=st.none() | PLACES,
    kind=st.none() | st.sampled_from(RouteKind),
    routes=st.lists(st.text(max_size=5), max_size=3).map(tuple),
)


@st.composite
def saved_values(draw: st.DrawFn) -> Saved:
    """
    Draw a Saved value that the reducer could produce.

    Parameters
    ----------
    draw : st.DrawFn
        Hypothesis draw function.

    Returns
    -------
    Saved
        Zero to five pins, a valid current index, any view and zoom.
    """
    pins = tuple(draw(st.lists(PINS, max_size=5)))
    unpinned = draw(st.none() | PINS) if pins else draw(PINS)
    return Saved(
        pins=pins,
        current=draw(st.integers(0, max(len(pins) - 1, 0))),
        unpinned=unpinned,
        view=draw(st.sampled_from(LiveView)),
        zoom=draw(st.integers(0, len(ZOOMS) - 1)),
    )


def sample() -> Saved:
    """
    Build a small saved state.

    Returns
    -------
    Saved
        One pin with a destination and a route.
    """
    pin = Pin(
        origin=StopPlace(stop_id='a', name='A', point=(1.5, -2.5)),
        destination=StopPlace(stop_id='b', name='B', point=None),
        kind=RouteKind.BUS,
        routes=('333',),
    )
    return Saved(
        pins=(pin, pin),
        current=1,
        unpinned=None,
        view=LiveView.STRIP,
        zoom=0,
    )


@pytest.fixture(name='path')
def path_fixture(tmp_path: Path) -> Path:
    """
    The state file's path.

    Parameters
    ----------
    tmp_path : Path
        Temporary directory.

    Returns
    -------
    Path
        A path that doesn't exist yet.
    """
    return tmp_path / 'state.json'


@settings(max_examples=60, deadline=None)
@given(saved=saved_values())
def test_round_trip(saved: Saved) -> None:
    """What is saved is what loads."""
    with tempfile.TemporaryDirectory() as directory:
        repository = JsonStateRepository(path=Path(directory) / 's.json')
        repository.save(saved=saved)
        loaded = repository.load()
    assert loaded.saved == saved
    assert loaded.stored


def test_missing_file_gives_defaults_quietly(
    path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """No file is normal: defaults, not stored, nothing logged."""
    loaded = JsonStateRepository(path=path).load()
    assert loaded.saved == DEFAULT_SAVED
    assert not loaded.stored
    assert not caplog.records


def test_truncated_file_gives_defaults(
    path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A cut-off file gives defaults and an error log."""
    repository = JsonStateRepository(path=path)
    repository.save(saved=sample())
    path.write_text(path.read_text()[:40])
    loaded = repository.load()
    assert loaded.saved == DEFAULT_SAVED
    assert not loaded.stored
    assert [r.levelno for r in caplog.records] == [logging.ERROR]


@pytest.mark.parametrize(
    'text',
    ['not json', '[]', '{}', '{"schema_version": "1"}', '\ud800'],
)
def test_garbage_and_wrong_types_give_defaults(
    path: Path, text: str, caplog: pytest.LogCaptureFixture
) -> None:
    """Unusable content gives the defaults."""
    path.write_bytes(text.encode('utf-8', errors='surrogatepass'))
    loaded = JsonStateRepository(path=path).load()
    assert loaded.saved == DEFAULT_SAVED
    assert not loaded.stored
    assert [r.levelno for r in caplog.records] == [logging.ERROR]


def test_wrong_field_type_gives_defaults(path: Path) -> None:
    """A field of the wrong type is rejected."""
    repository = JsonStateRepository(path=path)
    repository.save(saved=sample())
    data = json.loads(path.read_text())
    data['zoom'] = 'big'
    path.write_text(json.dumps(data))
    assert repository.load().saved == DEFAULT_SAVED


def test_future_version_gives_defaults(
    path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A file from a newer app is not guessed at."""
    repository = JsonStateRepository(path=path)
    repository.save(saved=sample())
    data = json.loads(path.read_text())
    data['schema_version'] = SCHEMA_VERSION + 1
    path.write_text(json.dumps(data))
    loaded = repository.load()
    assert loaded.saved == DEFAULT_SAVED
    assert not loaded.stored
    assert caplog.records


def test_unusable_file_is_kept_aside(path: Path) -> None:
    """A newer file survives a load and a save under a ``.bad-`` name."""
    repository = JsonStateRepository(path=path)
    repository.save(saved=sample())
    data = json.loads(path.read_text())
    data['schema_version'] = SCHEMA_VERSION + 1
    path.write_text(json.dumps(data))
    repository.save(saved=repository.load().saved)
    kept = list(path.parent.glob('state.json.bad-*'))
    assert len(kept) == 1
    assert json.loads(kept[0].read_text()) == data


def test_only_the_newest_unusable_files_are_kept(path: Path) -> None:
    """Older ``.bad-`` files beyond the newest two are deleted."""
    for stamp in ('100', '20', '3'):
        path.with_name(f'state.json.bad-{stamp}').write_text('old')
    path.write_text('not json')
    JsonStateRepository(path=path).load()
    kept = sorted(p.name for p in path.parent.glob('state.json.bad-*'))
    assert len(kept) == 2
    assert 'state.json.bad-100' in kept
    assert not path.exists()


def test_current_out_of_range_is_clamped(path: Path) -> None:
    """A current index past the last pin becomes the last pin."""
    repository = JsonStateRepository(path=path)
    repository.save(saved=sample())
    data = json.loads(path.read_text())
    data['current'] = 9
    path.write_text(json.dumps(data))
    loaded = repository.load()
    assert loaded.saved.current == 1
    assert loaded.stored


def test_no_pins_and_no_unpinned_gives_default_place(path: Path) -> None:
    """An empty pin list still shows a place."""
    repository = JsonStateRepository(path=path)
    repository.save(saved=sample())
    data = json.loads(path.read_text())
    data['pins'] = []
    path.write_text(json.dumps(data))
    loaded = repository.load().saved
    assert loaded.unpinned == DEFAULT_SAVED.unpinned
    assert loaded.current == 0


@pytest.fixture(name='locked')
def locked_fixture(tmp_path: Path) -> Iterator[Path]:
    """
    A state file in a read-only directory.

    Parameters
    ----------
    tmp_path : Path
        Temporary directory.

    Yields
    ------
    Path
        The state file, holding saved state.
    """
    directory = tmp_path / 'ro'
    directory.mkdir()
    path = directory / 'state.json'
    JsonStateRepository(path=path).save(saved=sample())
    directory.chmod(stat.S_IRUSR | stat.S_IXUSR)
    yield path
    directory.chmod(stat.S_IRWXU)


@pytest.mark.skipif(os.geteuid() == 0, reason='root ignores permissions')
def test_failed_write_keeps_old_file(locked: Path) -> None:
    """A write that can't happen leaves the old file and no temp file."""
    before = locked.read_text()
    repository = JsonStateRepository(path=locked)
    with pytest.raises(OSError):
        repository.save(saved=DEFAULT_SAVED)
    assert locked.read_text() == before
    assert [p.name for p in locked.parent.iterdir()] == ['state.json']


def test_file_holds_no_secret_keys(path: Path) -> None:
    """Only the documented keys are written."""
    JsonStateRepository(path=path).save(saved=sample())
    data = json.loads(path.read_text())
    assert set(data) == {
        'schema_version',
        'pins',
        'current',
        'unpinned',
        'view',
        'zoom',
    }
    assert set(data['pins'][0]) == {
        'origin',
        'destination',
        'kind',
        'routes',
    }


def test_written_json_shape(path: Path) -> None:
    """The version is 2 and stops carry their metre coordinates."""
    JsonStateRepository(path=path).save(saved=sample())
    data = json.loads(path.read_text())
    assert data['schema_version'] == 2
    assert data['pins'][0]['origin'] == {
        'stop_id': 'a',
        'name': 'A',
        'x': 1.5,
        'y': -2.5,
    }
    assert data['pins'][0]['destination']['x'] is None


def test_load_removes_stale_temp_files(path: Path) -> None:
    """A temp file left by a power cut is deleted on load."""
    stale = path.parent / '.state.json.abc123'
    stale.write_text('half')
    other = path.parent / 'keep.txt'
    other.write_text('x')
    JsonStateRepository(path=path).load()
    assert not stale.exists()
    assert other.exists()


def test_failed_rename_removes_temp_file(path: Path) -> None:
    """When the rename fails, the temp file is cleaned up."""
    path.mkdir()
    (path / 'inner').write_text('x')
    with pytest.raises(OSError):
        JsonStateRepository(path=path).save(saved=sample())
    assert [p.name for p in path.parent.iterdir()] == ['state.json']


def test_written_file_is_world_readable(path: Path) -> None:
    """The file holds no secrets and is mode 0644."""
    JsonStateRepository(path=path).save(saved=sample())
    assert stat.S_IMODE(path.stat().st_mode) == 0o644


def failing_sync(directory: Path) -> None:
    """
    Fail the way a directory fsync can.

    Parameters
    ----------
    directory : Path
        The directory.

    Raises
    ------
    OSError
        Always.
    """
    raise OSError(f'cannot sync {directory}')


def test_directory_sync_failure_is_logged_not_raised(
    path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The rename has landed, so a sync failure only warns."""
    write_atomic(path=path, text='{}', sync=failing_sync)
    assert path.read_text() == '{}'
    assert [r.levelno for r in caplog.records] == [logging.WARNING]


def test_directory_at_path_gives_defaults(
    path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Something that isn't a file at the path gives defaults and a log."""
    path.mkdir()
    loaded = JsonStateRepository(path=path).load()
    assert loaded.saved == DEFAULT_SAVED
    assert not loaded.stored
    assert [r.levelno for r in caplog.records] == [logging.ERROR]


def test_deeply_nested_json_gives_defaults(path: Path) -> None:
    """JSON nested past the recursion limit gives defaults."""
    path.write_text('[' * 100000)
    loaded = JsonStateRepository(path=path).load()
    assert loaded.saved == DEFAULT_SAVED
    assert not loaded.stored


def v1_pin(*, kind: str, routes: list[str]) -> dict[str, object]:
    """
    Build a stored version 1 pin.

    Parameters
    ----------
    kind : str
        Stored kind.
    routes : list[str]
        Stored route badges.

    Returns
    -------
    dict[str, object]
        The pin as JSON.
    """
    stop = {'stop_id': 'a', 'name': 'A', 'x': 1.0, 'y': 2.0}
    return {
        'origin': stop, 'destination': None, 'kind': kind, 'routes': routes,
    }


def test_migrate_regional_pin_to_coach(path: Path) -> None:
    """Regional pins load as Coach, or Train for a TrainLink line."""
    path.write_text(json.dumps({
        'schema_version': 1,
        'pins': [
            v1_pin(kind='regional', routes=['C1']),
            v1_pin(kind='regional', routes=['STH']),
            v1_pin(kind='regional', routes=[]),
            v1_pin(kind='bus', routes=['333']),
        ],
        'current': 0,
        'unpinned': v1_pin(kind='regional', routes=['NRC', 'WST']),
        'view': 'strip',
        'zoom': 0,
    }))
    loaded = JsonStateRepository(path=path).load()
    assert loaded.stored
    assert [pin.kind for pin in loaded.saved.pins] == [
        RouteKind.COACH, RouteKind.TRAIN, RouteKind.COACH, RouteKind.BUS,
    ]
    assert loaded.saved.unpinned is not None
    assert loaded.saved.unpinned.kind is RouteKind.TRAIN
    assert not list(path.parent.glob('state.json.bad-*'))


def test_migrate_regional_with_no_unpinned_place(path: Path) -> None:
    """A null unpinned place stays null."""
    path.write_text(json.dumps({
        'schema_version': 1,
        'pins': [v1_pin(kind='regional', routes=['C1'])],
        'current': 0,
        'unpinned': None,
        'view': 'strip',
        'zoom': 0,
    }))
    loaded = JsonStateRepository(path=path).load()
    assert loaded.stored
    assert loaded.saved.unpinned is None
    assert loaded.saved.pins[0].kind is RouteKind.COACH


def test_regional_to_coach_leaves_a_malformed_pin_list_alone() -> None:
    """Data the migration cannot read is passed on for validation to fail."""
    data: dict[str, object] = {'pins': 'oops', 'unpinned': 3}
    assert regional_to_coach(data) == data
