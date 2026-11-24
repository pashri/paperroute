"""The golden scene registry."""

from collections.abc import Callable
from dataclasses import replace
from datetime import timedelta
from typing import Final

from paperroute_device.core.device_model import (
    Confirm,
    ConfirmAction,
    Menu,
    Origin,
    PanelScreen,
    WifiPhase,
    WifiSetup,
)
from paperroute_testkit.core.factories import START
from paperroute_transit.core.saved import LiveView, MapFraming, StopSides
from paperroute_transit.core.state_model import State
from tests.core.chooser_walks import BUSY_PAIR, at_kerbs
from tests.core.test_alerts_view import on_alerts
from tests.golden.scenes.gauge import stop_gauge, stop_gauge_busy
from tests.golden.scenes.maps import (
    destination_scene,
    line_scene,
    map_framed,
    map_panes,
    map_scene,
)
from tests.golden.scenes.screens import (
    asleep,
    building,
    chooser,
    chooser_routes,
    notice,
    panel,
)
from tests.golden.scenes.stops import (
    both_sides_live,
    both_sides_scene,
    estimated,
    stop,
    stop_cambridge_trains,
    stop_larger_inverted,
    stop_no_destination,
    stop_no_key,
    stop_trainlink,
)
from tests.golden.scenes.strips import (
    EASY_LATES,
    LOOP_441,
    MISSED_LATES,
    SPUR_18,
    SPUR_506,
    TIGHT_LATES,
    connection,
    connection_strip,
    detour_scene,
    late_connection,
    strip_scene,
    train_strip_scene,
)

SCENES: Final[dict[str, Callable[[], State]]] = {
    'stop': stop,
    'stop_larger_inverted': stop_larger_inverted,
    'stop_no_key': stop_no_key,
    'stop_trainlink': stop_trainlink,
    'stop_cambridge_trains': stop_cambridge_trains,
    'stop_no_destination': stop_no_destination,
    'stop_both_sides': lambda: both_sides_scene(sides=StopSides.TIME_ORDER),
    'stop_each_way': lambda: both_sides_scene(sides=StopSides.EACH_WAY),
    'stop_gauge': stop_gauge,
    'stop_gauge_busy': stop_gauge_busy,
    'stop_estimates': lambda: estimated(
        state=stop(), lates={0: (-2, False), 1: (2, False)},
    ),
    'stop_uncertain': lambda: estimated(
        state=stop(), lates={0: (11, True), 1: (12, True), 2: (10, False)},
    ),
    'map': map_scene,
    'map_panes': map_panes,
    'map_lines': line_scene,
    'map_destination': destination_scene,
    'map_approach': lambda: map_framed(framing=MapFraming.APPROACH),
    'map_centred': lambda: map_framed(framing=MapFraming.CENTRED),
    'map_both_sides': lambda: both_sides_live(view=LiveView.MAP),
    'strip': lambda: strip_scene(view=LiveView.STRIP),
    'strip_map': lambda: strip_scene(view=LiveView.STRIP_MAP),
    'strip_both_sides': lambda: both_sides_live(view=LiveView.STRIP),
    'strip_map_both_sides': lambda: both_sides_live(
        view=LiveView.STRIP_MAP,
    ),
    'strip_loop': lambda: detour_scene(
        view=LiveView.STRIP, trip=LOOP_441, origin=3, route='441',
    ),
    'strip_loop_from': lambda: detour_scene(
        view=LiveView.STRIP, trip=LOOP_441, origin=6, route='441', lead=0,
    ),
    'strip_map_loop_from': lambda: detour_scene(
        view=LiveView.STRIP_MAP, trip=LOOP_441[3:], origin=3, route='441',
        lead=0,
    ),
    'strip_loop_bus': lambda: detour_scene(
        view=LiveView.STRIP, trip=LOOP_441, origin=3, route='441', lead=-4,
    ),
    'strip_map_loop_bus': lambda: detour_scene(
        view=LiveView.STRIP_MAP, trip=LOOP_441[3:], origin=0, route='441',
        lead=-4,
    ),
    'strip_spur_bus': lambda: detour_scene(
        view=LiveView.STRIP, trip=SPUR_18, origin=3, route='18', lead=-15,
    ),
    'strip_map_loop': lambda: detour_scene(
        view=LiveView.STRIP_MAP, trip=LOOP_441[3:], origin=0, route='441',
    ),
    'strip_spur': lambda: detour_scene(
        view=LiveView.STRIP, trip=SPUR_18, origin=3, route='18',
    ),
    'strip_map_spur': lambda: detour_scene(
        view=LiveView.STRIP_MAP, trip=SPUR_18[3:], origin=0, route='18',
    ),
    'strip_spur_506': lambda: detour_scene(
        view=LiveView.STRIP, trip=SPUR_506, origin=2, route='506',
    ),
    'train_strip': lambda: train_strip_scene(destination='Petersham'),
    'train_strip_ends': lambda: train_strip_scene(destination='Lidcombe'),
    'alerts': on_alerts,
    'connection': lambda: connection(view=LiveView.STOP),
    'connection_strip': lambda: connection_strip(view=LiveView.STRIP),
    'connection_strip_map': lambda: connection_strip(
        view=LiveView.STRIP_MAP,
    ),
    'connection_easy': lambda: late_connection(
        view=LiveView.STOP, lates=EASY_LATES,
    ),
    'connection_tight': lambda: late_connection(
        view=LiveView.STOP, lates=TIGHT_LATES,
    ),
    'connection_missed': lambda: late_connection(
        view=LiveView.STOP, lates=MISSED_LATES,
    ),
    'connection_strip_tight': lambda: late_connection(
        view=LiveView.STRIP, lates=TIGHT_LATES,
    ),
    'chooser': chooser,
    'chooser_routes': chooser_routes,
    'chooser_kerbs': lambda: replace(stop(), chooser=at_kerbs()),
    'chooser_kerbs_menu': lambda: replace(
        stop(), chooser=at_kerbs(pairs=(BUSY_PAIR,), menu=0),
    ),
    'building': building,
    'notice': notice,
    'menu': lambda: panel(menu=Menu()),
    'device': lambda: panel(menu=Menu(screen=PanelScreen.DEVICE)),
    'phone': lambda: panel(menu=Menu(screen=PanelScreen.PHONE)),
    'wifi_setup': lambda: panel(wifi=WifiSetup(
        phase=WifiPhase.HOTSPOT,
        password='7Q4K9W2M',
        until=START + timedelta(minutes=10),
    )),
    'confirm': lambda: panel(confirm=Confirm(
        action=ConfirmAction.SHUT_DOWN, origin=Origin.PANEL, asked_at=START,
    )),
    'sleep': asleep,
}
