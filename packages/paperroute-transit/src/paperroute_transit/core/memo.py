"""A small cache of results keyed on the very objects they came from."""

from collections import OrderedDict
from dataclasses import dataclass, field


@dataclass
class SameMemo[V]:
    """
    Results remembered for the same input objects, oldest dropped first.

    A key is the identity of each input, so equal but separate objects
    miss. The inputs are held with the result, so an id is never reused
    while its entry stands.

    Attributes
    ----------
    size : int
        The most results held.
    held : OrderedDict[tuple[int, ...], tuple[tuple[object, ...], V]]
        Inputs and result by the inputs' ids, oldest first.
    """

    size: int
    held: OrderedDict[tuple[int, ...], tuple[tuple[object, ...], V]] = (
        field(default_factory=OrderedDict)
    )

    def get(self, *, inputs: tuple[object, ...]) -> V | None:
        """
        Find the result held for these very objects.

        Parameters
        ----------
        inputs : tuple[object, ...]
            The objects the result was worked out from.

        Returns
        -------
        V | None
            The result, or None when it is not held.
        """
        key = tuple(map(id, inputs))
        entry = self.held.get(key)
        if entry is None:
            return None
        self.held.move_to_end(key)
        return entry[1]

    def put(self, *, inputs: tuple[object, ...], value: V) -> V:
        """
        Hold a result for some objects.

        Parameters
        ----------
        inputs : tuple[object, ...]
            The objects the result was worked out from.
        value : V
            The result.

        Returns
        -------
        V
            The result.
        """
        self.held[tuple(map(id, inputs))] = (inputs, value)
        if len(self.held) > self.size:
            self.held.popitem(last=False)
        return value
