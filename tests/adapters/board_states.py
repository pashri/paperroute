"""The kit's state file holding a board's state, for tests."""

from dataclasses import dataclass, replace
from pathlib import Path

from paperroute_device.adapters.migrations import (
    KIT_MIGRATIONS,
    chain,
    latest_version,
    migrate,
)
from paperroute_device.adapters.state_file import StateFile
from paperroute_device.core.device_state import KitSaved
from paperroute_testkit.core.kit_steps import QUIET_PARTS
from paperroute_transit.adapters.state_migrations import transit_migrations
from paperroute_transit.core.city import City
from paperroute_transit.core.saved import Saved, default_saved
from paperroute_transit.transit_app import TransitApp


def schema_of(*, city: City) -> int:
    """
    Give the schema version the kit writes a city's file at.

    Parameters
    ----------
    city : City
        The city.

    Returns
    -------
    int
        The version.
    """
    return latest_version(
        chain=chain(kit=KIT_MIGRATIONS, app=transit_migrations(city=city)),
    )


def migrated(*, data: dict[str, object], city: City) -> dict[str, object]:
    """
    Bring a decoded file up to the latest version through the kit's chain.

    Parameters
    ----------
    data : dict[str, object]
        The decoded file.
    city : City
        The city, whose defaults new settings take.

    Returns
    -------
    dict[str, object]
        The data at the latest version, its sections not yet repaired.

    Raises
    ------
    ValueError
        When the version is missing, bad or too new.
    """
    return migrate(
        data=data,
        chain=chain(kit=KIT_MIGRATIONS, app=transit_migrations(city=city)),
        latest=schema_of(city=city),
    )


@dataclass(frozen=True)
class BoardLoaded:
    """
    A board's state as the kit loaded it.

    Attributes
    ----------
    saved : Saved
        The board's state with the device settings in it; the city's
        defaults when the file held no usable board section.
    stored : bool
        Whether it came from the file.
    """

    saved: Saved
    stored: bool


class BoardStates:
    """
    The kit's state file opened with a city's board.

    Parameters
    ----------
    path : Path
        The file.
    city : City
        The city.
    """

    def __init__(self, *, path: Path, city: City) -> None:
        self.city = city
        self.file = StateFile(
            path=path, app=TransitApp(city=city, parts=QUIET_PARTS),
        )

    def load(self) -> BoardLoaded:
        """
        Load the file as the kit's run does and join its two sections.

        Returns
        -------
        BoardLoaded
            The board's state with the device settings in it.
        """
        loaded = self.file.load()
        board = (
            loaded.app
            if isinstance(loaded.app, Saved)
            else default_saved(city=self.city)
        )
        saved = replace(
            board, settings=replace(board.settings, device=loaded.device),
        )
        return BoardLoaded(saved=saved, stored=loaded.stored)

    def save(self, *, saved: Saved) -> None:
        """
        Write a board's state as the kit does.

        Parameters
        ----------
        saved : Saved
            The board's state, device settings included.
        """
        self.file.save(saved=KitSaved(device=saved.settings.device, app=saved))
