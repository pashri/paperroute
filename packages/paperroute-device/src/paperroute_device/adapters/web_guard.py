"""Which requests the device's web server answers."""

import ipaddress
from collections.abc import Callable
from http import HTTPStatus
from typing import Final
from urllib.parse import urlsplit

from aiohttp import web
from aiohttp.typedefs import Handler, Middleware

HOSTNAMES: Final[frozenset[str]] = frozenset({'paperroute.local', 'localhost'})

RemoteOf = Callable[[web.BaseRequest], str | None]


def peer_address(request: web.BaseRequest) -> str | None:
    """
    Get the address of the connected peer.

    Parameters
    ----------
    request : web.BaseRequest
        The incoming request.

    Returns
    -------
    str | None
        The peer's IP address, or None if it is unknown.
    """
    return request.remote


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


def local_only(*, remote_of: RemoteOf) -> Middleware:
    """
    Build a middleware that refuses peers outside the local network.

    It also refuses requests for an unknown host and cross-site POSTs.

    Parameters
    ----------
    remote_of : RemoteOf
        Finds the peer address of a request.

    Returns
    -------
    Middleware
        The middleware; it answers 403 or passes the request on.
    """

    @web.middleware
    async def guard(
        request: web.Request, handler: Handler,
    ) -> web.StreamResponse:
        """
        Refuse peers outside the local network and cross-site POSTs.

        Parameters
        ----------
        request : web.Request
            The incoming request.
        handler : Handler
            The route handler.

        Returns
        -------
        web.StreamResponse
            A 403 response, or the handler's response.
        """
        if not is_local(remote=remote_of(request)):
            return web.Response(status=HTTPStatus.FORBIDDEN)
        if not known_host(request=request):
            return web.Response(status=HTTPStatus.FORBIDDEN)
        if request.method == 'POST' and cross_site(request=request):
            return web.Response(status=HTTPStatus.FORBIDDEN)
        return await handler(request)

    return guard
