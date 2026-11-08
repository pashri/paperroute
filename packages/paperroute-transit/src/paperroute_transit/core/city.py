"""The values that fit the engine to one city."""

from dataclasses import dataclass
from zoneinfo import ZoneInfo

from paperroute_transit.core.geo import DegreeBox, Projection
from paperroute_transit.core.modes import Classifier, ModeSpec, spec_of


@dataclass(frozen=True)
class City:
    """
    One city's settings.

    Attributes
    ----------
    name : str
        The city's name.
    zone : ZoneInfo
        Local time zone: clocks, service days and auto windows use it.
    projection : Projection
        The metre projection and pixel grid for its maps.
    bbox : DegreeBox
        The area its tiles may cover.
    modes : tuple[ModeSpec, ...]
        Its modes, in chooser order.
    classify : Classifier
        Sorts a route into a mode and a feed source.
    """

    name: str
    zone: ZoneInfo
    projection: Projection
    bbox: DegreeBox
    modes: tuple[ModeSpec, ...]
    classify: Classifier

    def mode(self, *, mode_id: str | None) -> ModeSpec | None:
        """
        Find one of the city's modes.

        Parameters
        ----------
        mode_id : str | None
            Mode id, or None.

        Returns
        -------
        ModeSpec | None
            Its spec; None for None or an id the city does not declare.
        """
        return spec_of(modes=self.modes, mode=mode_id)

    @property
    def mode_ids(self) -> frozenset[str]:
        """
        The ids of the city's modes.

        Returns
        -------
        frozenset[str]
            Every declared mode id.
        """
        return frozenset(spec.id for spec in self.modes)
