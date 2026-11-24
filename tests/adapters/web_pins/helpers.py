"""Shared helpers for the web pins tests."""

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from paperroute_sydney.adapters.web_board import (
    SYDNEY_HINT,
    SYDNEY_NAV,
    SuggestPlug,
)
from paperroute_sydney.city import SYDNEY, SydneyMode
from paperroute_testkit.fake_pins import FakePinOptions
from paperroute_testkit.page_markup import Node, inside, parse_tree
from paperroute_transit.adapters.board_head import BoardHead
from paperroute_transit.adapters.board_pages import PinPages
from paperroute_transit.core.chooser import Choice
from paperroute_transit.core.model import Pin, StopPlace
from paperroute_transit.core.modes import end_point_modes
from paperroute_transit.core.pin_choices import StopMatch
from paperroute_transit.observers import PinsSeen
from tests.adapters.test_web_device import sydney_site
from tests.adapters.transit_web import transit_display

Client = TestClient[web.Request, web.Application]


def _pin(
    *,
    name: str,
    stop_id: str,
    to: str | None = None,
    routes: tuple[str, ...] = (),
    nickname: str | None = None,
    missing: bool = False,
) -> Pin:
    """Build a pin between two named places."""
    return Pin(
        origin=StopPlace(stop_id=stop_id, name=name, point=None),
        destination=(
            None
            if to is None
            else StopPlace(stop_id=f'{stop_id}-to', name=to, point=None)
        ),
        routes=routes,
        nickname=nickname,
        missing=missing,
    )


ENDS = end_point_modes(modes=SYDNEY.modes)


WORK = _pin(
    name='Hollis St', stop_id='a', to='Parramatta', routes=('606',),
    nickname='Work',
)


HOME = _pin(name='Home Stop', stop_id='b')


ODD = _pin(name='<b>&', stop_id='c', missing=True)


class Pins:
    """Pins the app has reported, and the events posted."""

    def __init__(self, *, pins: tuple[Pin, ...], shown: int | None = 0):
        self.seen: PinsSeen | None = PinsSeen(
            pins=pins, shown=shown, revision=3,
        )
        self.posted: list[object] = []
        self.slept: list[float] = []
        self.bump_after: float | None = None

    def get(self) -> PinsSeen | None:
        """Give the latest report."""
        return self.seen

    async def sleep(self, delay: float) -> None:
        """Pretend to wait, bumping the revision when told to."""
        self.slept.append(delay)
        if self.seen and self.bump_after is not None:
            if sum(self.slept) >= self.bump_after:
                self.seen = PinsSeen(
                    pins=(HOME,), shown=None,
                    revision=self.seen.revision + 1,
                )


async def _client(
    *, pins: Pins, options: FakePinOptions | None = None,
) -> Client:
    """Start a client of a display wired to the fake pins."""
    board = PinPages(
        head=BoardHead(nav=SYDNEY_NAV),
        pins=pins.get,
        options=options,
        modes=SYDNEY.modes,
        plug=SuggestPlug(planner=None),
    )
    board.pins_sleep = pins.sleep
    display = transit_display(
        device=sydney_site().pages(),
        board=board,
        nav=SYDNEY_NAV,
        hint=SYDNEY_HINT,
        city=SYDNEY,
    )
    display.bind(post=pins.posted.append)
    client = TestClient(TestServer(display.app()))
    await client.start_server()
    return client


CENTRAL = StopPlace(stop_id='C', name='Central', point=None)


JUNCTION = StopPlace(stop_id='J', name='Bondi Junction', point=None)


REDFERN = StopPlace(stop_id='R', name='Redfern', point=None)


BUS_STOP = StopPlace(stop_id='B2', name='Oxford St', point=None)


TOWN_HALL = StopPlace(stop_id='TH', name='Town Hall', point=None)


SAVED = Pin(
    origin=BUS_STOP,
    destination=TOWN_HALL,
    kind=SydneyMode.BUS,
    routes=('811',),
    nickname='Shops',
)


LOST = Pin(
    origin=StopPlace(stop_id='OLD', name='Old Stop', point=(1.0, 2.0)),
    kind=SydneyMode.BUS,
    routes=('809',),
    missing=True,
)


def _options() -> FakePinOptions:
    """C on T1/T2 and B2 on 809/811."""
    return FakePinOptions(
        places={place.stop_id: place for place in (
            CENTRAL, JUNCTION, REDFERN, BUS_STOP, TOWN_HALL,
        )},
        lines={
            ('C', SydneyMode.METRO): (
                Choice(key='T1', label='North Shore', badge='T1'),
                Choice(key='T2', label='Leppington', badge='T2'),
            ),
            ('B2', SydneyMode.BUS): (
                Choice(key='809', label='809', badge='809'),
                Choice(key='811', label='811', badge='811'),
            ),
        },
        to={
            ('C', 'T1'): (JUNCTION, REDFERN),
            ('B2', '811'): (TOWN_HALL,),
        },
        only={
            ('C', SydneyMode.METRO): ('T1', 'T2'),
            ('B2', SydneyMode.BUS): ('809', '811'),
        },
        near=(BUS_STOP,),
        found=(
            StopMatch(
                place=CENTRAL, kinds=(SydneyMode.METRO,), routes=('T1', 'T2'),
            ),
        ),
    )


FULL: dict[str, str | list[str]] = {
    'stop': 'C', 'kind': 'metro', 'route': 'T1', 'to': 'J',
    'only': ['T2'], 'nickname': 'Home',
}


def _form(fields: dict[str, str | list[str]]) -> list[tuple[str, str]]:
    """Flatten form fields, repeating list values."""
    return [
        (name, item)
        for name, value in fields.items()
        for item in ([value] if isinstance(value, str) else value)
    ]


def delete_button(*, held: list[Node], ref: str) -> Node:
    """
    Check a delete form's ref field and danger button, and give the button.

    Parameters
    ----------
    held : list[Node]
        The nodes inside the delete form's card.
    ref : str
        The ref the form must carry.

    Returns
    -------
    Node
        The Delete pin button.
    """
    field = next(node for node in held if node.attrs.get('name') == 'ref')
    assert field.attrs['value'] == ref
    button = next(node for node in held if node.tag == 'button')
    assert button.text.strip() == 'Delete pin'
    assert button.attrs['class'] == 'danger'
    return button


def check_danger_card(*, text: str, ref: str, label: str) -> None:
    """
    Check a page puts Delete in its own card after the editor's.

    Parameters
    ----------
    text : str
        The page.
    ref : str
        The pin's ref.
    label : str
        The pin's name.
    """
    nodes = parse_tree(text=text)
    editor_card = next(
        node for node in nodes
        if node.attrs.get('aria-labelledby') == 'pin-edit-heading'
    )
    card = next(
        node for node in nodes
        if node.tag == 'section'
        and 'danger-zone' in (node.attrs.get('class') or '').split()
    )
    assert card.index > max(inside(nodes=nodes, outer=editor_card)).index
    assert editor_card.index not in card.parents
    held = inside(nodes=nodes, outer=card)
    assert [node.text for node in held if node.tag == 'h2'] == [
        'Delete this pin',
    ]
    assert [node.text for node in held if node.tag == 'p'] == [
        "It disappears from the panel and this list. This can't be undone.",
    ]
    form = next(node for node in held if node.tag == 'form')
    assert form.attrs['action'] == '/pins/delete'
    button = delete_button(held=held, ref=ref)
    assert button.attrs['data-label'] == label
