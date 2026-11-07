"""Check, download and record the TfNSW timetable zips."""

import hashlib
import http.client
import json
import os
from dataclasses import asdict, dataclass
from functools import partial
from pathlib import Path
from typing import Final
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from paperroute_device.adapters.rate_limit import SyncStartLimiter

CHUNK_BYTES: Final[int] = 1 << 20


@dataclass(frozen=True)
class SourceSpec:
    """One timetable zip and its API path."""

    name: str
    path: str


SOURCES: Final[tuple[SourceSpec, ...]] = (
    SourceSpec(name='sydneytrains', path='v1/gtfs/schedule/sydneytrains'),
    SourceSpec(name='metro', path='v2/gtfs/schedule/metro'),
    SourceSpec(
        name='bundle',
        path='v1/publictransport/timetables/complete/gtfs',
    ),
)


@dataclass(frozen=True)
class SourceState:
    """A source's validators and the SHA-256 of its last zip."""

    etag: str | None
    last_modified: str | None
    sha256: str | None


class SourceError(Exception):
    """A source couldn't be checked or downloaded."""


def load_states(*, path: Path) -> dict[str, SourceState]:
    """
    Read the stored source states.

    Parameters
    ----------
    path : Path
        JSON file of states by source name.

    Returns
    -------
    dict[str, SourceState]
        States by source name; empty when the file is missing.
    """
    if not path.exists():
        return {}
    raw = json.loads(path.read_text(encoding='utf-8'))
    return {name: SourceState(**fields) for name, fields in raw.items()}


def save_states(*, path: Path, states: dict[str, SourceState]) -> None:
    """
    Write the source states atomically.

    Parameters
    ----------
    path : Path
        JSON file to replace.
    states : dict[str, SourceState]
        States by source name.
    """
    scratch = path.with_name(f'{path.name}.tmp')
    raw = {name: asdict(state) for name, state in states.items()}
    with scratch.open('w', encoding='utf-8') as file:
        json.dump(raw, file, indent=2, sort_keys=True)
        file.flush()
        os.fsync(file.fileno())
    os.replace(scratch, path)


def request(
    *,
    base_url: str,
    spec: SourceSpec,
    api_key: str,
    method: str,
) -> Request:
    """
    Build an authorised request for a source.

    The key is an unredirected header, so a redirect to another host
    doesn't carry it.

    Parameters
    ----------
    base_url : str
        API root, ending in '/'.
    spec : SourceSpec
        Source.
    api_key : str
        TfNSW API key.
    method : str
        'HEAD' or 'GET'.

    Returns
    -------
    Request
        The request.
    """
    built = Request(base_url + spec.path, method=method)
    built.add_unredirected_header('Authorization', f'apikey {api_key}')
    return built


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


def head(
    *,
    base_url: str,
    spec: SourceSpec,
    api_key: str,
    timeout_s: float,
    limiter: SyncStartLimiter | None = None,
) -> SourceState:
    """
    Read a source's ETag and Last-Modified.

    Parameters
    ----------
    base_url : str
        API root, ending in '/'.
    spec : SourceSpec
        Source.
    api_key : str
        TfNSW API key.
    timeout_s : float
        Socket timeout in seconds.
    limiter : SyncStartLimiter | None
        Spaces out request starts; none means no limit.

    Returns
    -------
    SourceState
        The validators, with sha256 None.
    """
    built = request(
        base_url=base_url,
        spec=spec,
        api_key=api_key,
        method='HEAD',
    )
    with open_source(
        built=built, name=spec.name, timeout_s=timeout_s, limiter=limiter,
    ) as rsp:
        return SourceState(
            etag=rsp.headers.get('ETag'),
            last_modified=rsp.headers.get('Last-Modified'),
            sha256=None,
        )


def stream_to(*, response: http.client.HTTPResponse, out: Path) -> str:
    """
    Copy a response body to a file in chunks.

    Parameters
    ----------
    response : http.client.HTTPResponse
        Open response.
    out : Path
        File to write.

    Returns
    -------
    str
        SHA-256 hex digest of the body.

    Raises
    ------
    http.client.IncompleteRead
        If the body ends before its Content-Length.
    """
    digest = hashlib.sha256()
    with out.open('wb') as file:
        for chunk in iter(partial(response.read, CHUNK_BYTES), b''):
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


def fsync_dir(*, path: Path) -> None:
    """
    Flush a directory's entries, so renames into it survive a power cut.

    Parameters
    ----------
    path : Path
        Directory.
    """
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def download(
    *,
    base_url: str,
    spec: SourceSpec,
    api_key: str,
    out: Path,
    timeout_s: float,
    limiter: SyncStartLimiter | None = None,
) -> str:
    """
    Download a source's zip.

    Parameters
    ----------
    base_url : str
        API root, ending in '/'.
    spec : SourceSpec
        Source.
    api_key : str
        TfNSW API key.
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
        On an HTTP error, a short body or a write failure. The message
        holds the source name and the status or error class only.
    """
    built = request(
        base_url=base_url,
        spec=spec,
        api_key=api_key,
        method='GET',
    )
    try:
        with open_source(
            built=built,
            name=spec.name,
            timeout_s=timeout_s,
            limiter=limiter,
        ) as response:
            return stream_to(response=response, out=out)
    except (http.client.HTTPException, OSError) as error:
        raise SourceError(f'{spec.name}: {type(error).__name__}') from error


def needs_download(
    *,
    stored: SourceState | None,
    fresh: SourceState,
    zip_exists: bool,
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

    Returns
    -------
    bool
        True when there is no stored state or zip, the server sent
        neither validator, or either validator changed.
    """
    if stored is None or not zip_exists:
        return True
    if fresh.etag is None and fresh.last_modified is None:
        return True
    return (fresh.etag, fresh.last_modified) != (
        stored.etag,
        stored.last_modified,
    )
