"""The stations facing the stops a both-sides strip's buses approach by."""

from collections.abc import Mapping

from paperroute_transit.core.reach import ReachInput
from paperroute_transit.core.sides import KerbPair


def approach_stations(*, data: ReachInput) -> frozenset[str]:
    """
    List the stations called at before the From on either kerb.

    Parameters
    ----------
    data : ReachInput
        The strip's runs.

    Returns
    -------
    frozenset[str]
        Every station in the before of the tails and the across tails.
    """
    return frozenset(
        station
        for run in (*data.tails, *data.across_tails)
        for station in run.before
    )


def facing_of(*, pairs: Mapping[str, KerbPair]) -> dict[str, str]:
    """
    Name the station across the road from each paired station.

    Parameters
    ----------
    pairs : Mapping[str, KerbPair]
        Kerb pairs by station.

    Returns
    -------
    dict[str, str]
        The partner's station ID by station.
    """
    return {
        station: pair.other.stop_id for station, pair in pairs.items()
    }
