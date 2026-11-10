"""Tests for the device's menu rows."""

from paperroute_device.core.device_model import (
    KIT_HEAD,
    KIT_TAIL,
    AppRow,
    MenuRow,
    menu_rows,
)


def test_menu_rows_put_app_rows_before_restart() -> None:
    """The app's rows sit between Sleep now and Restart."""
    extra = (AppRow(label='First'), AppRow(label='Second'))
    rows = menu_rows(app_rows=extra)
    assert rows == (*KIT_HEAD, *extra, *KIT_TAIL)
    assert rows.index(extra[0]) == rows.index(MenuRow.SLEEP) + 1
    assert rows[rows.index(extra[1]) + 1] is MenuRow.RESTART


def test_menu_rows_without_an_app_are_the_kit_rows() -> None:
    """With no app rows only the kit's seven remain."""
    assert menu_rows(app_rows=()) == tuple(MenuRow)
    assert len(MenuRow) == 7
