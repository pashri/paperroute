"""Tests for driving Wi-Fi through nmcli."""

import secrets
from pathlib import Path

import pytest

from paperroute.adapters.wifi import (
    PASSWORD_ALPHABET,
    PASSWORD_LENGTH,
    NmcliWifi,
    NullWifi,
    hotspot_password,
    join_outcome,
    parse_scan,
    split_terse,
)
from paperroute.core.device_model import (
    JoinOutcome,
    JoinRequest,
    WifiNetwork,
    WifiStatus,
)
from tests.adapters.fake_nmcli import calls, make_nmcli

WRONG_PASSWORD_REPLY = (
    4,
    'Error: Connection activation failed: '
    'Secrets were required, but not provided.',
)


def wifi_on(
    *, tmp_path: Path, replies: dict[str, tuple[int, str]] | None = None,
) -> tuple[NmcliWifi, Path]:
    """
    Make an adapter running the stand-in.

    Parameters
    ----------
    tmp_path : Path
        Test folder.
    replies : dict[str, tuple[int, str]] | None
        Stand-in replies by argument prefix.

    Returns
    -------
    tuple[NmcliWifi, Path]
        The adapter, and the stand-in's log.
    """
    program, log = make_nmcli(tmp_path=tmp_path, replies=replies or {})
    wireless = tmp_path / 'wireless'
    return NmcliWifi(nmcli=str(program), wireless=wireless), log


def test_split_terse_unescapes() -> None:
    """Escaped colons and backslashes stay in their field."""
    assert split_terse(line='a\\:b:c\\\\:') == ['a:b', 'c\\', '']


def test_parse_scan() -> None:
    """Blank, own and weaker duplicates go; the rest sort by signal."""
    text = '\n'.join([
        'Home:80:WPA2',
        'Home:60:WPA2',
        'Cafe\\:Wi-Fi:40:',
        ':70:WPA2',
        'paperroute-setup:99:WPA2',
        'Corp:55:WPA2 802.1X',
    ])
    assert parse_scan(text=text) == (
        WifiNetwork(ssid='Home', signal=80, secured=True, enterprise=False),
        WifiNetwork(ssid='Corp', signal=55, secured=True, enterprise=True),
        WifiNetwork(
            ssid='Cafe:Wi-Fi', signal=40, secured=False, enterprise=False,
        ),
    )


def test_join_outcome() -> None:
    """Exit codes and messages map to outcomes."""
    assert join_outcome(code=0, output='') is JoinOutcome.JOINED
    assert join_outcome(code=10, output='Error: No network with SSID x') is (
        JoinOutcome.NOT_FOUND
    )
    assert join_outcome(code=1, output='odd') is JoinOutcome.FAILED


def test_hotspot_password_shape() -> None:
    """Eight characters, all from the alphabet."""
    password = hotspot_password()
    assert len(password) == PASSWORD_LENGTH == 8
    assert set(password) <= set(PASSWORD_ALPHABET)


def test_hotspot_password_uses_secrets() -> None:
    """The default chooser is secrets.choice."""
    defaults = hotspot_password.__kwdefaults__ or {}
    assert defaults['choose'] is secrets.choice
    assert hotspot_password(choose=lambda chars: chars[0]) == '22222222'


async def test_start_hotspot_argv(tmp_path: Path) -> None:
    """The hotspot is shared on wlan0 with the password given."""
    wifi, log = wifi_on(tmp_path=tmp_path)
    assert await wifi.start_hotspot(password='7Q4K9W2M')
    assert calls(log=log) == [[
        'device', 'wifi', 'hotspot', 'ifname', 'wlan0',
        'con-name', 'paperroute-setup', 'ssid', 'paperroute-setup',
        'band', 'bg', 'password', '7Q4K9W2M',
    ]]


async def test_start_hotspot_failure(tmp_path: Path) -> None:
    """A non-zero exit is a failed start."""
    wifi, _ = wifi_on(
        tmp_path=tmp_path, replies={'device wifi hotspot': (1, 'no')},
    )
    assert not await wifi.start_hotspot(password='7Q4K9W2M')


async def test_stop_hotspot_downs_and_deletes(tmp_path: Path) -> None:
    """The hotspot is taken down, then its profile deleted."""
    wifi, log = wifi_on(
        tmp_path=tmp_path, replies={'connection down': (10, 'gone')},
    )
    await wifi.stop_hotspot()
    assert calls(log=log) == [
        ['connection', 'down', 'id', 'paperroute-setup'],
        ['connection', 'delete', 'id', 'paperroute-setup'],
    ]


async def test_join_wrong_password(tmp_path: Path) -> None:
    """A refused password is reported and its profile deleted."""
    wifi, log = wifi_on(
        tmp_path=tmp_path, replies={'--wait': WRONG_PASSWORD_REPLY},
    )
    outcome = await wifi.join(
        request=JoinRequest(ssid='Home', password='hunter22'),
    )
    assert outcome is JoinOutcome.WRONG_PASSWORD
    assert calls(log=log) == [
        [
            '--wait', '30', 'device', 'wifi', 'connect', 'Home',
            'password', 'hunter22', 'ifname', 'wlan0',
        ],
        ['connection', 'delete', 'id', 'Home'],
    ]


async def test_join_hidden_and_open_argv(tmp_path: Path) -> None:
    """An open network has no password words; a hidden one says so."""
    wifi, log = wifi_on(tmp_path=tmp_path)
    outcome = await wifi.join(
        request=JoinRequest(ssid='Back room', hidden=True),
    )
    assert outcome is JoinOutcome.JOINED
    assert calls(log=log) == [[
        '--wait', '30', 'device', 'wifi', 'connect', 'Back room',
        'ifname', 'wlan0', 'hidden', 'yes',
    ]]


