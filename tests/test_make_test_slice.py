"""Tests for the fixture slicing script's shape step."""

import importlib.util
import sys
import zipfile
from pathlib import Path
from types import ModuleType

import pytest

from paperroute_testkit.build.feed_zip import write_feed
from tests.build.feeds import shape_rows

SCRIPT = Path(__file__).parent.parent / 'scripts' / 'make_test_slice.py'
POINTS = [(151.0, -33.8), (151.1, -33.9)]


def load_script() -> ModuleType:
    """
    Import the slicing script.

    Returns
    -------
    ModuleType
        The script as a module.
    """
    spec = importlib.util.spec_from_file_location('make_test_slice', SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def fixture_zip(*, path: Path, shapes: list[str], column: bool = True) -> Path:
    """
    Write a slice zip whose trips name the given shapes.

    Parameters
    ----------
    path : Path
        Zip to write.
    shapes : list[str]
        Shape ID of each trip.
    column : bool
        Whether trips.txt has a shape_id column.

    Returns
    -------
    Path
        The zip.
    """
    trips = [
        {'trip_id': f't{i}', **({'shape_id': s} if column else {})}
        for i, s in enumerate(shapes)
    ]
    return write_feed(
        path=path,
        files={'stops.txt': [{'stop_id': 'a'}], 'trips.txt': trips},
    )


def source_zip(*, path: Path) -> Path:
    """
    Write a source zip with shapes S1, S2 and S3.

    Parameters
    ----------
    path : Path
        Zip to write.

    Returns
    -------
    Path
        The zip.
    """
    rows = [
        row
        for name in ('S1', 'S2', 'S3')
        for row in shape_rows(shape_id=name, points=POINTS)
    ]
    return write_feed(path=path, files={'shapes.txt': rows})


def test_add_shapes_appends_only_named_shapes(tmp_path: Path) -> None:
    """Named shapes are added and every other member is untouched."""
    script = load_script()
    fixture = fixture_zip(path=tmp_path / 'f.zip', shapes=['S1', 'S3', ''])
    before = zipfile.ZipFile(fixture).read('trips.txt')
    count = script.add_shapes(
        source=source_zip(path=tmp_path / 's.zip'), fixture=fixture
    )
    with zipfile.ZipFile(fixture) as archive:
        lines = archive.read('shapes.txt').decode().splitlines()
        assert archive.read('trips.txt') == before
    assert count == 4
    assert {line.split(',')[0] for line in lines[1:]} == {'S1', 'S3'}
    assert not list(tmp_path.glob('*.new'))


def test_add_shapes_limits_to_the_chosen_ids(tmp_path: Path) -> None:
    """Only the chosen ones of the named shapes are copied."""
    script = load_script()
    fixture = fixture_zip(path=tmp_path / 'f.zip', shapes=['S1', 'S2'])
    count = script.add_shapes(
        source=source_zip(path=tmp_path / 's.zip'),
        fixture=fixture,
        shape_ids={'S2', 'S3'},
    )
    assert count == 2


def test_add_shapes_refuses_a_second_shapes_member(tmp_path: Path) -> None:
    """A fixture that has shapes.txt is not given another."""
    script = load_script()
    fixture = fixture_zip(path=tmp_path / 'f.zip', shapes=['S1'])
    source = source_zip(path=tmp_path / 's.zip')
    script.add_shapes(source=source, fixture=fixture)
    with pytest.raises(ValueError, match='already has shapes.txt'):
        script.add_shapes(source=source, fixture=fixture)


@pytest.mark.parametrize(
    ('shapes', 'column', 'message'),
    [
        (['S1'], False, 'no shape_id'),
        ([''], True, 'name no shapes'),
        (['S9'], True, 'holds none'),
    ],
)
def test_add_shapes_fails_clearly_and_leaves_the_fixture(
    tmp_path: Path, shapes: list[str], column: bool, message: str
) -> None:
    """Each way of finding nothing to add is an error, with no change."""
    script = load_script()
    fixture = fixture_zip(
        path=tmp_path / 'f.zip', shapes=shapes, column=column
    )
    before = fixture.read_bytes()
    with pytest.raises(ValueError, match=message):
        script.add_shapes(
            source=source_zip(path=tmp_path / 's.zip'), fixture=fixture
        )
    assert fixture.read_bytes() == before
    assert not list(tmp_path.glob('*.new'))


def test_sample_shapes_keeps_606_and_one_each_of_the_others() -> None:
    """The bundle sample is every 606 shape plus one T1 and one regional."""
    script = load_script()
    shapes = script.sample_shapes(
        archive=SCRIPT.parent.parent / 'tests/fixtures/real/bundle.zip'
    )
    assert len([s for s in shapes if '-606-' in s]) == 7
    assert len([s for s in shapes if '-T1-' in s]) == 1
    assert len(shapes) == 9
