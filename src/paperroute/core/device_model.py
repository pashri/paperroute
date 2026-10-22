"""What the device knows about itself."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Panel:
    """
    The device's own state.

    Attributes
    ----------
    key_set : bool | None
        Whether a TfNSW API key is set; None until known.
    """

    key_set: bool | None = None
