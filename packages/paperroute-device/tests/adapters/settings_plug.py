"""A small Settings plug for the kit's tests: a chime, a colour and an alarm."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import time
from typing import Final

from paperroute_device.adapters.device_form import (
    SWITCH,
    UnknownValue,
    clock_of,
    clock_text,
)

ALARM_ERROR: Final[str] = 'Alarm not saved: use HH:MM.'
COLOURS: Final[frozenset[str]] = frozenset({'red', 'blue'})
DEFAULTS: Final[dict[str, str]] = {
    'chime': 'off',
    'colour': 'red',
    'alarm': '07:00',
}
SECTIONS: Final[str] = """\
    <section class="card setting" aria-labelledby="clock">
      <h2 id="clock">Clock</h2>
      <form id="clock-form" method="post" action="/settings">
        <input type="hidden" name="chime" value="off">
        <label class="switch">
          <input type="checkbox" role="switch" name="chime"
                 value="on"{{chime}}>
          Chime
        </label>
        <label class="switch">
          <input type="radio" name="colour" value="red"{{colour:red}}>
          Red
        </label>
        <label class="switch">
          <input type="radio" name="colour" value="blue"{{colour:blue}}>
          Blue
        </label>
        <label>Alarm
          <input type="time" name="alarm" value="{{alarm}}">
        </label>
        {{error_alarm}}
        <button type="submit">Save</button>
        <p class="hint saved" aria-live="polite"></p>
      </form>
    </section>
"""


@dataclass(frozen=True)
class ClockChange:
    """
    The clock settings a post changes.

    Attributes
    ----------
    chime : bool | None
        Whether the clock chimes.
    colour : str | None
        The face's colour.
    alarm : time | None
        When the alarm rings.
    """

    chime: bool | None = None
    colour: str | None = None
    alarm: time | None = None


class ClockPlug:
    """The clock's Settings section."""

    switches: Final[frozenset[str]] = frozenset({'chime'})
    radios: Final[frozenset[str]] = frozenset({'colour'})
    sheets: Final[tuple[str, ...]] = ('clock.css',)
    errors: Final[Mapping[str, str]] = {'alarm': ALARM_ERROR}

    def sections(self) -> str:
        """
        Write the Clock section.

        Returns
        -------
        str
            The section's lines.
        """
        return SECTIONS

    def values(self, *, status: Mapping[str, object]) -> dict[str, str]:
        """
        Read the clock's values from the status, over its defaults.

        Parameters
        ----------
        status : Mapping[str, object]
            The status fields.

        Returns
        -------
        dict[str, str]
            The chime, colour and alarm values.
        """
        reported = status.get('settings')
        values = dict(DEFAULTS)
        if isinstance(reported, Mapping):
            values.update({
                key: str(value)
                for key, value in reported.items()
                if key in DEFAULTS
            })
        return values

    def change_from(self, *, fields: Mapping[str, str]) -> object | None:
        """
        Read the clock's posted fields.

        Parameters
        ----------
        fields : Mapping[str, str]
            The posted fields.

        Returns
        -------
        object | None
            A `ClockChange`, or None when no clock field was posted.

        Raises
        ------
        UnknownValue
            For a chime or colour the page never sends.
        BadInput
            For an alarm that is not `HH:MM`.
        """
        if not set(fields) & set(DEFAULTS):
            return None
        chime = fields.get('chime')
        colour = fields.get('colour')
        if chime is not None and chime not in SWITCH:
            raise UnknownValue(chime)
        if colour is not None and colour not in COLOURS:
            raise UnknownValue(colour)
        alarm = fields.get('alarm')
        return ClockChange(
            chime=None if chime is None else SWITCH[chime],
            colour=colour,
            alarm=None if alarm is None else clock_of(
                text=alarm, section='alarm',
            ),
        )

    def posted_values(
        self, *, fields: Mapping[str, str], change: object,
    ) -> dict[str, str]:
        """
        Write the clock's posted fields as the status will report them.

        Parameters
        ----------
        fields : Mapping[str, str]
            The posted fields.
        change : object
            The `ClockChange` read from them.

        Returns
        -------
        dict[str, str]
            The posted clock values.
        """
        del fields
        if not isinstance(change, ClockChange):
            return {}
        values = {
            'chime': (
                None
                if change.chime is None
                else 'on' if change.chime else 'off'
            ),
            'colour': change.colour,
            'alarm': (
                None
                if change.alarm is None
                else clock_text(moment=change.alarm)
            ),
        }
        return {key: value for key, value in values.items() if value}
