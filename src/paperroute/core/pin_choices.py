"""What a pin's editor accepts."""

from typing import Final

NICKNAME_MAX: Final[int] = 24


def clean_nickname(*, text: str) -> str | None:
    """
    Tidy a nickname typed by the owner.

    Parameters
    ----------
    text : str
        The typed text.

    Returns
    -------
    str | None
        The text with whitespace collapsed and trimmed, cut to
        `NICKNAME_MAX` characters; None when nothing is left.
    """
    return ' '.join(text.split())[:NICKNAME_MAX].strip() or None
