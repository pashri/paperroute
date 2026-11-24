"""A small cache of results keyed on the very objects they came from."""

from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Final

type Key = tuple[tuple[int, ...], tuple[object, ...]]
NO_SAME: Final[tuple[object, ...]] = ()


@dataclass
class SameMemo[V]:
    """
    Results remembered for the same input objects, oldest dropped first.

    A key is the identity of each input, so equal but separate objects
    miss. The inputs are held with the result, so an id is never reused
    while its entry stands. An optional `same` tuple of hashable values
    is compared by equality alongside the identities.

    Attributes
    ----------
    size : int
        The most results held.
    held : OrderedDict[Key, tuple[tuple[object, ...], V]]
        Inputs and result by the inputs' ids and `same` values, oldest
        first.
    """

    size: int
    held: OrderedDict[Key, tuple[tuple[object, ...], V]] = field(
        default_factory=OrderedDict,
    )

    def clear(self) -> None:
        """Forget every result."""
        self.held.clear()

    def get(
        self,
        *,
        inputs: tuple[object, ...],
        same: tuple[object, ...] = NO_SAME,
    ) -> V | None:
        """
        Find the result held for these very objects.

        Parameters
        ----------
        inputs : tuple[object, ...]
            The objects the result was worked out from.
        same : tuple[object, ...]
            Hashable values that must be equal, not identical.

        Returns
        -------
        V | None
            The result, or None when it is not held.
        """
        key = (tuple(map(id, inputs)), same)
        entry = self.held.get(key)
        if entry is None:
            return None
        self.held.move_to_end(key)
        return entry[1]

    def put(
        self,
        *,
        inputs: tuple[object, ...],
        value: V,
        same: tuple[object, ...] = NO_SAME,
    ) -> V:
        """
        Hold a result for some objects.

        Parameters
        ----------
        inputs : tuple[object, ...]
            The objects the result was worked out from.
        value : V
            The result.
        same : tuple[object, ...]
            Hashable values that must be equal, not identical.

        Returns
        -------
        V
            The result.
        """
        self.held[(tuple(map(id, inputs)), same)] = (inputs, value)
        if len(self.held) > self.size:
            self.held.popitem(last=False)
        return value
