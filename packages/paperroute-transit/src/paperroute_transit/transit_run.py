"""The transit board's run on the device kit: what it opens and serves."""

import argparse
import logging
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import replace
from datetime import tzinfo
from pathlib import Path
from typing import TYPE_CHECKING, cast

from paperroute_device.core.app import MigrationStep, SectionCodec
from paperroute_device.core.device_model import DeviceKey
from paperroute_device.logs import LastError
from paperroute_device.ports import Clock
from paperroute_device.runtime.app_web import AppWeb
from paperroute_device.runtime.timings import KitTimings
from paperroute_transit.adapters.board_head import BoardHead
from paperroute_transit.adapters.board_pages import PinPages
from paperroute_transit.adapters.board_routes import BoardRoutes
from paperroute_transit.adapters.board_section import BoardCodec
from paperroute_transit.adapters.board_settings import BoardSettingsPlug
from paperroute_transit.adapters.device_data import data_section
from paperroute_transit.adapters.leg_cache import LegCache
from paperroute_transit.adapters.network_db import SqliteNetworkRepository
from paperroute_transit.adapters.pin_options import SqlitePinOptions
from paperroute_transit.adapters.state_migrations import transit_migrations
from paperroute_transit.adapters.tiles_db import NullTiles, tiles_facts
from paperroute_transit.app_effects import VersionWatch
from paperroute_transit.app_pins import BoardObserver
from paperroute_transit.app_ports import Ports
from paperroute_transit.app_render import MapRenderer
from paperroute_transit.app_work import perform
from paperroute_transit.core.city import City
from paperroute_transit.core.model import Pin
from paperroute_transit.core.saved import default_settings
from paperroute_transit.core.tiles import TileVariant
from paperroute_transit.observers import NullObserver, ObserverGroup, Timings
from paperroute_transit.ports import Observer
from paperroute_transit.summary import Summary

if TYPE_CHECKING:
    from paperroute_transit.city_parts import CityParts

logger = logging.getLogger(__name__)


