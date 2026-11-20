"""One route line the Map draws."""

from dataclasses import dataclass, field

from paperroute_transit.core.geo import Shape


@dataclass(frozen=True)
class MapLine:
    """A shape and the stretch of it drawn solid.

    Attributes
    ----------
    shape_id : str
        The shape's id.
    solid : tuple[float, float] | None
        Metres along the shape where the solid stretch starts and ends;
        None draws the whole line dashed.
    shape : Shape
        The shape; it does not take part in comparison.
    fingerprint : tuple[int, float]
        The shape's point count and length, set from `shape`; lines
        compare equal only when these match too.
    """

    shape_id: str
    solid: tuple[float, float] | None
    shape: Shape = field(compare=False, repr=False)
    fingerprint: tuple[int, float] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        """Set the fingerprint from the shape."""
        object.__setattr__(
            self,
            'fingerprint',
            (len(self.shape.points), self.shape.along[-1]),
        )
