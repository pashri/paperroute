"""Scan, join and share Wi-Fi through NetworkManager's nmcli."""

import asyncio
import logging
import secrets
from collections.abc import Callable
from pathlib import Path
from typing import Final

from paperroute_device.core.device_model import (
    JoinOutcome,
    JoinRequest,
    WifiNetwork,
    WifiStatus,
)
from paperroute_device.core.wifi_text import SETUP_SSID

logger = logging.getLogger(__name__)

NMCLI: Final[str] = '/usr/bin/nmcli'
IFACE: Final[str] = 'wlan0'
HOTSPOT_ADDRESS: Final[str] = '10.42.0.1'
JOIN_WAIT_S: Final[int] = 30
COMMAND_TIMEOUT_S: Final[float] = 60.0
PASSWORD_ALPHABET: Final[str] = '23456789ABCDEFGHJKLMNPQRSTUVWXYZ'
PASSWORD_LENGTH: Final[int] = 8
SSID_MAX_BYTES: Final[int] = 32
WPA_MIN: Final[int] = 8
WPA_MAX: Final[int] = 63
FORBIDDEN: Final[frozenset[str]] = frozenset({'\x00', '\n', '\r'})
WIFI_TYPE: Final[str] = '802-11-wireless'
PSK_HEX_LENGTH: Final[int] = 64
HEX_DIGITS: Final[frozenset[str]] = frozenset('0123456789abcdefABCDEF')
HELP_WORDS: Final[frozenset[str]] = frozenset(
    {'help', '-h', '--help', '-help'},
)


def hotspot_password(
    *, choose: Callable[[str], str] = secrets.choice,
) -> str:
    """
    Make a hotspot password from unambiguous letters and digits.

    Parameters
    ----------
    choose : Callable[[str], str]
        Picks one character from the alphabet.

    Returns
    -------
    str
        PASSWORD_LENGTH characters from PASSWORD_ALPHABET.
    """
    return ''.join(
        choose(PASSWORD_ALPHABET) for _ in range(PASSWORD_LENGTH)
    )


def split_terse(*, line: str) -> list[str]:
    """
    Split one line of ``nmcli -t`` output into its fields.

    Colons inside a field are written ``\\:`` and backslashes ``\\\\``.

    Parameters
    ----------
    line : str
        One output line.

    Returns
    -------
    list[str]
        The fields, unescaped.
    """
    fields = ['']
    chars = iter(line)
    for char in chars:
        if char == '\\':
            fields[-1] += next(chars, '')
        elif char == ':':
            fields.append('')
        else:
            fields[-1] += char
    return fields


def scan_row(*, line: str) -> WifiNetwork | None:
    """
    Read one SSID,SIGNAL,SECURITY row.

    Parameters
    ----------
    line : str
        One terse output line.

    Returns
    -------
    WifiNetwork | None
        The network; None for a blank SSID, the setup hotspot or a
        malformed row.
    """
    match split_terse(line=line):
        case [ssid, signal, security] if signal.isdigit():
            pass
        case _:
            return None
    if not ssid or ssid == SETUP_SSID:
        return None
    return WifiNetwork(
        ssid=ssid,
        signal=int(signal),
        secured=security not in ('', '--'),
        enterprise='802.1X' in security,
    )


def parse_scan(*, text: str) -> tuple[WifiNetwork, ...]:
    """
    Read a scan's networks, one per SSID.

    Parameters
    ----------
    text : str
        ``nmcli -t -f SSID,SIGNAL,SECURITY device wifi list`` output.

    Returns
    -------
    tuple[WifiNetwork, ...]
        The strongest row of each SSID, by signal descending then SSID.
    """
    best: dict[str, WifiNetwork] = {}
    for line in text.splitlines():
        network = scan_row(line=line)
        if network is None:
            continue
        seen = best.get(network.ssid)
        if seen is None or network.signal > seen.signal:
            best[network.ssid] = network
    return tuple(
        sorted(best.values(), key=lambda n: (-n.signal, n.ssid)),
    )


