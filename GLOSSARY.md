# Paperroute

A desk-side e-paper board showing live Sydney public transport, worked by two knobs and a phone.

## Panel

**Full refresh**:
The whole screen redrawn from white, with true greys.
_Avoid_: Clean, redraw

**Quick patch**:
A fast black-and-white update of the changed areas only.
_Avoid_: Partial, fast refresh

**Clear refresh**:
A fast whole-screen black-and-white redraw, used when a menu opens.
_Avoid_: Fast full, mono full

**Cleaning refresh**:
A full refresh that clears leftovers after several quick patches.
_Avoid_: Tidy-up, scheduled full

**Menu**:
Any all black-and-white screen worked with the knobs, such as the route chooser or Settings.
_Avoid_: Settings screen (for the chooser)

**Sleep mode**:
The device state where the board shows "Asleep" and waits for a knob.
_Avoid_: Panel sleep (the panel resting between updates is not Sleep mode)