class TransitRun:
    """
    The board's run hooks: the adapters it opens and the pages it serves.

    The run's hooks open the timetable, map tiles and feeds, and
    `attach` hands them to the hooks that need them.

    Parameters
    ----------
    city : City
        The city the board runs for.
    parts : CityParts
        The city's runtime wiring; the reducer hooks never read it.
    observer : BoardObserver | None
        Told of settings and pin changes; None tells no one until
        `timings` makes the board's observers.
    watch : VersionWatch | None
        Reads the timetable's version before each tick; None reads
        nothing until `attach` watches the timetable given.

    Attributes
    ----------
    summary : Summary
        Counts frames and the feeds' requests for the periodic log.
    ports : Ports | None
        Transit's adapters once attached, or None.
    """

    def __init__(
        self,
        *,
        city: City,
        parts: CityParts,
        observer: BoardObserver | None = None,
        watch: VersionWatch | None = None,
    ) -> None:
        self.city = city
        self.parts = parts
        self.observer = observer
        self.watch = watch
        self.summary = Summary()
        self.ports: Ports | None = None
        self.board_timings: Timings | None = None
        self.feeds_observer: Observer = NullObserver()
        self.seed: Pin | None = None
        self.maps = MapRenderer(tiles=NullTiles())
        self.replay = False

    def attach(
        self,
        *,
        ports: Ports,
        seed: Pin | None = None,
        variant: TileVariant = TileVariant.GREY,
    ) -> None:
        """
        Hand the board its open adapters.

        Parameters
        ----------
        ports : Ports
            Transit's adapters.
        seed : Pin | None
            Pin to start with when there is no saved state.
        variant : TileVariant
            Which tile rendering the Map draws.
        """
        self.ports = ports
        self.seed = seed
        self.maps = MapRenderer(tiles=ports.tiles, variant=variant)
        if self.watch is None:
            self.watch = VersionWatch(timetable=ports.timetable)

    def attached(self) -> Ports:
        """
        Give transit's adapters.

        Returns
        -------
        Ports
            The adapters `attach` was given.

        Raises
        ------
        RuntimeError
            Before `attach`.
        """
        if self.ports is None:
            raise RuntimeError('the board has no adapters yet')
        return self.ports

    def section(self) -> SectionCodec:
        """
        Give the codec of the board's section of the state file.

        Returns
        -------
        SectionCodec
            The board's codec for the city.
        """
        return BoardCodec(city=self.city)

    def migrations(self) -> Mapping[int, MigrationStep]:
        """
        Give transit's steps in the state file's migration chain.

        Returns
        -------
        Mapping[int, MigrationStep]
            Steps 1 to 12, each bound to the city.
        """
        return transit_migrations(city=self.city)

    def zone(self) -> tzinfo:
        """
        Give the city's time zone.

        Returns
        -------
        tzinfo
            The zone.
        """
        return self.city.zone

    def keys(self) -> tuple[DeviceKey, ...]:
        """
        Give the city's API keys, each saying when a feed last worked.

        Returns
        -------
        tuple[DeviceKey, ...]
            The keys, in declaration order.
        """
        keys = tuple(self.parts.keys())
        timings = self.board_timings
        if timings is None:
            return keys
        return tuple(replace(key, last_used=timings.last_used) for key in keys)

    def erased(self, *, args: argparse.Namespace) -> tuple[Path, ...]:
        """
        Name the timetable, which Erase everything deletes.

        Parameters
        ----------
        args : argparse.Namespace
            The run command's arguments.

        Returns
        -------
        tuple[Path, ...]
            The timetable file.
        """
        return (args.timetable,)

    def add_arguments(self, *, parser: argparse.ArgumentParser) -> None:
        """
        Add the board's flags to the run command.

        Parameters
        ----------
        parser : argparse.ArgumentParser
            The run command's parser.
        """
        # pylint: disable-next=import-outside-toplevel,cyclic-import
        from paperroute_transit.board import add_run_arguments

        add_run_arguments(parser)

    def check_arguments(self, *, args: argparse.Namespace) -> None:
        """
        Refuse a lone --from or --to before anything opens.

        Parameters
        ----------
        args : argparse.Namespace
            The run command's parsed arguments.
        """
        # pylint: disable-next=import-outside-toplevel,cyclic-import
        from paperroute_transit.board import require_both_or_neither

        require_both_or_neither(args=args)

    @contextmanager
    def opened(
        self, *, args: argparse.Namespace, last_error: LastError,
    ) -> Iterator[None]:
        """
        Open the timetable, map tiles and feeds for the run.

        Parameters
        ----------
        args : argparse.Namespace
            The run command's arguments.
        last_error : LastError
            Unused: the kit's probe reads it.

        Yields
        ------
        None
            While the adapters are open; the timetable and tiles close
            after.
        """
        # pylint: disable-next=import-outside-toplevel,cyclic-import
        from paperroute_transit import board

        del last_error
        self.replay = args.feeds != 'live'
        feeds = board.feeds_client(
            choice=args.feeds,
            live=self.parts.live_feeds,
            summary=self.summary,
        )
        with (
            board.timetable_for(args=args, city=self.city) as timetable,
            board.network_for(args=args, city=self.city) as network,
            board.tiles_for(args=args, city=self.city) as tiles,
        ):
            ports = Ports(
                timetable=timetable,
                feeds=feeds,
                clock=board.clock_for(args=args, city=self.city),
                network=network,
                observer=self.feeds_observer,
                tiles=tiles,
                builder=board.builder_for(args=args),
                legs=LegCache(zone=self.city.zone),
            )
            self.attach(
                ports=ports,
                seed=board.seed_for(args=args),
                variant=args.tiles_variant,
            )
            yield

    def clock(self) -> Clock | None:
        """
        Give the board's clock: a replay's, or the system's.

        Returns
        -------
        Clock | None
            The attached clock, or None before `attach`.
        """
        return None if self.ports is None else self.ports.clock

    def replaying(self) -> bool:
        """
        Say whether the feeds are replayed from recordings.

        Returns
        -------
        bool
            True when the run was given a recordings directory.
        """
        return self.replay

    def timings(self) -> Timings:
        """
        Make the board's timings and the observers that tell them.

        Returns
        -------
        Timings
            The one timings object, which the kit and the board both
            report to.
        """
        timings = Timings()
        group = ObserverGroup(observers=[timings, self.summary])
        self.board_timings = timings
        self.observer = group
        self.feeds_observer = group
        return timings

    def observers(self) -> tuple[Observer, ...]:
        """
        Give the board's own observer beside the timings.

        Returns
        -------
        tuple[Observer, ...]
            The summary.
        """
        return (self.summary,)

    def web(self, *, args: argparse.Namespace, timings: KitTimings) -> AppWeb:
        """
        Give the board's nav, pages, Settings sections and Data section.

        Parameters
        ----------
        args : argparse.Namespace
            The run command's arguments.
        timings : KitTimings
            The board's timings, from `timings`.

        Returns
        -------
        AppWeb
            The board's part of the web pages.
        """
        board = cast(Timings, timings)
        plug = BoardSettingsPlug(defaults=default_settings(city=self.city))
        network = self.attached().network
        options = (
            SqlitePinOptions(network=network)
            if isinstance(network, SqliteNetworkRepository)
            else None
        )
        pins = PinPages(
            pins=lambda: board.pins,
            options=options,
            modes=self.city.modes,
            plug=self.parts.suggest(live=args.feeds == 'live'),
            head=BoardHead(sheets=self.parts.sheets, nav=self.parts.nav),
        )
        return AppWeb(
            nav=self.parts.nav,
            hint=self.parts.hint,
            sheets=self.parts.sheets,
            status_line=self.parts.status_line,
            routes=BoardRoutes(
                modes=self.city.modes,
                pins=pins,
                reported=lambda: plug.values(status=board.snapshot()),
            ),
            settings=plug,
            data=self.data_writer(timings=board),
        )

    def data_writer(self, *, timings: Timings) -> Callable[[], str]:
        """
        Make what writes the Device page's Data section.

        Parameters
        ----------
        timings : Timings
            The board's timings.

        Returns
        -------
        Callable[[], str]
            Writes the Timetable, Map tiles, API calls and Feeds rows.
        """
        feeds = self.city.feeds

        def written() -> str:
            """
            Write the section.

            Returns
            -------
            str
                The section's markup.
            """
            device = timings.device
            return data_section(
                status=timings.app or None,
                facts=None if device is None else device.facts,
                feeds=timings.last_ok,
                now=time.time(),
                label=lambda feed: feeds.label(feed=feed),
                daily_limit=feeds.daily_limit,
                zone=self.city.zone,
            )

        return written

    async def perform(
        self, *, effect: object, post: Callable[[object], None],
    ) -> None:
        """
        Carry out one of transit's effects, in the background.

        Parameters
        ----------
        effect : object
            The effect.
        post : Callable[[object], None]
            Posts an event to the kit's queue.
        """
        await perform(ports=self.attached(), effect=effect, post=post)

    async def probe(self) -> object:
        """
        Find out when the map tiles were built.

        Returns
        -------
        object
            The TilesFacts.
        """
        return await tiles_facts(tiles=self.attached().tiles)

    async def before_reset(self) -> None:
        """Stop any timetable rebuild before a reset deletes a file."""
        await self.attached().builder.stop()

    async def stopped(self) -> None:
        """Stop any timetable rebuild and close the feeds client."""
        if self.ports is None:
            return
        await self.ports.builder.stop()
        await self.ports.feeds.close()