def join_outcome(*, code: int, output: str) -> JoinOutcome:
    """
    Say how a ``device wifi connect`` ended.

    Parameters
    ----------
    code : int
        Exit code from nmcli.
    output : str
        Its output.

    Returns
    -------
    JoinOutcome
        JOINED on exit 0, else from the error message.
    """
    if code == 0:
        return JoinOutcome.JOINED
    if 'Secrets were required' in output:
        return JoinOutcome.WRONG_PASSWORD
    if 'No network with SSID' in output:
        return JoinOutcome.NOT_FOUND
    return JoinOutcome.FAILED


def valid_text(*, text: str) -> bool:
    """
    Say whether text has no NUL or line breaks.

    Parameters
    ----------
    text : str
        SSID or password.

    Returns
    -------
    bool
        True when none of the forbidden characters is in it.
    """
    return not FORBIDDEN & set(text)


def valid_ssid(*, ssid: str) -> bool:
    """
    Check an SSID can be passed to nmcli.

    nmcli reads a lone ``help`` or ``-h`` as a request for help and
    exits 0, which would look like a join.

    Parameters
    ----------
    ssid : str
        Network name.

    Returns
    -------
    bool
        True for 1 to 32 bytes without NUL or line breaks that isn't a
        help word.
    """
    return (
        0 < len(ssid.encode()) <= SSID_MAX_BYTES
        and valid_text(text=ssid)
        and ssid.lower() not in HELP_WORDS
    )


def valid_password(*, password: str) -> bool:
    """
    Check a password is one WPA2 takes.

    Parameters
    ----------
    password : str
        Password; empty for an open network.

    Returns
    -------
    bool
        True when empty, 8 to 63 printable ASCII characters, or 64 hex
        digits.
    """
    if len(password) == PSK_HEX_LENGTH:
        return set(password) <= HEX_DIGITS
    return not password or (
        WPA_MIN <= len(password) <= WPA_MAX
        and all(' ' <= char <= '~' for char in password)
    )


def valid_request(*, request: JoinRequest) -> bool:
    """
    Check an SSID and password can be passed to nmcli.

    Parameters
    ----------
    request : JoinRequest
        The network asked for.

    Returns
    -------
    bool
        True when both the SSID and the password are valid.
    """
    return valid_ssid(ssid=request.ssid) and valid_password(
        password=request.password,
    )


def wifi_uuids(*, text: str) -> frozenset[str]:
    """
    Pick the Wi-Fi profiles out of a UUID,TYPE listing.

    Parameters
    ----------
    text : str
        ``nmcli -t -f UUID,TYPE connection show`` output.

    Returns
    -------
    frozenset[str]
        UUIDs of the 802-11-wireless profiles.
    """
    rows = [split_terse(line=line) for line in text.splitlines()]
    return frozenset(
        row[0] for row in rows if len(row) == 2 and row[1] == WIFI_TYPE
    )


def read_dbm(*, wireless: Path, iface: str) -> int | None:
    """
    Read an interface's signal level from ``/proc/net/wireless``.

    Parameters
    ----------
    wireless : Path
        The wireless status file.
    iface : str
        Interface name.

    Returns
    -------
    int | None
        The level in dBm; None without the file or the interface.
    """
    try:
        text = wireless.read_text(encoding='utf-8')
    except OSError:
        return None
    for line in text.splitlines():
        fields = line.split()
        if len(fields) > 3 and fields[0] == f'{iface}:':
            level = fields[3].rstrip('.')
            return int(level) if level.lstrip('-').isdigit() else None
    return None


