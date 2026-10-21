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
      uv run python -m paperroute run --timetable data/timetable.db \
      --tiles data/tiles.db --state data/state.json

`--from` and `--to` (stop ids, for example `--from 215020 --to 200060`)
only seed a first start; after that the pins come from `--state`.

Keys: Q quits.

- S opens the route chooser, and goes back.
- A and D switch pins.
- K switches views, and holding K (about 0.8 s) pins or unpins.
- J and L scroll, zoom or move the cursor.

The chooser picks a kind first. Trains, metro, light rail and ferries go
line, then station, then destination on the same line; grey stations have no
direct service, and the From row reads ALL. Buses, school buses and coaches
go route, then direction, then stop. Replacement buses are under Bus, as
Trackwork.

Replay recorded feeds with `--feeds tests/fixtures/real --at 2026-10-16T06:25`.

## Map tiles

Download Geofabrik's New South Wales `.osm.pbf` into `data/raw/nsw.osm.pbf`,
then build the tiles on the Mac:

    uv run python -m paperroute.build tiles --osm data/raw/nsw.osm.pbf \
      --timetable data/timetable.db --out data/tiles.db

This builds all of NSW. It takes about 28 minutes on an Apple-silicon Mac,
needs about 2 GB of RAM and makes about 470 MB. Add `--bbox` to build a
smaller area for trying things out. Without `data/tiles.db` the Map shows
"Map not installed".

`scripts/deploy` copies `data/tiles.db` to the Pi only when it has changed.
The first upload takes about 12.5 minutes, and later deploys print "tiles
unchanged". The Pi needs about 3 times the tiles file's size free while
deploy installs it (about 1.4 GB), and deploy says so before copying when
it doesn't.

Press the bottom knob to switch views; on the Map, turn it to zoom. Hollow badges are timetable positions. A small "4m" tab means
the bus was last heard 4 minutes ago.

## Install on the device

For a Pi built from Paul Lagier's shiptracker plans. Replace `<user>` with
your Pi username.

### Flash the card

Put the microSD card in your Mac through an adapter or card reader, not in
the Pi. In Raspberry Pi Imager choose:

- Device: Raspberry Pi Zero 2 W
- OS: Raspberry Pi OS (other), then Raspberry Pi OS Lite (64-bit), Trixie
- Hostname `paperroute`, your username and a password
- Wi-Fi (2.4 GHz) with country AU
- Time zone UTC
- SSH with your public key

### Power on

Insert the card in the Pi and power it through PWR IN. The Zero 2 W has no
power LED, and the green ACT LED stays dark until the OS boots.

A VPN on your Mac can hide `paperroute.local`. If it does, use the Pi's IP
address in its place. If the Pi's USB data cable is plugged into the Mac,
`paperroute.local` may point at the USB link: use the Pi's IP address or
unplug the data cable.

### Install

1. Provision, in your own terminal. Type the Pi password once at the sudo
   prompt.

       scripts/provision <user>@paperroute.local --reboot

2. Put the TfNSW API key on the Pi without printing it.

       ssh <user>@paperroute.local 'umask 077; cat > ~/tfnsw_api_key.tmp' < ~/tfnsw_api_key
       ssh -t <user>@paperroute.local 'sudo install -m 600 -o paperroute -g paperroute ~/tfnsw_api_key.tmp /var/lib/paperroute/secrets && rm ~/tfnsw_api_key.tmp'

3. Deploy. It never prompts.

       scripts/deploy <user>@paperroute.local

4. Build the timetable. This takes about 40 minutes on a Zero 2 W, and the
   app starts by itself when it finishes.

       ssh -t <user>@paperroute.local 'sudo systemctl start --no-block paperroute-timetable'
       ssh <user>@paperroute.local journalctl -fu paperroute-timetable

5. Open `http://paperroute.local`, or the Pi's IP address.

### Upgrade from the previous install

1. Run `scripts/deploy <user>@paperroute.local`.
2. Wait until the app has written `/var/lib/paperroute/state.json`.
3. Run `scripts/provision <user>@paperroute.local` once more, without
   `--reboot`. It refuses to run before step 2 finishes.
4. Rebuild the timetable, as in Install step 4 (the nightly 03:30 run does
   it too). Until it finishes the line chooser says "Routes need a timetable
   update".

### Green LED

- Fast blink: booting, until the first frame.
- Steady: running.
- Slow blink: rebuilding the timetable.

### Pins and refresh

Make pins with the route chooser on the device or on its web page, and hold
to pin. On the web page, double-click a Press button, or hold it for about
0.8 s. The pins live in
`/var/lib/paperroute/state.json`. A new device starts on Town Hall's map.

The timetable refreshes nightly at 03:30 Sydney time.
