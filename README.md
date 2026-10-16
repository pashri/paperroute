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
address in its place.

### Install

1. Provision, in your own terminal. Type the Pi password once at the sudo
   prompt.

       scripts/provision <user>@paperroute.local --reboot

2. Put the TfNSW API key on the Pi without printing it.

       ssh <user>@paperroute.local 'umask 077; cat > ~/tfnsw_api_key.tmp' < ~/tfnsw_api_key
       ssh -t <user>@paperroute.local 'sudo install -m 600 -o paperroute -g paperroute ~/tfnsw_api_key.tmp /var/lib/paperroute/secrets && rm ~/tfnsw_api_key.tmp'

3. Deploy. It never prompts.

       scripts/deploy <user>@paperroute.local

4. Build the timetable. This takes about 17 minutes on a Zero 2 W, and the
   app starts by itself when it finishes.

       ssh -t <user>@paperroute.local 'sudo systemctl start --no-block paperroute-timetable'
       ssh <user>@paperroute.local journalctl -fu paperroute-timetable

5. Open `http://paperroute.local`, or the Pi's IP address.

### Green LED

- Fast blink: booting, until the first frame.
- Steady: running.
- Slow blink: rebuilding the timetable.

### Pin and refresh

The pin is `PAPERROUTE_FROM` and `PAPERROUTE_TO` in the root-owned
`/etc/paperroute/m2a.env`, defaulting to the 606. To change it, edit the file
with `sudo`, then `sudo systemctl restart paperroute`.

The timetable refreshes nightly at 03:30 Sydney time.
