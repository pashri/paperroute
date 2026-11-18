"""Describe, check and download a city's timetable zips."""

import hashlib
import http.client
import os
from collections.abc import Mapping
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Final, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from paperroute_device.adapters.rate_limit import SyncStartLimiter
from paperroute_transit.build.source_state import SourceError, SourceState

CHUNK_BYTES: Final[int] = 1 << 20


class SourceCut(Protocol):
    """Writes the part of a downloaded zip that a city keeps."""

    def __call__(self, *, archive: Path, out: Path) -> object:
        """
        Cut a downloaded zip.

        Parameters
        ----------
        archive : Path
            The downloaded zip.
        out : Path
            Where the cut goes.

        Returns
        -------
        object
            Whatever the city reports about the cut; unused.
        """


@dataclass(frozen=True)
class SourceSpec:
    """
    One timetable zip and where the downloader finds it.

    Attributes
    ----------
    name : str
        Source name.
    path : str
        Where the downloader finds the zip.
    cut : SourceCut | None
        Applied to the download, whose result is what is stored; none
        stores the download as it is.
    optional : bool
        When True, the build goes on without this source if it has no
        copy to build from.
    size_limit : int | None
        Largest download accepted, in bytes; None accepts any size.
    cut_rule : str | None
        A short stable string naming the rule the cut applies; a stored
        cut made under another rule is cut again. None skips the check.
    """

    name: str
    path: str
    cut: SourceCut | None = None
    optional: bool = False
    size_limit: int | None = None
    cut_rule: str | None = None


class DownloadTooLarge(Exception):
    """A download is larger than its source's size limit."""


def check_size(*, size: int, limit: int | None) -> None:
    """
    Refuse a size over a limit.

    Parameters
    ----------
    size : int
        Bytes downloaded or declared.
    limit : int | None
        Largest size accepted; none accepts any.

    Raises
    ------
    DownloadTooLarge
        If a limit is set and the size is over it.
    """
    if limit is not None and size > limit:
        raise DownloadTooLarge(f'{size} bytes is over {limit}')


class Downloader(Protocol):
    """Reads a source's validators and downloads its zip."""

    def head(
        self,
        *,
        spec: SourceSpec,
        timeout_s: float,
        limiter: SyncStartLimiter | None,
    ) -> SourceState:
        """
        Read a source's ETag and Last-Modified.

        Parameters
        ----------
        spec : SourceSpec
            Source.
        timeout_s : float
            Socket timeout in seconds.
        limiter : SyncStartLimiter | None
            Spaces out request starts; none means no limit.

        Returns
        -------
        SourceState
            The validators, with sha256 None.
        """

    def download(
        self,
        *,
        spec: SourceSpec,
        out: Path,
        timeout_s: float,
        limiter: SyncStartLimiter | None,
    ) -> str:
        """
        Download a source's zip.

        Parameters
        ----------
        spec : SourceSpec
            Source.
        out : Path
            File to write.
        timeout_s : float
            Socket timeout in seconds.
        limiter : SyncStartLimiter | None
            Spaces out request starts; none means no limit.

        Returns
        -------
        str
            SHA-256 hex digest of the zip.

        Raises
        ------
        SourceError
            On an HTTP error, a short body, an oversized body or a write
            failure. The message holds the source name and the status or
            error class only.
        """


@dataclass(frozen=True)
class BuildParts:
    """
    What a city plugs into the timetable build.

    Attributes
    ----------
    city_ref : str
        The city's reference, such as ``package.module:NAME``.
    sources : tuple[SourceSpec, ...]
        The timetable zips, preferred first, so their trips win the
        merge.
    downloader : Downloader
        Fetches the zips.
    """

    city_ref: str
    sources: tuple[SourceSpec, ...]
    downloader: Downloader


def open_source(
    *,
    built: Request,
    name: str,
    timeout_s: float,
    limiter: SyncStartLimiter | None = None,
) -> http.client.HTTPResponse:
    """
    Send a request, turning failures into SourceError.

    Parameters
    ----------
    built : Request
        Request to send.
    name : str
        Source name, for the error message.
    timeout_s : float
        Socket timeout in seconds.
    limiter : SyncStartLimiter | None
        Spaces out request starts; none means no limit.

    Returns
    -------
    http.client.HTTPResponse
        Open response.

    Raises
    ------
    SourceError
        On an HTTP error status or a connection failure. The message holds
        the source name and the status or error class only.
    """
    if limiter:
        limiter.wait()
    try:
        # pylint: disable-next=consider-using-with
        response: http.client.HTTPResponse = urlopen(
            built,
            timeout=timeout_s,
        )
    except HTTPError as error:
        error.close()
        raise SourceError(f'{name}: HTTP {error.code}') from error
    except (URLError, OSError, http.client.HTTPException) as error:
        raise SourceError(f'{name}: {type(error).__name__}') from error
    return response


def stream_to(
    *,
    response: http.client.HTTPResponse,
    out: Path,
    limit: int | None = None,
) -> str:
    """
    Copy a response body to a file in chunks.

    Parameters
    ----------
    response : http.client.HTTPResponse
        Open response.
    out : Path
        File to write.
    limit : int | None
        Largest body accepted, in bytes; none accepts any size.

    Returns
    -------
    str
        SHA-256 hex digest of the body.

    Raises
    ------
    http.client.IncompleteRead
        If the body ends before its Content-Length.
    DownloadTooLarge
        If the Content-Length or the bytes read pass the limit.
    """
    if response.length is not None:
        check_size(size=response.length, limit=limit)
    digest = hashlib.sha256()
    total = 0
    with out.open('wb') as file:
        for chunk in iter(partial(response.read, CHUNK_BYTES), b''):
            total += len(chunk)
            check_size(size=total, limit=limit)
            digest.update(chunk)
            file.write(chunk)
        file.flush()
        os.fsync(file.fileno())
    if response.length:
        raise http.client.IncompleteRead(partial=b'', expected=response.length)
    return digest.hexdigest()


