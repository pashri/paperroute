"""Tests for refresh: handover."""

import os
from pathlib import Path

import pytest

from paperroute_sydney.city import SYDNEY, SYDNEY_REF
from paperroute_transit.build import handoff as handoff_module
from paperroute_transit.build import refresh as refresh_module
from paperroute_transit.build.refresh import refresh
from tests.build.feed_server import FeedServer
from tests.build.refresh.helpers import DAYS, parts_for


class Execed(Exception):
    """Raised by the stand-in for os.execv."""


def test_refresh_hands_over_after_the_load(
    server: FeedServer,
    data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With hand_over, the load execs into extras with a checked handoff."""
    seen: dict[str, object] = {}

    def execv(path: str, argv: list[str]) -> None:
        record = handoff_module.read_handoff(path=Path(argv[-1]))
        assert record.tail is not None
        seen.update(
            path=path, argv=argv, record=record,
            inheritable=os.get_inheritable(record.tail.lock_fd),
        )
        raise Execed

    monkeypatch.setattr(os, 'execv', execv)
    with pytest.raises(Execed):
        refresh(
            data_dir=data_dir, city=SYDNEY, parts=parts_for(server=server),
            days=DAYS, error_log=data_dir / 'log' / 'errors.jsonl',
            hand_over=True,
        )
    work = data_dir / 'work'
    assert seen['argv'] == [
        seen['path'], '-m', 'paperroute_transit.build.extras', SYDNEY_REF,
        str(work / 'timetable.new'), str(data_dir / 'tiles.db'),
        str(work / 'handoff.json'),
    ]
    assert seen['inheritable'] is True
    record = seen['record']
    assert isinstance(record, handoff_module.HandoffRecord)
    assert record.tail is not None
    assert record.tail.fetched.changed == ('sydneytrains', 'metro', 'bundle')
    assert record.tail.fallbacks == ()
    assert record.out == work / 'timetable.db'
    assert not (data_dir / 'timetable.db').exists()


def test_refresh_hands_the_tiles_to_the_build(
    server: FeedServer,
    data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every build attempt is given the data dir's tiles file."""
    seen: list[object] = []

    def build(**kwargs: object) -> None:
        seen.append(kwargs['tiles'])
        raise Execed

    monkeypatch.setattr(refresh_module, 'build_timetable', build)
    with pytest.raises(Execed):
        refresh(
            data_dir=data_dir, city=SYDNEY, parts=parts_for(server=server),
            days=DAYS, error_log=data_dir / 'log' / 'errors.jsonl',
        )
    assert seen
    assert set(seen) == {data_dir / 'tiles.db'}