@pytest.mark.parametrize('ssid', ['', 'a' * 33, 'a\nb', 'a\x00b'])
async def test_join_refuses_bad_ssid(tmp_path: Path, ssid: str) -> None:
    """A bad SSID fails without running nmcli."""
    wifi, log = wifi_on(tmp_path=tmp_path)
    outcome = await wifi.join(request=JoinRequest(ssid=ssid))
    assert outcome is JoinOutcome.FAILED
    assert calls(log=log) == []


@pytest.mark.parametrize('password', ['short', 'a' * 64, 'abcdefgh\n'])
async def test_join_refuses_bad_password(
    tmp_path: Path, password: str,
) -> None:
    """A password that WPA2 can't take fails without running nmcli."""
    wifi, log = wifi_on(tmp_path=tmp_path)
    outcome = await wifi.join(
        request=JoinRequest(ssid='Home', password=password),
    )
    assert outcome is JoinOutcome.FAILED
    assert calls(log=log) == []


async def test_scan(tmp_path: Path) -> None:
    """A scan asks for a fresh list and parses it."""
    wifi, log = wifi_on(
        tmp_path=tmp_path,
        replies={'-t -f SSID': (0, 'Home:80:WPA2\n')},
    )
    assert await wifi.scan() == (
        WifiNetwork(ssid='Home', signal=80, secured=True, enterprise=False),
    )
    assert calls(log=log) == [[
        '-t', '-f', 'SSID,SIGNAL,SECURITY',
        'device', 'wifi', 'list', '--rescan', 'yes',
    ]]


async def test_forget_all_deletes_only_wifi(tmp_path: Path) -> None:
    """Wi-Fi profiles are deleted; wired ones are kept."""
    wifi, log = wifi_on(
        tmp_path=tmp_path,
        replies={
            '-t -f NAME,TYPE': (
                0, 'Home:802-11-wireless\nWired 1:802-3-ethernet\n',
            ),
        },
    )
    await wifi.forget_all()
    assert calls(log=log) == [
        ['-t', '-f', 'NAME,TYPE', 'connection', 'show'],
        ['connection', 'delete', 'id', 'Home'],
    ]


async def test_status_reads_ssid_ip_and_dbm(tmp_path: Path) -> None:
    """The joined SSID, wlan0's address and its signal in dBm."""
    wifi, log = wifi_on(
        tmp_path=tmp_path,
        replies={
            '-t -f ACTIVE,SSID': (0, 'no:Cafe\nyes:Home\n'),
            '-g IP4.ADDRESS': (0, '192.168.1.40/24\n'),
        },
    )
    (tmp_path / 'wireless').write_text(
        'Inter-| sta-|   Quality        |\n'
        ' face | tus | link level noise |\n'
        ' wlan0: 0000   70.  -52.  -256        0      0\n',
        encoding='utf-8',
    )
    assert await wifi.status() == WifiStatus(
        managed=True, ssid='Home', dbm=-52, ip='192.168.1.40',
    )
    assert calls(log=log) == [
        [
            '-t', '-f', 'ACTIVE,SSID', 'device', 'wifi', 'list',
            '--rescan', 'no',
        ],
        ['-g', 'IP4.ADDRESS', 'device', 'show', 'wlan0'],
    ]


async def test_status_without_link(tmp_path: Path) -> None:
    """Managed but not joined: no SSID, address or signal."""
    wifi, _ = wifi_on(tmp_path=tmp_path)
    assert await wifi.status() == WifiStatus(managed=True)


async def test_missing_nmcli_is_unmanaged(tmp_path: Path) -> None:
    """With no nmcli the Wi-Fi isn't managed and nothing fails."""
    wifi = NmcliWifi(nmcli=str(tmp_path / 'missing'))
    assert await wifi.status() == WifiStatus(managed=False)
    assert await wifi.scan() == ()
    assert not await wifi.start_hotspot(password='7Q4K9W2M')
    outcome = await wifi.join(request=JoinRequest(ssid='Home'))
    assert outcome is JoinOutcome.FAILED


async def test_passwords_never_logged(
    tmp_path: Path, caplog: pytest.LogCaptureFixture,
) -> None:
    """Neither the hotspot nor the Wi-Fi password reaches a log record."""
    caplog.set_level('DEBUG')
    wifi, _ = wifi_on(
        tmp_path=tmp_path,
        replies={
            'device wifi hotspot': (1, 'failed'),
            '--wait': WRONG_PASSWORD_REPLY,
        },
    )
    await wifi.start_hotspot(password='7Q4K9W2M')
    await wifi.join(request=JoinRequest(ssid='Home', password='hunter22'))
    assert caplog.records
    for record in caplog.records:
        seen = record.getMessage() + repr(record.__dict__)
        assert '7Q4K9W2M' not in seen
        assert 'hunter22' not in seen


def test_join_request_hides_password() -> None:
    """The password stays out of the request's repr."""
    assert 'hunter22' not in repr(JoinRequest(ssid='H', password='hunter22'))


async def test_null_wifi() -> None:
    """NullWifi reports nothing and never acts."""
    wifi = NullWifi()
    assert await wifi.status() == WifiStatus()
    assert await wifi.scan() == ()
    assert not await wifi.start_hotspot(password='7Q4K9W2M')
    await wifi.stop_hotspot()
    outcome = await wifi.join(request=JoinRequest(ssid='Home'))
    assert outcome is JoinOutcome.FAILED
    await wifi.forget_all()