def connect_argv(*, request: JoinRequest, iface: str) -> list[str]:
    """
    The nmcli arguments that join a network.

    Parameters
    ----------
    request : JoinRequest
        The network asked for.
    iface : str
        Interface name.

    Returns
    -------
    list[str]
        Arguments, without the program.
    """
    argv = ['--wait', str(JOIN_WAIT_S), 'device', 'wifi', 'connect']
    argv.append(request.ssid)
    if request.password:
        argv += ['password', request.password]
    argv += ['ifname', iface]
    if request.hidden:
        argv += ['hidden', 'yes']
    return argv


class NullWifi:
    """A Wi-Fi control that never acts, for development machines."""

    def __init__(self) -> None:
        self.logged = False

    async def status(self) -> WifiStatus:
        """
        Report an unmanaged link.

        Returns
        -------
        WifiStatus
            Not managed.
        """
        return WifiStatus()

    async def scan(self) -> tuple[WifiNetwork, ...]:
        """
        Find nothing.

        Returns
        -------
        tuple[WifiNetwork, ...]
            Empty.
        """
        return ()

    async def start_hotspot(self, *, password: str) -> bool:
        """
        Refuse, saying so the first time.

        Parameters
        ----------
        password : str
            Ignored.

        Returns
        -------
        bool
            Always False.
        """
        del password
        if not self.logged:
            logger.warning('wifi control not enabled')
            self.logged = True
        return False

    async def stop_hotspot(self) -> None:
        """Do nothing."""

    async def join(self, *, request: JoinRequest) -> JoinOutcome:
        """
        Refuse.

        Parameters
        ----------
        request : JoinRequest
            Ignored.

        Returns
        -------
        JoinOutcome
            Always FAILED.
        """
        del request
        return JoinOutcome.FAILED

    async def forget_all(self) -> None:
        """Do nothing."""

    async def active_wifi(self) -> str | None:
        """
        Find nothing.

        Returns
        -------
        str | None
            None.
        """
        return None

    async def reconnect(self, *, uuid: str) -> None:
        """
        Do nothing.

        Parameters
        ----------
        uuid : str
            Ignored.
        """
        del uuid

    async def hotspot_saved(self) -> bool:
        """
        Find nothing.

        Returns
        -------
        bool
            False.
        """
        return False


