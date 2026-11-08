"""Versioned sections of a state file, each with its own migrations."""

from collections.abc import Callable, Mapping
from typing import Final

type SectionStep = Callable[[dict[str, object]], dict[str, object]]

DEVICE_SCHEMA: Final[int] = 1
DEVICE_MIGRATIONS: Final[Mapping[int, SectionStep]] = {}


def migrate_section(
    *,
    data: dict[str, object],
    steps: Mapping[int, SectionStep],
    latest: int,
    name: str,
) -> dict[str, object]:
    """
    Bring one section up to its latest version.

    Parameters
    ----------
    data : dict[str, object]
        The stored section.
    steps : Mapping[int, SectionStep]
        The step that brings each version up one.
    latest : int
        The version this code writes.
    name : str
        The section's name, for errors.

    Returns
    -------
    dict[str, object]
        The section at `latest`.

    Raises
    ------
    ValueError
        When its version is missing, not a whole number, above `latest`,
        or has no step.
    """
    version = data.get('schema_version')
    if not isinstance(version, int) or isinstance(version, bool):
        raise ValueError(f'{name} section has bad version {version!r}')
    if version > latest:
        raise ValueError(f'{name} section version {version} is too new')
    while version < latest:
        if version not in steps:
            raise ValueError(f'{name} section has no step from {version}')
        data = steps[version](data)
        version += 1
        data['schema_version'] = version
    return data
