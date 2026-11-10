"""Draw the Device page's Data section: timetable, tiles, calls, feeds."""

import html
from collections.abc import Callable, Mapping
from datetime import datetime, tzinfo
from typing import Final

from paperroute_device.adapters.device_page import ago_text, row
from paperroute_device.core.device_model import DeviceFacts
from paperroute_device.core.panel_views import UNKNOWN
from paperroute_device.core.text import DOT, clock_text, day_text, local_time
from paperroute_transit.core.panel_screens import TilesFacts

DATA_SECTION: Final[str] = (
    '<section class="card setting" aria-labelledby="data">\n'
    '      <h2 id="data">Data</h2>\n'
    '      <dl class="facts">\n'
    '{rows}\n'
    '      </dl>\n'
    '      <div class="table">\n'
    '        <table>\n'
    '          <caption>Feeds</caption>\n'
    '          <thead>\n'
    '            <tr><th scope="col">Feed</th>'
    '<th scope="col">Last ok</th><th scope="col">Age</th></tr>\n'
    '          </thead>\n'
    '          <tbody>\n'
    '{feeds}\n'
    '          </tbody>\n'
    '        </table>\n'
    '      </div>\n'
    '    </section>'
)
FEED_ROW: Final[str] = (
    '            <tr><th scope="row">{label}</th>'
    '<td>{last}</td><td>{age}</td></tr>'
)
NO_FEEDS: Final[str] = '            <tr><td colspan="3">none yet</td></tr>'


def text_of(*, status: Mapping[str, object], name: str) -> str | None:
    """
    Read a text reading from the app's status.

    Parameters
    ----------
    status : Mapping[str, object]
        The app's status.
    name : str
        The reading's name.

    Returns
    -------
    str | None
        The reading when it is text, else None.
    """
    value = status.get(name)
    return value if isinstance(value, str) else None


def timetable_text(*, status: Mapping[str, object], zone: tzinfo) -> str:
    """
    Describe the timetable in use.

    Parameters
    ----------
    status : Mapping[str, object]
        The app's status: `timetable`, `schema`, `rebuilding` and
        `rebuild_failed`.
    zone : tzinfo
        Time zone whose wall clock to show.

    Returns
    -------
    str
        `rebuilding` while a rebuild runs; else its version and build
        time, then `ok` or `last build failed`; with no timetable,
        `none yet` or `last build failed`.
    """
    failed = 'last build failed'
    if status.get('rebuilding'):
        return 'rebuilding'
    bad = bool(status.get('rebuild_failed'))
    stamp = text_of(status=status, name='timetable')
    built = local_time(stamp=stamp, zone=zone)
    if built is None:
        return failed if bad else 'none yet'
    schema = text_of(status=status, name='schema')
    parts = [] if schema is None else [f'version {schema}']
    clock = clock_text(moment=built, zone=zone)
    parts.append(f'built {day_text(moment=built)} {clock}')
    parts.append(failed if bad else 'ok')
    return DOT.join(parts)


def tiles_text(*, facts: DeviceFacts | None, zone: tzinfo) -> str:
    """
    Say when the map tiles were built.

    Parameters
    ----------
    facts : DeviceFacts | None
        The latest probe, or None.
    zone : tzinfo
        Time zone whose calendar day to show.

    Returns
    -------
    str
        `built 19 Oct`, `none`, or a dash before the first probe.
    """
    if facts is None:
        return UNKNOWN
    app = facts.app
    stamp = app.built if isinstance(app, TilesFacts) else None
    built = local_time(stamp=stamp, zone=zone)
    return 'none' if built is None else f'built {day_text(moment=built)}'


def calls_text(*, status: Mapping[str, object], daily_limit: int) -> str:
    """
    Count the API calls made today against the limit.

    Parameters
    ----------
    status : Mapping[str, object]
        The app's status, holding `calls_today`.
    daily_limit : int
        The API calls allowed each day.

    Returns
    -------
    str
        `8,412 of 60,000`.
    """
    calls = status.get('calls_today')
    count = calls if isinstance(calls, int) else 0
    return f'{count:,} of {daily_limit:,}'


def data_rows(
    *,
    status: Mapping[str, object] | None,
    facts: DeviceFacts | None,
    daily_limit: int,
    zone: tzinfo,
) -> str:
    """
    Draw the Data section's facts.

    Parameters
    ----------
    status : Mapping[str, object] | None
        The app's status, or None before the app reports it.
    facts : DeviceFacts | None
        The latest probe, or None.
    daily_limit : int
        The API calls allowed each day.
    zone : tzinfo
        Time zone whose wall clock to show.

    Returns
    -------
    str
        Timetable, map tiles and the API calls made today.
    """
    if status is None:
        timetable, calls = UNKNOWN, UNKNOWN
    else:
        timetable = timetable_text(status=status, zone=zone)
        calls = calls_text(status=status, daily_limit=daily_limit)
    return '\n'.join((
        row(label='Timetable', value=timetable),
        row(label='Map tiles', value=tiles_text(facts=facts, zone=zone)),
        row(label='API calls today', value=calls),
    ))


def feed_rows(
    *,
    feeds: Mapping[str, float],
    now: float,
    label: Callable[[str], str],
    zone: tzinfo,
) -> str:
    """
    Draw the feed table's body.

    Parameters
    ----------
    feeds : Mapping[str, float]
        Wall-clock time each feed last worked.
    now : float
        The wall-clock time now.
    label : Callable[[str], str]
        Names a feed for people.
    zone : tzinfo
        Time zone whose wall clock to show.

    Returns
    -------
    str
        One row per feed, by name, with its time in ``zone`` and its
        age; one `none yet` row with no feeds.
    """
    rows = sorted((label(feed), last) for feed, last in feeds.items())
    if not rows:
        return NO_FEEDS
    return '\n'.join(
        FEED_ROW.format(
            label=html.escape(name),
            last=f'{datetime.fromtimestamp(last, tz=zone):%H:%M:%S}',
            age=ago_text(seconds=now - last),
        )
        for name, last in rows
    )


def data_section(
    *,
    status: Mapping[str, object] | None,
    facts: DeviceFacts | None,
    feeds: Mapping[str, float],
    now: float,
    label: Callable[[str], str],
    daily_limit: int,
    zone: tzinfo,
) -> str:
    """
    Draw the Device page's Data section.

    Parameters
    ----------
    status : Mapping[str, object] | None
        The app's status, or None before the app reports it.
    facts : DeviceFacts | None
        The latest probe, or None.
    feeds : Mapping[str, float]
        Wall-clock time each feed last worked.
    now : float
        The wall-clock time now.
    label : Callable[[str], str]
        Names a feed for people.
    daily_limit : int
        The API calls allowed each day.
    zone : tzinfo
        Time zone whose wall clock to show.

    Returns
    -------
    str
        The whole section, from its opening tag to its closing one.
        Every value written into it is escaped.
    """
    return DATA_SECTION.format(
        rows=data_rows(
            status=status, facts=facts, daily_limit=daily_limit, zone=zone,
        ),
        feeds=feed_rows(feeds=feeds, now=now, label=label, zone=zone),
    )
