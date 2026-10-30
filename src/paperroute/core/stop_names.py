"""Shorter forms of stop names."""

import re
from typing import Final

SHORT_DROPS: Final[tuple[str, ...]] = (
    r' Opp .*$',
    r' at .*$',
    r',.*$',
    r' Station$',
    r' Stn$',
)


def short_name(*, name: str) -> str:
    """
    Shorten a stop name to the place's own name.

    Each pattern in ``SHORT_DROPS`` is removed in turn. A removal that
    would leave nothing is skipped.

    Parameters
    ----------
    name : str
        Full stop name.

    Returns
    -------
    str
        The name without ' Opp ...', ' at ...', ', ...', ' Station' or
        ' Stn' at its end.
    """
    for pattern in SHORT_DROPS:
        name = re.sub(pattern, '', name) or name
    return name
