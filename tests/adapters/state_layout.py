"""State files in the older layouts tests write by hand."""

from pathlib import Path
from typing import Any, Final

HERE: Final[Path] = Path(__file__).parent
STATE_V11: Final[Path] = HERE / 'state_v11.json'
STATE_V11_KINDS: Final[Path] = HERE / 'state_v11_kinds.json'
STATE_V12: Final[Path] = HERE / 'state_v12.json'


def flat_v11(*, data: dict[str, Any]) -> dict[str, Any]:
    """
    Give a sectioned file's data in the schema 11 layout.

    Parameters
    ----------
    data : dict[str, Any]
        Data in the sectioned layout, with both sections.

    Returns
    -------
    dict[str, Any]
        One flat object at schema 11: the board's fields at the top,
        and the device and board settings in one ``settings`` object.
    """
    board = {
        key: value
        for key, value in data['board'].items()
        if key != 'schema_version'
    }
    return {
        'schema_version': 11,
        **board,
        'settings': {
            **data['device']['settings'],
            **data['board']['settings'],
        },
    }
