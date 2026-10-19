"""Hand-written stand-ins for the network and state repositories."""

import sqlite3
from dataclasses import dataclass, field

from paperroute.core.chooser import ChooserPath, Column
from paperroute.core.model import StopPlace
from paperroute.core.reach import ReachInput, StripKey
from paperroute.core.saved import DEFAULT_SAVED, Saved
from paperroute.ports import Loaded


@dataclass
class FakeNetwork:
    """
    A network that gives fixed answers, or fails every read.

    Attributes
    ----------
    column : Column | None
        The column every path leads to.
    line : tuple[StopPlace, ...] | None
        The stations of every towards row.
    data : ReachInput | None
        The runs through every strip's From.
    error : sqlite3.Error | None
        Raised by every read when set.
    """

    column: Column | None = None
    line: tuple[StopPlace, ...] | None = None
    data: ReachInput | None = None
    error: sqlite3.Error | None = None

    def check(self) -> None:
        """
        Fail when a read error is set.

        Raises
        ------
        sqlite3.Error
            The error set.
        """
        if self.error is not None:
            raise self.error

    def choices(self, *, path: ChooserPath) -> Column | None:
        """
        Give the fixed column.

        Parameters
        ----------
        path : ChooserPath
            Ignored.

        Returns
        -------
        Column | None
            The column.
        """
        self.check()
        return self.column

    def towards_line(self, *, towards: int) -> tuple[StopPlace, ...] | None:
        """
        Give the fixed stations.

        Parameters
        ----------
        towards : int
            Ignored.

        Returns
        -------
        tuple[StopPlace, ...] | None
            The stations.
        """
        self.check()
        return self.line

    def reach_input(
        self, *, key: StripKey, origin: StopPlace,
    ) -> ReachInput | None:
        """
        Give the fixed runs.

        Parameters
        ----------
        key : StripKey
            Ignored.
        origin : StopPlace
            Ignored.

        Returns
        -------
        ReachInput | None
            The runs.
        """
        self.check()
        return self.data


@dataclass
class MemoryStates:
    """
    Saved state kept in memory.

    Attributes
    ----------
    stored : Saved | None
        What was last saved; None when nothing ever was.
    error : OSError | None
        Raised by every save when set.
    saves : list[Saved]
        Every state saved, in order.
    """

    stored: Saved | None = None
    error: OSError | None = None
    saves: list[Saved] = field(default_factory=list)

    def load(self) -> Loaded:
        """
        Give what was stored, or the defaults.

        Returns
        -------
        Loaded
            The stored state, or the defaults not stored.
        """
        if self.stored is None:
            return Loaded(saved=DEFAULT_SAVED, stored=False)
        return Loaded(saved=self.stored, stored=True)

    def save(self, *, saved: Saved) -> None:
        """
        Keep a saved state.

        Parameters
        ----------
        saved : Saved
            The pins and view.

        Raises
        ------
        OSError
            The error set.
        """
        if self.error is not None:
            raise self.error
        self.stored = saved
        self.saves.append(saved)
