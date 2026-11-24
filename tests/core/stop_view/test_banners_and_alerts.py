"""Tests for stop view: banners and alerts."""

from dataclasses import replace
from datetime import timedelta
from typing import Final

from paperroute_device.core.banners import no_wifi_banner
from paperroute_device.core.text import DOT
from paperroute_sydney.city import SYDNEY, SydneyFeed, SydneyMode
from paperroute_testkit.core.factories import START, departure
from paperroute_transit.core.alert_model import AlertBanner
from paperroute_transit.core.banners import OFFLINE_PHRASE
from paperroute_transit.core.city import Nouns
from paperroute_transit.core.stop_view import (
    NOT_PINNED,
    ROWS_UNDER_BANNER,
    PinTag,
    nouns,
    pin_tag,
)
from tests.core.alert_factories import WESTERN, XPLORER
from tests.core.stop_view.helpers import live, many, view

ACTIVE: Final[AlertBanner] = AlertBanner(
    text=WESTERN.header, active=True, more=2,
)


COMING: Final[AlertBanner] = AlertBanner(
    text=f'From Sat 6:00{DOT}{XPLORER.header}', active=False, more=0,
)


NO_FEED: Final[str] = f'Timetable times{DOT}no live feed for this service'


NO_WIFI: Final[str] = no_wifi_banner(phrase=OFFLINE_PHRASE)


def test_device_warning_beats_alert() -> None:
    """A device warning keeps the banner, and no pill shows."""
    result = view(departures=(live(minutes=5),), notice=NO_WIFI, alert=ACTIVE)
    assert result.banner == NO_WIFI
    assert result.more == 0
    assert not result.planned


def test_empty_notice_lets_alert_through() -> None:
    """An empty notice counts as none, so the alert takes the banner."""
    result = view(departures=(live(minutes=5),), notice='', alert=ACTIVE)
    assert result.banner == WESTERN.header
    assert result.warning
    assert result.more == 2


def test_stale_line_beats_alert() -> None:
    """Live data three minutes old outranks an alert."""
    result = view(
        departures=(live(minutes=5),),
        age=timedelta(minutes=3),
        alert=ACTIVE,
    )
    assert result.banner is not None
    assert result.banner.startswith('No live data since')
    assert result.more == 0


def test_alert_beats_no_live_feed_line() -> None:
    """The permanent no-feed line gives way to an alert, but not to none."""
    result = view(
        departures=(live(minutes=5),), has_feed=False, alert=ACTIVE,
    )
    assert result.banner == WESTERN.header
    assert result.warning
    result = view(departures=(live(minutes=5),), has_feed=False)
    assert result.banner == NO_FEED


def test_no_live_data_yet_beats_alert() -> None:
    """With no live data fetched yet, that line outranks an alert."""
    result = view(departures=(live(minutes=5),), age=None, alert=ACTIVE)
    assert result.banner == f'Timetable times{DOT}no live data yet'


def test_active_alert_banner() -> None:
    """An active alert is a warning banner with its pill count."""
    result = view(departures=(live(minutes=5),), alert=ACTIVE)
    assert result.banner == WESTERN.header
    assert result.warning
    assert result.more == 2
    assert not result.planned


def test_coming_alert_outlined() -> None:
    """A coming alert is outlined, not a warning."""
    result = view(departures=(live(minutes=5),), alert=COMING)
    assert result.banner == COMING.text
    assert result.planned
    assert not result.warning


def test_alert_banner_shortens_list() -> None:
    """An alert banner takes a row's height."""
    result = view(departures=many(), alert=ACTIVE)
    assert len(result.rows) == ROWS_UNDER_BANNER


def test_pin_tag_auto() -> None:
    """An auto tag keeps its text; an unpinned place stays not pinned."""
    tag = pin_tag(index=1, total=3, nickname='Work', auto=True)
    assert tag == PinTag(text='Work 2/3', pinned=True, auto=True)
    plain = pin_tag(index=1, total=3, nickname='Work')
    assert plain.text == tag.text
    assert not plain.auto
    assert pin_tag(index=None, total=3, auto=True) == NOT_PINNED


def test_destination_pin_rows_show_chip() -> None:
    """Rows of a pin with a To each carry their own route chip."""
    routes = ('711', '606', '711')
    result = view(departures=tuple(
        replace(
            live(minutes=minutes),
            departure=departure(
                departs=START + timedelta(minutes=minutes), route=route,
            ),
        )
        for minutes, route in zip((5, 8, 12), routes, strict=True)
    ))
    assert result.hero is not None
    assert result.hero.route == '711'
    assert [row.route for row in result.rows] == ['606', '711']


def test_nouns_by_source_with_trainlink_override() -> None:
    """A TrainLink train reads train; other rows keep their source's words."""
    trainlink = departure(
        departs=START, mode=SydneyFeed.REGIONAL, kind=SydneyMode.TRAIN,
    )
    coach = replace(
        trainlink, facts=replace(trainlink.facts, kind=SydneyMode.COACH),
    )
    kindless = replace(trainlink, facts=replace(trainlink.facts, kind=None))
    bus = departure(departs=START, mode=SydneyFeed.BUS)
    ferry = departure(departs=START, mode=SydneyFeed.FERRY)
    unknown = replace(bus, source='hovercraft')
    regional = Nouns(vehicle='service', bay='platform', connection='train')
    assert nouns(departure=trainlink, city=SYDNEY) == Nouns(
        vehicle='train', bay='platform', connection='train',
    )
    assert nouns(departure=coach, city=SYDNEY) == regional
    assert nouns(departure=kindless, city=SYDNEY) == regional
    assert nouns(departure=bus, city=SYDNEY) == Nouns(
        vehicle='bus', bay='stand', connection='bus',
    )
    assert nouns(departure=ferry, city=SYDNEY) == Nouns(
        vehicle='ferry', bay='wharf', connection='ferry',
    )
    assert nouns(departure=unknown, city=SYDNEY) == Nouns(
        vehicle='service', bay='platform', connection='service',
    )
