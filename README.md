# paperroute

A two-knob e-paper tracker for Sydney public transport (TfNSW open data),
built on Paul Lagier's shiptracker hardware: Raspberry Pi Zero 2 W,
Waveshare 7.5" V2 (800×480) and two push encoders.

## Layout

A uv workspace of five packages and one app per city:

- `packages/paperroute-hardware`: drivers for the panel, knobs and LED.
- `packages/paperroute-device`: settings, power, drawing and the device's
  web pages.
- `packages/paperroute-transit`: the transit model, geometry, map tiles and
  the board itself.
- `packages/paperroute-testkit`: fakes and factories shared by the tests.
- `packages/paperroute-device-testkit`: fakes for the device's tests.
- `apps/paperroute-sydney`: the Sydney app, with the TfNSW feeds and Trip
  Planner and the `paperroute-board` and `paperroute-build` commands.
- `apps/paperroute-cambridge`: the Cambridge app: buses, coaches and trains.

Hardware, device and transit each import only from the ones above them,
the apps import from all three, and only the tests use the two test kits.

## Run on the Mac

    uv python install 3.14
    uv sync
    uv run python -m paperroute_sydney.build gtfs --bundle data/raw/complete.zip \
      --sydneytrains data/raw/st/sydneytrains.zip --metro data/raw/st/metro_v2.zip \
      --out data/timetable.db
    PAPERROUTE_KEY_FILE_TFNSW=~/tfnsw_api_key \
      uv run python -m paperroute_sydney run --timetable data/timetable.db \
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

    uv run python -m paperroute_sydney.build tiles --osm data/raw/nsw.osm.pbf \
      --timetable data/timetable.db --out data/tiles.db

This builds all of NSW. It takes about 17 minutes on an Apple-silicon Mac,
needs about 1.2 GB of RAM and makes about 490 MB. `--track-osm` defaults to
`--osm`. Add `--bbox` to build a
smaller area for trying things out. Without `data/tiles.db` the Map shows
"Map not installed". The app shows "Map not installed" when the tiles file
was built for other zoom levels or another map style; rebuild the tiles when
a release changes them. `scripts/deploy` runs
`apps/paperroute-sydney/scripts/before-release`, which warns about such a
file but deploys anyway.

`before-release` copies `data/tiles.db` to the Pi only when it has changed.
The first upload takes about 12.5 minutes, and later deploys print "tiles
unchanged". The Pi needs about twice the tiles file's size free, plus
64 MB, while deploy installs it (about 1 GB), and deploy says so before
copying when it doesn't. On a Pi that is not provisioned yet, deploy prints
"deploy: tiles not installed: run scripts/provision HOST sydney, then deploy
again" and carries on; the tiles go in through `paperroute-sydney-install-tiles`
on the next deploy.

Press the bottom knob to switch views; on the Map, turn it to zoom. Hollow badges are timetable positions. A small "4m" tab means
the bus was last heard 4 minutes ago.

With no Destination, the Stop view shows how the next departure is running, where its vehicle was last seen when its position is reported, and its next three calls. For this, Sydney fetches the next vehicle's positions while the Stop view shows.

A train's Strip follows the next train, lists the stations it passes without calling, and names the other ways trains leave it.

## Cambridge

The Cambridge app shows buses and coaches from the Bus Open Data Service
(BODS), and trains at timetable times from the national rail timetable that
the gb-transit project publishes on GitHub. Its data lives in
`data/cambridge/`. Trains are listed by line, named for where they go; a
train pin is a From and an optional Destination.

Build the timetable. `refresh` downloads the public BODS timetable (no key)
and the latest gb-transit release's `gtfs-national-rail-only.zip`, keeps every
train that calls at Cambridge or Ely, and rebuilds
`data/cambridge/timetable.db`. Without a rail zip it keeps the trains it last
built from, or builds the buses alone; the Device page's Sources row
gives each source's date.

    uv run python -m paperroute_cambridge.build refresh --data-dir data/cambridge

To build from local zips:

    uv run python -m paperroute_cambridge.build gtfs \
      --bods data/cambridge/raw/bods.zip \
      --rail data/cambridge/raw/rail/gtfs-national-rail-only.zip \
      --out /tmp/timetable.db --start 2026-11-16 --days 14

On the Mac, the build of the 2026-11-16 feeds took about 24 s with a peak of
about 80 MB and made a 30 MB file with 66 routes and 7,276 trips: 5,735 bus
and coach trips and 1,541 train trips.

Download Geofabrik's `cambridgeshire`, `bedfordshire`, `hertfordshire`,
`essex`, `suffolk`, `greater-london` and `norfolk` extracts
(`https://download.geofabrik.de/europe/united-kingdom/england/<name>-latest.osm.pbf`)
into `data/cambridge/raw/`, merge the first five and build the tiles. The
rail track is read from every `--track-osm` file:

    osmium merge data/cambridge/raw/{cambridgeshire,bedfordshire,hertfordshire,essex,suffolk}-latest.osm.pbf \
      -o data/cambridge/raw/cambridge-area.osm.pbf
    uv run python -m paperroute_cambridge.build tiles \
      --osm data/cambridge/raw/cambridge-area.osm.pbf \
      --track-osm data/cambridge/raw/cambridge-area.osm.pbf \
      --track-osm data/cambridge/raw/greater-london-latest.osm.pbf \
      --track-osm data/cambridge/raw/norfolk-latest.osm.pbf \
      --timetable data/cambridge/timetable.db --out data/cambridge/tiles.db
    uv run python -m paperroute_cambridge.build check-tiles data/cambridge/tiles.db

