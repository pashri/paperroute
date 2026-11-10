"""Tests for the state file's one migration chain."""

import pytest

from paperroute_device.adapters.migrations import (
    KIT_MIGRATIONS,
    chain,
    latest_version,
    migrate,
    stored_version,
)
from paperroute_device.core.app import MigrationStep


def stamp(name: str) -> MigrationStep:
    """
    Make a step that records its name in the data.

    Parameters
    ----------
    name : str
        The name it records.

    Returns
    -------
    MigrationStep
        The step.
    """
    def step(data: dict[str, object]) -> dict[str, object]:
        """
        Record the step's name.

        Parameters
        ----------
        data : dict[str, object]
            Data at one version.

        Returns
        -------
        dict[str, object]
            The data with the name added to its `ran` list.
        """
        ran = data.get('ran', [])
        assert isinstance(ran, list)
        return {**data, 'ran': [*ran, name]}

    return step


def test_repeated_step_number_refuses() -> None:
    """A step number the kit and the app both register is refused."""
    first = stamp('kit')
    second = stamp('app')
    with pytest.raises(
        ValueError, match='^migration step 3 is registered twice$',
    ):
        chain(kit={3: first}, app={1: second, 3: second})


def test_chain_orders_by_version() -> None:
    """The chain holds both parts, ordered by version."""
    first = stamp('a')
    second = stamp('b')
    found = chain(kit={4: first}, app={2: second})
    assert list(found) == [2, 4]


def test_kit_registers_no_steps_yet() -> None:
    """The kit's own part of the chain is empty."""
    assert not KIT_MIGRATIONS


def test_latest_is_max_plus_one() -> None:
    """The file is written one past the highest step."""
    found = chain(kit={}, app={1: stamp('a'), 12: stamp('b')})
    assert latest_version(chain=found) == 13


def test_no_chain_starts_at_one() -> None:
    """With no steps at all the file is version 1."""
    assert latest_version(chain=chain(kit={}, app={})) == 1


def test_missing_step_is_a_no_op() -> None:
    """A file passes a version with no step unchanged, and runs the rest."""
    found = chain(kit={}, app={1: stamp('a'), 3: stamp('c')})
    latest = latest_version(chain=found)
    data = migrate(
        data={'schema_version': 1, 'kept': True}, chain=found, latest=latest,
    )
    assert data == {'schema_version': 4, 'kept': True, 'ran': ['a', 'c']}


def test_current_file_is_left_alone() -> None:
    """A file at the latest version runs no step."""
    found = chain(kit={}, app={1: stamp('a')})
    data = migrate(data={'schema_version': 2}, chain=found, latest=2)
    assert data == {'schema_version': 2}


def test_future_version_names_the_future() -> None:
    """A version past the latest is refused by name."""
    with pytest.raises(
        ValueError, match='^schema version 5 is from the future$',
    ):
        stored_version(data={'schema_version': 5}, latest=4)


@pytest.mark.parametrize('version', [None, '3', True, 2.0, 0, -1])
def test_bad_version_is_refused(version: object) -> None:
    """A version that is not a whole number from 1 is refused."""
    with pytest.raises(ValueError, match='^bad schema version '):
        stored_version(data={'schema_version': version}, latest=4)
