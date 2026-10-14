# paperroute

A two-knob e-paper tracker for Sydney public transport (TfNSW open data),
built on Paul Lagier's shiptracker hardware: Raspberry Pi Zero 2 W,
Waveshare 7.5" V2 (800×480) and two push encoders.

## Interface mockups

`tools/mockups` turns the TfNSW timetable and an OpenStreetMap extract into
the interface boards.

1. Put the TfNSW complete GTFS (`complete.zip`) and BBBike's
   `Sydney.osm.pbf` in `data/raw/`, then unzip the GTFS into `data/gtfs/`.
2. `uv run --group mockups python tools/mockups/extract_osm.py data/raw/Sydney.osm.pbf data/osm`
3. Build the timetable data with `snapshot.py`, `catalogue.py`,
   `departures.py` and `trains_to.py` into `data/snap/`, then
   `bundle.py data/osm data/snap data/snap/bundle.json`.
4. Each `boards/*.template.html` gets the bundle in place of `__DATA__`.