The merge makes a 172 MB file in 3.4 s. The tiles take 131 s with a peak of
about 1,000 MB and make an 18 MB file with 4,131 tiles, 8,400 labels and a
rail track of 108,445 nodes and 577 stations.

Build in this order: the timetable, the tiles, then the timetable again with
`--tiles data/cambridge/tiles.db`, which writes the stations each train passes
between its calls. On the Mac that last build took about 25 s with a peak of
about 74 MB, against 44 MB without the tiles: 406 hops between stations, 296
of them on the track.

Run the board live:

    PAPERROUTE_KEY_FILE_BODS=~/bods_api_key \
      uv run python -m paperroute_cambridge run --timetable data/cambridge/timetable.db \
      --tiles data/cambridge/tiles.db --state data/cambridge/state.json

Replay the recorded feeds:

    uv run python -m paperroute_cambridge run --timetable data/cambridge/timetable.db \
      --tiles data/cambridge/tiles.db --state data/cambridge/state.json \
      --feeds apps/paperroute-cambridge/tests/fixtures/real --at 2026-11-16T21:25

## Install on the device

For a Pi built from Paul Lagier's shiptracker plans. Replace `<user>` with
your Pi username.

### Flash the card

Put the microSD card in your Mac through an adapter or card reader, not in
the Pi. In Raspberry Pi Imager choose:

- Device: Raspberry Pi Zero 2 W
- OS: Raspberry Pi OS (other), then Raspberry Pi OS Lite (64-bit), Trixie
- Hostname `paperroute`, your username and a password
- Wi-Fi (2.4 GHz) with the country the device is in (GB, AU)
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
errors also go to `/opt/paperroute/data/log/errors.jsonl`.

A second app is provisioned and deployed the same way as the first and waits
until it is chosen under "App" on the Device page. `sudo paperroute-switch-app
<app>` does the same from a shell. Each app's files are under
`/var/lib/paperroute/apps/<app>/`, for example `/var/lib/paperroute/apps/sydney/`.

`scripts/provision <user>@paperroute.local cambridge` and
`scripts/deploy <user>@paperroute.local cambridge` install Cambridge beside
Sydney. It waits until it is chosen under "App" on the Device page. Its data
is in `/var/lib/paperroute/apps/cambridge/`. Type the bus data key under "Bus
data API key" on the Device page while Cambridge is showing. Its timetable
builds at 06:30 UK time.

Reset settings and Erase everything act on the app that is showing. Other
installed apps keep their settings and data; Wi-Fi is forgotten for the whole
device.

### Where things live

```
/opt/paperroute/python/                        shared Python
/opt/paperroute/apps/<app>/releases/<sha>/     that app's releases, three kept
/opt/paperroute/apps/<app>/current             -> releases/<sha>
/opt/paperroute/current                        -> /opt/paperroute/apps/<active>/current
/opt/paperroute/data                           -> /var/lib/paperroute/apps/<active>
/var/lib/paperroute/apps/<app>/                state.json and its set-aside copies, secrets,
                                               log/, sources/, work/, the app's own files
/usr/local/share/paperroute/deploy/            the kit's copy
/usr/local/share/paperroute/apps/<app>/        the app's deploy/ copy, with timers and ready
/usr/local/share/paperroute/active-app         one line: the active app
/usr/local/share/paperroute/units-contract     the units contract
/usr/local/sbin/paperroute-<app>-<tool>        each app's root tools
/var/cache/paperroute-stage/tiles/sydney/      Sydney's staged tiles
```

### Upgrade

When the release changes the systemd units, or when moving from a
single-app install:

1. Run `scripts/deploy <user>@paperroute.local sydney`.
2. Straight away, run `scripts/provision <user>@paperroute.local sydney`,
   without `--reboot`.

Do not reboot between the two. When the units do not change, step 1 alone is
enough.

On a single-app install, the provision moves the releases and
/var/lib/paperroute into the app's own directories and restarts the board.

A release whose units contract differs from the installed one is not
switched back to; fix forward and redeploy.

After any rollback across this upgrade, restore the state.json backup: an
older release sets a newer file aside and starts with defaults.

For a card imaged for another country, set the Wi-Fi country on the Pi:

    ssh -t <user>@paperroute.local sudo raspi-config nonint do_wifi_country GB

A provision that changes an app timer's calendar drops that timer's zone
drop-in; the next deploy writes it again.

Each app keeps its own state file; provisioning another app leaves the first
untouched.

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
`/opt/paperroute/data/state.json`. A new device starts on Town Hall's map.

The timetable refreshes nightly at 03:30 Sydney time. If refreshes keep
failing, the board's banner says "Timetable ends <weekday>" from three days
before it runs out.

## Data

Timetable and real-time data come from Transport for NSW Open Data under
[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). The recorded
feeds and timetable slices in `tests/fixtures/real` are cut down from that
data, and some of their stops are edited.

Bus and coach data: contains public sector information licensed under the
[Open Government Licence v3.0](https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/).
The recorded feeds and timetable in `apps/paperroute-cambridge/tests/fixtures/real`
are cut down from it by `scripts/record_bods_fixtures.py`.

Map data © [OpenStreetMap](https://www.openstreetmap.org/copyright)
contributors, available under the
[Open Database License (ODbL)](https://opendatacommons.org/licenses/odbl/).
