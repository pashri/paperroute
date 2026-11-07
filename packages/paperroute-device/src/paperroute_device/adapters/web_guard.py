"""Which requests the device's web server answers."""

import ipaddress
from typing import Final
from urllib.parse import urlsplit

from aiohttp import web

HOSTNAMES: Final[frozenset[str]] = frozenset({'paperroute.local', 'localhost'})


def is_local(*, remote: str | None) -> bool:
    """
    Check whether an address is private or loopback.

    Parameters
    ----------
    remote : str | None
        An IP address in text form.

    Returns
    -------
    bool
        False for a missing or unparsable address.
    """
    try:
        address = ipaddress.ip_address(remote or '')
    except ValueError:
        return False
    return address.is_private or address.is_loopback


def known_host(*, request: web.Request) -> bool:
    """
    Check the Host header names this device.

    Parameters
    ----------
    request : web.Request
        The incoming request.

    Returns
    -------
    bool
        True for `paperroute.local`, `localhost` or an IP address
        literal (IPv6 in brackets), with or without a port.
    """
    try:
        hostname = urlsplit(f'//{request.host}').hostname
    except ValueError:
        return False
    if hostname in HOSTNAMES:
        return True
    try:
        ipaddress.ip_address(hostname or '')
    except ValueError:
        return False
    return True


def cross_site(*, request: web.Request) -> bool:
    """
    Check whether a POST names a different origin from its own host.

    Parameters
    ----------
    request : web.Request
        The incoming request.

    Returns
    -------
    bool
        True if an `Origin` header is present and its host differs
        from the `Host` header.
    """
    origin = request.headers.get('Origin')
    if origin is None:
        return False
    return urlsplit(origin).netloc != request.host
