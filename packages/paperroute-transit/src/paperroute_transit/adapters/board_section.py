"""The board's section of the device kit's state file."""

from paperroute_device.core.sections import migrate_section
from paperroute_transit.adapters.state_file import (
    BoardSectionModel,
    board_section,
    from_model,
    repaired_board,
)
from paperroute_transit.adapters.state_migrations import (
    BOARD_MIGRATIONS,
    BOARD_SCHEMA,
)
from paperroute_transit.core.city import City
from paperroute_transit.core.saved import Saved


class BoardCodec:
    """
    Read and write the board's section: its pins, view and settings.

    Parameters
    ----------
    city : City
        The city: its time zone, default pin and kinds of pin.
    """

    def __init__(self, *, city: City) -> None:
        self.city = city

    def load(self, *, data: object) -> Saved:
        """
        Read the section, migrating and repairing it first.

        Parameters
        ----------
        data : object
            The section as stored.

        Returns
        -------
        Saved
            The board's state, with the city's default device settings.

        Raises
        ------
        ValueError
            When its version is missing, too new or has no step, or it
            is not valid after repair.
        """
        if isinstance(data, dict):
            data = migrate_section(
                data=data,
                steps=BOARD_MIGRATIONS,
                latest=BOARD_SCHEMA,
                name='board',
            )
        model = BoardSectionModel.model_validate(
            repaired_board(section=data, city=self.city),
        )
        return from_model(model=model, city=self.city)

    def dump(self, *, saved: object) -> dict[str, object]:
        """
        Write the section.

        Parameters
        ----------
        saved : object
            The board's state, a `Saved`.

        Returns
        -------
        dict[str, object]
            The section at `BOARD_SCHEMA`.

        Raises
        ------
        TypeError
            When `saved` is not the board's state.
        """
        if not isinstance(saved, Saved):
            raise TypeError(f'not the board state: {saved!r}')
        return board_section(saved=saved).model_dump(mode='json')
