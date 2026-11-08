"""The state file keeps today's bytes."""

from dataclasses import replace
from datetime import date, datetime, time
from pathlib import Path
from typing import Final

from paperroute.adapters.state_file import JsonStateRepository
from paperroute.city import SYDNEY, SYDNEY_ZONE
from paperroute.core.saved import (
    BoardSettings,
    ConnectionMap,
    LiveView,
    Lookahead,
    Quota,
    Rate,
    Saved,
    Settings,
)
from paperroute_device.core.device_settings import (
    DeviceSettings,
    QuietMode,
    Window,
)
from paperroute_testkit.core.connection_factories import TWO_WAY, WORK
from paperroute_testkit.core.factories import pin_at
from paperroute_transit.core.auto_model import AutoState, Before, running
from paperroute_transit.core.model import AutoWindow, Pin, RouteKind
from tests.golden.scenes import GOLDEN, baseline_bytes

STATE: Final[Path] = GOLDEN / 'state.json'
WORK_WAYS: Final[Pin] = replace(WORK, ways=(*WORK.ways, TWO_WAY))
HOME: Final[Pin] = replace(
    pin_at('home', x=100.0),
    kind=RouteKind.BUS,
    routes=('606', '609'),
    only=('606',),
    never=('660',),
    windows=(AutoWindow(frozenset({0, 1, 2, 3, 4}), time(17), time(18)),),
)
AUTO_AT: Final[datetime] = datetime(2026, 10, 16, 17, 20, tzinfo=SYDNEY_ZONE)
RICH_SAVED: Final[Saved] = Saved(
    pins=(WORK_WAYS, HOME),
    current=1,
    unpinned=None,
    view=LiveView.STRIP,
    settings=Settings(
        device=DeviceSettings(
            auto_rebuild=False,
            quiet_mode=QuietMode.TIMETABLE,
            my_hours=Window(start=time(22, 15), end=time(6)),
            rotate=True,
            larger_text=True,
            inverted_lists=True,
            clean_after=60,
            swap_knobs=True,
        ),
        board=BoardSettings(
            peak_rate=Rate.SLOW,
            offpeak_rate=Rate.STEADY,
            peak_windows=(Window(start=time(7), end=time(9)),),
            alert_lookahead=Lookahead.THREE_DAYS,
            connection_map=ConnectionMap.WHOLE,
            skip_school_buses=False,
            skip_school_on_route_pins=True,
        ),
    ),
    quota=Quota(day=date(2026, 10, 16), calls=1234),
    auto=AutoState(
        occurrence=running(pins=(HOME,), now=AUTO_AT, zone=SYDNEY_ZONE),
        before=Before(place=WORK_WAYS, pinned=True),
        overridden_at=AUTO_AT,
    ),
)


def test_state_file_bytes_match_baseline(tmp_path: Path) -> None:
    """Saving the rich state writes the baseline's bytes."""
    path = tmp_path / 'state.json'
    JsonStateRepository(path=path, city=SYDNEY).save(saved=RICH_SAVED)
    data = path.read_bytes()
    assert data == baseline_bytes(path=STATE, data=data)


def test_baseline_state_round_trips(tmp_path: Path) -> None:
    """The baseline loads as the rich state and saves as the same bytes."""
    path = tmp_path / 'state.json'
    path.write_bytes(STATE.read_bytes())
    repository = JsonStateRepository(path=path, city=SYDNEY)
    loaded = repository.load()
    assert (loaded.saved, loaded.stored) == (RICH_SAVED, True)
    repository.save(saved=loaded.saved)
    assert path.read_bytes() == STATE.read_bytes()