class NmcliWifi:
    """
    Drive Wi-Fi with nmcli, one fixed argument list per action.

    The app's user is allowed these by a polkit rule. Passwords go to
    nmcli as arguments and never into a log record.

    Parameters
    ----------
    nmcli : str
        The nmcli program.
    iface : str
        Wi-Fi interface.
    wireless : Path
        The wireless status file the signal level is read from.
    """

    def __init__(
        self,
        *,
        nmcli: str = NMCLI,
        iface: str = IFACE,
        wireless: Path = Path('/proc/net/wireless'),
    ) -> None:
        self.nmcli = nmcli
        self.iface = iface
        self.wireless = wireless

    async def run(self, *args: str, verb: str) -> tuple[int, str] | None:
        """
        Run nmcli and wait for it.

        Parameters
        ----------
        *args : str
            Arguments, without the program.
        verb : str
            What the run is for, for the logs.

        Returns
        -------
        tuple[int, str] | None
            Exit code and combined output; None when nmcli could not
            start or ran out of time. A cancelled run kills nmcli.
        """
        process = await self.started(*args, verb=verb)
        if process is None:
            return None
        try:
            output, _ = await asyncio.wait_for(
                process.communicate(), timeout=COMMAND_TIMEOUT_S,
            )
        except TimeoutError:
            logger.error('nmcli timed out', extra={'verb': verb})
            return None
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()
        return process.returncode or 0, output.decode(errors='replace')

    async def started(
        self, *args: str, verb: str,
    ) -> asyncio.subprocess.Process | None:
        """
        Start nmcli with its output piped.

        Parameters
        ----------
        *args : str
            Arguments, without the program.
        verb : str
            What the run is for, for the logs.

        Returns
        -------
        asyncio.subprocess.Process | None
            The process; None when nmcli could not start.
        """
        try:
            return await asyncio.create_subprocess_exec(
                self.nmcli,
                *args,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
        except OSError:
            logger.info('nmcli could not start', extra={'verb': verb})
            return None

    async def status(self) -> WifiStatus:
        """
        Report the joined network, signal and address.

        Returns
        -------
        WifiStatus
            Not managed when nmcli can't run or fails.
        """
        listed = await self.run(
            '-t', '-f', 'ACTIVE,SSID', 'device', 'wifi', 'list',
            '--rescan', 'no',
            verb='status',
        )
        if listed is None or listed[0] != 0:
            return WifiStatus(managed=False)
        rows = [split_terse(line=line) for line in listed[1].splitlines()]
        ssid = next(
            (row[1] for row in rows if len(row) == 2 and row[0] == 'yes'),
            None,
        )
        return WifiStatus(
            managed=True,
            ssid=ssid or None,
            dbm=read_dbm(wireless=self.wireless, iface=self.iface),
            ip=await self.address(),
        )

    async def address(self) -> str | None:
        """
        Read the interface's IPv4 address.

        Returns
        -------
        str | None
            The address without its prefix length, or None.
        """
        shown = await self.run(
            '-g', 'IP4.ADDRESS', 'device', 'show', self.iface,
            verb='address',
        )
        if shown is None or shown[0] != 0:
            return None
        first = shown[1].strip().split('|')[0]
        return first.split('/')[0] or None

    async def scan(self) -> tuple[WifiNetwork, ...]:
        """
        Rescan and list the networks in range.

        Returns
        -------
        tuple[WifiNetwork, ...]
            Strongest first; empty when the scan failed.
        """
        listed = await self.run(
            '-t', '-f', 'SSID,SIGNAL,SECURITY', 'device', 'wifi', 'list',
            '--rescan', 'yes',
            verb='scan',
        )
        if listed is None or listed[0] != 0:
            logger.info('wifi scan failed')
            return ()
        return parse_scan(text=listed[1])

    async def start_hotspot(self, *, password: str) -> bool:
        """
        Share a WPA2 hotspot named SETUP_SSID on the interface.

        Parameters
        ----------
        password : str
            Hotspot password.

        Returns
        -------
        bool
            True when nmcli exited 0.
        """
        ran = await self.run(
            'device', 'wifi', 'hotspot', 'ifname', self.iface,
            'con-name', SETUP_SSID, 'ssid', SETUP_SSID,
            'band', 'bg', 'password', password,
            verb='hotspot',
        )
        started = ran is not None and ran[0] == 0
        if not started:
            logger.error('hotspot did not start')
        return started

    async def stop_hotspot(self) -> None:
        """Take the hotspot down and delete its profile."""
        for action in ('down', 'delete'):
            ran = await self.run(
                'connection', action, 'id', SETUP_SSID, verb=action,
            )
            if ran is None or ran[0] != 0:
                logger.info('hotspot %s failed', action)

    async def join(self, *, request: JoinRequest) -> JoinOutcome:
        """
        Join a network; delete the profile it made unless it joined.

        Profiles that were there before are kept, whatever their name.

        Parameters
        ----------
        request : JoinRequest
            Network name, password and whether it is hidden.

        Returns
        -------
        JoinOutcome
            JOINED only once the status shows the SSID active; FAILED
            without running nmcli for an SSID or password nmcli can't
            take.

        Raises
        ------
        asyncio.CancelledError
            When cancelled, after the new profile is deleted.
        """
        if not valid_request(request=request):
            logger.warning('wifi join refused: bad ssid or password')
            return JoinOutcome.FAILED
        # Callers serialise joins and hotspot starts; the diff assumes it.
        before = await self.profiles()
        try:
            outcome = await self.connect(request=request)
        except asyncio.CancelledError:
            await asyncio.shield(self.clean_up(before=before))
            raise
        logger.info(
            'wifi join', extra={'ssid': request.ssid, 'outcome': outcome},
        )
        if outcome is not JoinOutcome.JOINED:
            await self.clean_up(before=before)
        return outcome

    async def connect(self, *, request: JoinRequest) -> JoinOutcome:
        """
        Run the connect and confirm it by the status.

        nmcli exits 0 for some arguments it reads as options or help,
        so a success counts only when the SSID is then active.

        Parameters
        ----------
        request : JoinRequest
            A valid request.

        Returns
        -------
        JoinOutcome
            How the attempt ended.
        """
        ran = await self.run(
            *connect_argv(request=request, iface=self.iface), verb='join',
        )
        if ran is None:
            return JoinOutcome.FAILED
        outcome = join_outcome(code=ran[0], output=ran[1])
        if outcome is JoinOutcome.JOINED:
            status = await self.status()
            if status.ssid != request.ssid:
                return JoinOutcome.FAILED
        return outcome

    async def clean_up(self, *, before: frozenset[str] | None) -> None:
        """
        Delete the Wi-Fi profiles made since a listing.

        Parameters
        ----------
        before : frozenset[str] | None
            UUIDs listed before; None deletes nothing.
        """
        if before is None:
            return
        after = await self.profiles()
        for uuid in sorted((after or frozenset()) - before):
            await self.delete(uuid=uuid)

    async def profiles(self) -> frozenset[str] | None:
        """
        List the saved Wi-Fi profiles.

        Returns
        -------
        frozenset[str] | None
            Their UUIDs; None when they could not be listed.
        """
        shown = await self.run(
            '-t', '-f', 'UUID,TYPE', 'connection', 'show', verb='profiles',
        )
        if shown is None or shown[0] != 0:
            logger.error('wifi profiles could not be listed')
            return None
        return wifi_uuids(text=shown[1])

    async def delete(self, *, uuid: str) -> None:
        """
        Delete a connection profile.

        Parameters
        ----------
        uuid : str
            Profile UUID.
        """
        ran = await self.run(
            'connection', 'delete', 'uuid', uuid, verb='forget',
        )
        if ran is None or ran[0] != 0:
            logger.info('profile delete failed', extra={'uuid': uuid})

    async def forget_all(self) -> None:
        """Delete every saved Wi-Fi profile, keeping wired ones."""
        for uuid in sorted(await self.profiles() or ()):
            await self.delete(uuid=uuid)

    async def active_wifi(self) -> str | None:
        """
        Find the Wi-Fi connection active on the interface.

        Returns
        -------
        str | None
            Its UUID; None with none active or when nmcli fails.
        """
        shown = await self.run(
            '-t', '-f', 'UUID,TYPE,DEVICE', 'connection', 'show', '--active',
            verb='active',
        )
        if shown is None or shown[0] != 0:
            return None
        rows = [split_terse(line=line) for line in shown[1].splitlines()]
        return next(
            (
                row[0]
                for row in rows
                if len(row) == 3 and row[1:] == [WIFI_TYPE, self.iface]
            ),
            None,
        )

    async def reconnect(self, *, uuid: str) -> None:
        """
        Bring a saved connection up again; a failure is only logged.

        Parameters
        ----------
        uuid : str
            The connection's UUID.
        """
        ran = await self.run('connection', 'up', 'uuid', uuid, verb='up')
        if ran is None or ran[0] != 0:
            logger.info('wifi reconnect failed', extra={'uuid': uuid})

    async def hotspot_saved(self) -> bool:
        """
        Say whether a profile named SETUP_SSID exists.

        Returns
        -------
        bool
            True when one is listed; False when nmcli fails.
        """
        shown = await self.run(
            '-t', '-f', 'NAME', 'connection', 'show', verb='saved',
        )
        if shown is None or shown[0] != 0:
            return False
        names = [split_terse(line=line) for line in shown[1].splitlines()]
        return [SETUP_SSID] in names
