# Map sidebar headsigns

## What changed

- Every Map sidebar row's second line is now `<headsign> · arr H:MM`,
  with or without a destination. The `to X` header stays for
  destination pins. The stop count is no longer shown.
- When the headsign and arrival don't fit, the headsign is shortened
  (naming rules, then an ellipsis) so ` · arr H:MM` always fits. Before,
  the arrival was dropped.
- The old arrival-only line (`arrival_note`) is gone.

## Owner's picks

1. Destination pins show the bus's headsign plus arrival, the same as
   pins without a destination, and drop the stop count.
2. A long headsign gives way to the arrival, never the other way round.

## Rulings

- Siblings for naming are the rows' headsigns in both cases.
- Destination pins now show the note slot too: a platform, or
  `uncertain` when no late mark carries it. Before, they showed only
  `uncertain`.
- Run-on rows keep the headsign plus run-on tag, without the arrival,
  as before; the tag already competes for the line.
- A row with no headsign reads `arr H:MM` alone.
