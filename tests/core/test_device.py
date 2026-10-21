"""Tests for the device's own settings."""

from paperroute.core.messages import SaveState, SettingsChanged, Tick
from paperroute.core.state import SAVE_AFTER, step
from tests.core.factories import START
from tests.core.test_rebuild import pinned


def test_settings_change_is_saved() -> None:
    """A switched-off rebuild is saved once the save delay has passed."""
    state, effects = step(pinned(), SettingsChanged(auto_rebuild=False))
    assert not effects
    _, effects = step(state, Tick(now=START + SAVE_AFTER * 2))
    saves = [effect for effect in effects if isinstance(effect, SaveState)]
    assert len(saves) == 1
    assert saves[0].saved.settings.auto_rebuild is False
