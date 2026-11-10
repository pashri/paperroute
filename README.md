# paperroute

A two-knob e-paper tracker for Sydney public transport (TfNSW open data),
built on Paul Lagier's shiptracker hardware: Raspberry Pi Zero 2 W,
Waveshare 7.5" V2 (800×480) and two push encoders.

## Layout

A uv workspace of four packages and one app per city:

- `packages/paperroute-hardware`: drivers for the panel, knobs and LED.
- `packages/paperroute-device`: settings, power, drawing and the device's
  web pages.
- `packages/paperroute-transit`: the transit model, geometry, map tiles and
  the board itself.
- `packages/paperroute-testkit`: fakes and factories shared by the tests.
- `apps/paperroute-sydney`: the Sydney app, with the TfNSW feeds and Trip
  Planner and the `paperroute-board` and `paperroute-build` commands.

Hardware, device and transit each import only from the ones above them,
the apps import from all three, and only the tests use the testkit.

## Run on the Mac

    uv python install 3.14
    uv sync
    uv run paperroute-build gtfs --bundle data/raw/complete.zip \
      --sydneytrains data/raw/st/sydneytrains.zip --metro data/raw/st/metro_v2.zip \
      --out data/timetable.db
    PAPERROUTE_KEY_FILE_TFNSW=~/tfnsw_api_key \
      uv run paperroute-board run --timetable data/timetable.db \
      --tiles data/tiles.db --state data/state.json

`--from` and `--to` (stop ids, for example `--from 215020 --to 200060`)
only seed a first start; after that the pins come from `--state`.

Keys: Q quits.

- S opens the route chooser, and goes back.
- A and D switch pins.
- K switches views, and holding K (about 0.8 s) pins or unpins on release.
- J and L scroll, zoom or move the cursor; on the Strip, J and L while K
  is held pick a train in the panel instead.

The chooser picks a kind first. Trains, metro, light rail and ferries go
line, then station, then destination on the same line; grey stations have no
direct service, and the From row reads ALL. Buses, school buses and coaches
go route, then direction, then stop. Replacement buses are under Bus, as
Trackwork.

Replay recorded feeds with `--feeds tests/fixtures/real --at 2026-10-16T06:25`.

Run the linters, type checks, layer check and tests with `scripts/check`.
`scripts/deploy` runs it first.

## Map tiles

Download Geofabrik's New South Wales `.osm.pbf` into `data/raw/nsw.osm.pbf`,
then build the tiles on the Mac:

    uv run paperroute-build tiles --osm data/raw/nsw.osm.pbf \
      --timetable data/timetable.db --out data/tiles.db

This builds all of NSW. It takes about 17 minutes on an Apple-silicon Mac,
needs about 1.2 GB of RAM and makes about 490 MB. Add `--bbox` to build a
smaller area for trying things out. Without `data/tiles.db` the Map shows
"Map not installed". The app shows "Map not installed" when the tiles file
was built for other zoom levels or another map style; rebuild the tiles when
a release changes them. scripts/deploy warns about such a file but deploys
anyway.

`scripts/deploy` copies `data/tiles.db` to the Pi only when it has changed.
The first upload takes about 12.5 minutes, and later deploys print "tiles
unchanged". The Pi needs about twice the tiles file's size free, plus
64 MB, while deploy installs it (about 1 GB), and deploy says so before
copying when it doesn't.

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

After a power-off, press knob A (the top knob unless the knobs are
swapped) to switch the board back on. Its press is wired to GPIO 3
(pin 5), the one line a halted Pi watches, so I2C must stay off.

A VPN on your Mac can hide `paperroute.local`. If it does, use the Pi's IP
address in its place. If the Pi's USB data cable is plugged into the Mac,
`paperroute.local` may point at the USB link: use the Pi's IP address or
unplug the data cable.

### Install

1. Provision, in your own terminal. Type the Pi password once at the sudo
   prompt.

       scripts/provision <user>@paperroute.local sydney --reboot

2. Set the TfNSW API key at `http://paperroute.local/device` under
   "TfNSW API key" (or on the phone during Wi-Fi setup).

3. Deploy. It never prompts.

       scripts/deploy <user>@paperroute.local sydney

4. Build the timetable. This takes about 40 minutes on a Zero 2 W, and the
   app starts by itself when it finishes.

       ssh -t <user>@paperroute.local 'sudo systemctl start --no-block paperroute-timetable'
       ssh <user>@paperroute.local journalctl -fu paperroute-timetable

5. Open `http://paperroute.local`, or the Pi's IP address.

The app logs to the journal (`journalctl -u paperroute`), and warnings and
errors also go to `/var/lib/paperroute/log/errors.jsonl`.

### Upgrade

When the release changes the systemd units:

1. Run `scripts/provision <user>@paperroute.local sydney`, without
   `--reboot`.
2. Straight away, run `scripts/deploy <user>@paperroute.local sydney`.

Do not reboot between the two: the old release cannot run under the new
units. When the units do not change, step 2 alone is enough.

This release changes the units: run the provision and the deploy together.

A release whose units contract differs from the installed one is not
switched back to; fix forward and redeploy.

After any rollback across this upgrade, restore the state.json backup: an
older release sets a newer file aside and starts with defaults.

A provision that changes an app timer's calendar drops that timer's zone
drop-in; the next deploy writes it again.

Provisioning a different app from the one installed renames
`/var/lib/paperroute/state.json` to `state.json.<old app>-<time>` and keeps it.

When a release changes the timetable format, the route chooser shows
"Routes need a timetable update" for about 40 minutes while the board
rebuilds the timetable itself.

### Green LED

- Fast blink: booting, until the first frame.
- Steady: running.
- Slow blink: the app is busy (rebuilding the timetable).

### Pins and refresh

Make pins with the route chooser on the device or on its web page, and hold
to pin. On the web page, double-click a Press button, or hold it for about
0.8 s. The pins live in
`/var/lib/paperroute/state.json`. A new device starts on Town Hall's map.

The timetable refreshes nightly at 03:30 Sydney time. If refreshes keep
failing, the board's banner says "Timetable ends <weekday>" from three days
before it runs out.

## Data

Timetable and real-time data come from Transport for NSW Open Data under
[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). The recorded
feeds and timetable slices in `tests/fixtures/real` are cut down from that
data, and some of their stops are edited.

Map data © [OpenStreetMap](https://www.openstreetmap.org/copyright)
contributors, available under the
[Open Database License (ODbL)](https://opendatacommons.org/licenses/odbl/).