def file_sha256(*, path: Path) -> str:
    """
    Hash a file in chunks.

    Parameters
    ----------
    path : Path
        File to read.

    Returns
    -------
    str
        SHA-256 hex digest.
    """
    digest = hashlib.sha256()
    with path.open('rb') as file:
        for chunk in iter(partial(file.read, CHUNK_BYTES), b''):
            digest.update(chunk)
    return digest.hexdigest()


def needs_download(
    *,
    stored: SourceState | None,
    fresh: SourceState,
    zip_exists: bool,
    rule_kept: bool = True,
) -> bool:
    """
    Whether the headers fail to show the stored zip is current.

    Parameters
    ----------
    stored : SourceState | None
        State saved after the last download.
    fresh : SourceState
        Validators from a new HEAD.
    zip_exists : bool
        Whether the stored zip is on disk.
    rule_kept : bool
        Whether the stored zip was cut by the source's current rule.

    Returns
    -------
    bool
        True when there is no stored state or zip, the stored zip was cut
        by another rule, the server sent neither validator, or either
        validator changed.
    """
    if stored is None or not zip_exists or not rule_kept:
        return True
    if fresh.etag is None and fresh.last_modified is None:
        return True
    return (fresh.etag, fresh.last_modified) != (
        stored.etag,
        stored.last_modified,
    )


class RequestHeaders(Protocol):
    """Gives the headers to send with a source's requests."""

    def __call__(self, *, spec: SourceSpec) -> Mapping[str, str]:
        """
        Give the headers.

        Parameters
        ----------
        spec : SourceSpec
            Source the request is for.

        Returns
        -------
        Mapping[str, str]
            Header names and values.
        """


def no_headers(*, spec: SourceSpec) -> Mapping[str, str]:
    """
    Give no headers.

    Parameters
    ----------
    spec : SourceSpec
        Source the request is for; unused.

    Returns
    -------
    Mapping[str, str]
        An empty mapping.
    """
    del spec
    return {}


class UrlDownloader:
    """
    Fetch timetable zips from a URL root.

    Parameters
    ----------
    base_url : str
        Root, ending in '/'; a source's path follows it.
    headers : RequestHeaders | None
        Gives the headers for each request, called for every one; none
        sends only the defaults.
    """

    def __init__(
        self,
        *,
        base_url: str,
        headers: RequestHeaders | None = None,
    ) -> None:
        self.base_url = base_url
        self.headers = no_headers if headers is None else headers

    def request(self, *, spec: SourceSpec, method: str) -> Request:
        """
        Build a request for a source.

        Each header is unredirected, so a redirect to another host
        doesn't carry it.

        Parameters
        ----------
        spec : SourceSpec
            Source.
        method : str
            'HEAD' or 'GET'.

        Returns
        -------
        Request
            The request.
        """
        built = Request(self.base_url + spec.path, method=method)
        for name, value in self.headers(spec=spec).items():
            built.add_unredirected_header(name, value)
        return built

    def head(
        self,
        *,
        spec: SourceSpec,
        timeout_s: float,
        limiter: SyncStartLimiter | None,
    ) -> SourceState:
        """
        Read a source's ETag and Last-Modified.

        Parameters
        ----------
        spec : SourceSpec
            Source.
        timeout_s : float
            Socket timeout in seconds.
        limiter : SyncStartLimiter | None
            Spaces out request starts; none means no limit.

        Returns
        -------
        SourceState
            The validators, with sha256 None.
        """
        with open_source(
            built=self.request(spec=spec, method='HEAD'),
            name=spec.name,
            timeout_s=timeout_s,
            limiter=limiter,
        ) as response:
            return SourceState(
                etag=response.headers.get('ETag'),
                last_modified=response.headers.get('Last-Modified'),
                sha256=None,
            )

    def download(
        self,
        *,
        spec: SourceSpec,
        out: Path,
        timeout_s: float,
        limiter: SyncStartLimiter | None,
    ) -> str:
        """
        Download a source's zip.

        Parameters
        ----------
        spec : SourceSpec
            Source.
        out : Path
            File to write.
        timeout_s : float
            Socket timeout in seconds.
        limiter : SyncStartLimiter | None
            Spaces out request starts; none means no limit.

        Returns
        -------
        str
            SHA-256 hex digest of the zip.

        Raises
        ------
        SourceError
            On an HTTP error, a short body, an oversized body or a write
            failure. The message holds the source name and the status or
            error class only.
        """
        built = self.request(spec=spec, method='GET')
        try:
            with open_source(
                built=built,
                name=spec.name,
                timeout_s=timeout_s,
                limiter=limiter,
            ) as response:
                return stream_to(
                    response=response,
                    out=out,
                    limit=spec.size_limit,
                )
        except (
            http.client.HTTPException,
            OSError,
            DownloadTooLarge,
        ) as error:
            out.unlink(missing_ok=True)
            message = f'{spec.name}: {type(error).__name__}'
            raise SourceError(message) from error
