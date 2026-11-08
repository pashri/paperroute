"""Tests for the versioned sections of a state file."""

import pytest

from paperroute_device.core.sections import (
    DEVICE_MIGRATIONS,
    DEVICE_SCHEMA,
    SectionStep,
    migrate_section,
)


def test_newer_section_refused() -> None:
    """A section newer than the code is refused; a current one is kept."""
    with pytest.raises(ValueError, match='device'):
        migrate_section(
            data={'schema_version': 3}, steps={}, latest=1, name='device',
        )
    data: dict[str, object] = {'schema_version': 1, 'settings': {}}
    assert migrate_section(
        data=data, steps={}, latest=1, name='device',
    ) == {'schema_version': 1, 'settings': {}}


@pytest.mark.parametrize('version', [None, '1', True, 1.0])
def test_migrate_section_refuses_odd_version(version: object) -> None:
    """A missing or non-integer section version is refused by name."""
    with pytest.raises(ValueError, match='board'):
        migrate_section(
            data={'schema_version': version},
            steps={},
            latest=1,
            name='board',
        )


def test_migrate_section_runs_steps_in_order() -> None:
    """Each step runs once, and the version follows it up."""
    def first(data: dict[str, object]) -> dict[str, object]:
        """
        Add a mark.

        Parameters
        ----------
        data : dict[str, object]
            Version 1 data.

        Returns
        -------
        dict[str, object]
            The data with a mark.
        """
        return {**data, 'seen': ['first']}

    def second(data: dict[str, object]) -> dict[str, object]:
        """
        Add a second mark.

        Parameters
        ----------
        data : dict[str, object]
            Version 2 data.

        Returns
        -------
        dict[str, object]
            The data with a second mark.
        """
        return {**data, 'seen': ['first', 'second']}

    steps: dict[int, SectionStep] = {1: first, 2: second}
    assert migrate_section(
        data={'schema_version': 1}, steps=steps, latest=3, name='board',
    ) == {'schema_version': 3, 'seen': ['first', 'second']}


def test_migrate_section_refuses_missing_step() -> None:
    """An older section with no step to bring it up is refused."""
    with pytest.raises(ValueError, match='device'):
        migrate_section(
            data={'schema_version': 0}, steps={}, latest=1, name='device',
        )


def test_device_chain_starts_at_one() -> None:
    """The device section starts at version 1 with no steps."""
    assert (DEVICE_SCHEMA, dict(DEVICE_MIGRATIONS)) == (1, {})
