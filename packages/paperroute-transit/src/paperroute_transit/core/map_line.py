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
    """

    shape_id: str
    solid: tuple[float, float] | None
    shape: Shape = field(compare=False, repr=False)
