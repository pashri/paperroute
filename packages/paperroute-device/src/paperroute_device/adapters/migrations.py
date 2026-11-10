"""The state file's one migration chain: the kit's steps and the app's."""

from collections.abc import Mapping
from typing import Final

from paperroute_device.core.app import MigrationStep

KIT_MIGRATIONS: Final[dict[int, MigrationStep]] = {}


def chain(
    *,
    kit: Mapping[int, MigrationStep],
    app: Mapping[int, MigrationStep],
) -> dict[int, MigrationStep]:
    """
    Join the kit's steps and the app's into one chain.

    Parameters
    ----------
    kit : Mapping[int, MigrationStep]
        The kit's steps, by the version each brings up one.
    app : Mapping[int, MigrationStep]
        The running app's steps, numbered in the same version space.

    Returns
    -------
    dict[int, MigrationStep]
        Every step, ordered by version.

    Raises
    ------
    ValueError
        When the kit and the app both register a step number.
    """
    repeated = sorted(kit.keys() & app.keys())
    if repeated:
        raise ValueError(f'migration step {repeated[0]} is registered twice')
    return dict(sorted({**kit, **app}.items()))


# pylint: disable-next=redefined-outer-name
def latest_version(*, chain: Mapping[int, MigrationStep]) -> int:
    """
    Give the version the file is written at.

    Parameters
    ----------
    chain : Mapping[int, MigrationStep]
        The chain.

    Returns
    -------
    int
        One past the highest step; 1 with no steps.
    """
    return max(chain, default=0) + 1


def stored_version(*, data: dict[str, object], latest: int) -> int:
    """
    Read the stored schema version.

    Parameters
    ----------
    data : dict[str, object]
        The decoded file.
    latest : int
        The version this process writes.

    Returns
    -------
    int
        The version.

    Raises
    ------
    ValueError
        When it is missing, not a whole number from 1, or above `latest`.
    """
    version = data.get('schema_version')
    if (
        not isinstance(version, int)
        or isinstance(version, bool)
        or version < 1
    ):
        raise ValueError(f'bad schema version {version!r}')
    if version > latest:
        raise ValueError(f'schema version {version} is from the future')
    return version


def migrate(
    *,
    data: dict[str, object],
    chain: Mapping[int, MigrationStep],  # pylint: disable=redefined-outer-name
    latest: int,
) -> dict[str, object]:
    """
    Bring stored data up to the latest version.

    Parameters
    ----------
    data : dict[str, object]
        The decoded file.
    chain : Mapping[int, MigrationStep]
        The chain.
    latest : int
        The version this process writes.

    Returns
    -------
    dict[str, object]
        The data at `latest`. A version with no step in the chain is
        passed unchanged.

    Raises
    ------
    ValueError
        When the version is missing, not a whole number from 1, or above
        `latest`.
    """
    version = stored_version(data=data, latest=latest)
    data = dict(data)
    while version < latest:
        if version in chain:
            data = chain[version](data)
        version += 1
        data['schema_version'] = version
    return data
