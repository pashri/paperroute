"""Tests for the panel's confirm screen."""

import re
from datetime import datetime, timezone
from typing import Final

from paperroute_device.core.device_model import Confirm, ConfirmAction, Origin
from paperroute_device.core.panel_views import (
    KIT_CONFIRM_COPY,
    ConfirmView,
    confirm_view,
)

ASKED_AT: Final[datetime] = datetime(2025, 10, 21, tzinfo=timezone.utc)
SPECIFIC: Final[re.Pattern[str]] = re.compile(
    r'(?i)\b(pins?|key|timetable|map|tiles?)\b',
)


def test_confirm_copy_is_a_parameter() -> None:
    """The line saying what happens comes from the copy given."""
    copy = {action: f'Does {action.value}.' for action in ConfirmAction}
    confirm = Confirm(
        action=ConfirmAction.ERASE, origin=Origin.PANEL, asked_at=ASKED_AT,
    )
    assert confirm_view(confirm=confirm, copy=copy) == ConfirmView(
        title='Settings',
        question='Erase everything?',
        lines=('Does erase.',),
        cancel='Top: press to cancel',
        confirm='Bottom: press to erase',
    )


def test_kit_confirm_copy_names_no_pins_key_or_timetable() -> None:
    """The kit's own lines say nothing about an app's data."""
    assert set(KIT_CONFIRM_COPY) == set(ConfirmAction)
    for action in (
        ConfirmAction.RESTART,
        ConfirmAction.RESET_SETTINGS,
        ConfirmAction.ERASE,
    ):
        assert not SPECIFIC.search(KIT_CONFIRM_COPY[action])
    assert KIT_CONFIRM_COPY[ConfirmAction.RESTART] == (
        'paperroute starts again. Your settings stay.'
    )
    assert KIT_CONFIRM_COPY[ConfirmAction.ERASE] == (
        'Clears the settings, Wi-Fi, the keys and the saved data.'
    )
