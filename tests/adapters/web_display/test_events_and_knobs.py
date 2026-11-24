"""Tests for web display: events and knobs."""

import asyncio
from http import HTTPStatus

import pytest

from paperroute_device.adapters import web_live
from paperroute_device.adapters.web_display import WebDisplay
from paperroute_device.adapters.web_live import knob_event
from paperroute_device.render.canvas import encode_png
from paperroute_hardware.core.knob_events import (
    Knob,
    KnobHeld,
    KnobPressed,
    KnobsHeld,
    KnobTurned,
)
from paperroute_transit.core.messages import Event
from tests.adapters.web_display.helpers import FRAME, Client, _read_event


@pytest.mark.parametrize(('knob', 'action', 'expected'), [
    ('top', 'left', KnobTurned(knob=Knob.TOP, steps=-1)),
    ('top', 'right', KnobTurned(knob=Knob.TOP, steps=1)),
    ('top', 'press', KnobPressed(knob=Knob.TOP)),
    ('bottom', 'left', KnobTurned(knob=Knob.BOTTOM, steps=-1)),
    ('bottom', 'right', KnobTurned(knob=Knob.BOTTOM, steps=1)),
    ('bottom', 'press', KnobPressed(knob=Knob.BOTTOM)),
    ('bottom', 'hold', KnobHeld(knob=Knob.BOTTOM)),
    ('both', 'hold', KnobsHeld()),
    ('both', 'press', None),
    ('middle', 'left', None),
    ('top', 'spin', None),
])
def test_knob_event_mapping(
    knob: str, action: str, expected: Event | None
) -> None:
    """Valid knob and action pairs map to events, others to None."""
    assert knob_event(knob=knob, action=action) == expected


async def test_frame_png_503_before_first_frame(
    display: WebDisplay, client: Client
) -> None:
    """There is no frame until the first show."""
    response = await client.get('/frame.png')
    assert response.status == HTTPStatus.SERVICE_UNAVAILABLE
    display.show(frame=FRAME)
    response = await client.get('/frame.png')
    assert response.status == HTTPStatus.OK
    assert response.content_type == 'image/png'
    assert response.headers['Cache-Control'] == 'no-store'
    assert (await response.read()).startswith(b'\x89PNG')


async def test_web_copy_stays_upright(
    display: WebDisplay, client: Client
) -> None:
    """A turned frame is served as drawn, the right way up."""
    frame = FRAME.copy()
    frame[0, 0] = 0
    display.show(frame=frame, full=True, turned=True)
    response = await client.get('/frame.png')
    assert await response.read() == encode_png(grey=frame)


async def test_events_sends_current_frame_version_on_connect(
    display: WebDisplay, client: Client
) -> None:
    """A new stream starts with the version already shown."""
    display.show(frame=FRAME)
    response = await client.get('/events')
    assert response.content_type == 'text/event-stream'
    assert response.headers['Cache-Control'] == 'no-store'
    assert await _read_event(
        response=response,
    ) == ('frame', f'{display.live.boot}-1')


async def test_events_pushes_new_versions(
    display: WebDisplay, client: Client
) -> None:
    """Each new frame is pushed to an open stream."""
    display.show(frame=FRAME)
    response = await client.get('/events')
    assert await _read_event(
        response=response,
    ) == ('frame', f'{display.live.boot}-1')
    display.show(frame=FRAME)
    assert await _read_event(
        response=response,
    ) == ('frame', f'{display.live.boot}-2')
    display.show(frame=FRAME)
    assert await _read_event(
        response=response,
    ) == ('frame', f'{display.live.boot}-3')


async def test_events_pings_when_idle(
    display: WebDisplay,
    client: Client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A quiet stream gets a comment line to keep it open."""
    monkeypatch.setattr(web_live, 'PING_S', 0.05)
    response = await client.get('/events')
    async with asyncio.timeout(2):
        line = await response.content.readline()
    assert line.decode().strip() == ': ping'
    assert display.live.version == 0


async def test_knob_post_posts_event(
    posted: list[object], client: Client
) -> None:
    """A knob POST becomes an event on the bound queue."""
    response = await client.post(
        '/knob',
        data={'knob': 'bottom', 'action': 'right'},
    )
    assert response.status == HTTPStatus.NO_CONTENT
    response = await client.post(
        '/knob',
        json={'knob': 'top', 'action': 'press'},
    )
    assert response.status == HTTPStatus.NO_CONTENT
    assert posted == [
        KnobTurned(knob=Knob.BOTTOM, steps=1),
        KnobPressed(knob=Knob.TOP),
    ]


async def test_knob_both_hold_posts_knobs_held(
    posted: list[object], client: Client
) -> None:
    """Both-knob hold posts KnobsHeld; any other both action is a 400."""
    response = await client.post(
        '/knob', data={'knob': 'both', 'action': 'hold'},
    )
    assert response.status == HTTPStatus.NO_CONTENT
    response = await client.post(
        '/knob', data={'knob': 'both', 'action': 'press'},
    )
    assert response.status == HTTPStatus.BAD_REQUEST
    assert posted == [KnobsHeld()]


@pytest.mark.parametrize('payload', [
    {'knob': 'middle', 'action': 'left'},
    {'knob': 'top'},
])
async def test_knob_post_rejects_unknown(
    client: Client, payload: dict[str, str]
) -> None:
    """Unknown values are a 400."""
    response = await client.post('/knob', data=payload)
    assert response.status == HTTPStatus.BAD_REQUEST


async def test_knob_post_rejects_bad_json(client: Client) -> None:
    """A JSON body that is not an object is a 400."""
    response = await client.post('/knob', json=['top', 'left'])
    assert response.status == HTTPStatus.BAD_REQUEST
