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

## Run on the Mac

    uv python install 3.14
    uv sync
    uv run python -m paperroute.build gtfs --bundle data/raw/complete.zip \
      --sydneytrains data/raw/st/sydneytrains.zip --metro data/raw/st/metro_v2.zip \
      --out data/timetable.db
    PAPERROUTE_API_KEY_FILE=~/tfnsw_api_key \
      uv run python -m paperroute run --timetable data/timetable.db --from 215020 --to 200060

Keys: A/D/S top knob, J/L/K bottom knob, Q quits. Replay recorded feeds with
`--feeds tests/fixtures/real --at 2026-10-16T06:25`.
