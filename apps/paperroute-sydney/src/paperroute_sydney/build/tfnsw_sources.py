"""Sydney's timetable zips and how to fetch them from TfNSW."""

import http.client
from collections.abc import Callable
from pathlib import Path
from typing import Final
from urllib.request import Request

from paperroute_device.adapters.rate_limit import SyncStartLimiter
from paperroute_sydney.adapters.tfnsw import BASE_URL, api_key_from_env
from paperroute_transit.build.source_state import SourceError, SourceState
from paperroute_transit.build.sources import SourceSpec, open_source, stream_to

SYDNEY_SOURCES: Final[tuple[SourceSpec, ...]] = (
    SourceSpec(name='sydneytrains', path='v1/gtfs/schedule/sydneytrains'),
    SourceSpec(name='metro', path='v2/gtfs/schedule/metro'),
    SourceSpec(
        name='bundle',
        path='v1/publictransport/timetables/complete/gtfs',
    ),
)


class TfnswDownloader:
    """
    Fetch timetable zips from the TfNSW Open Data API.

    Parameters
    ----------
    key : Callable[[], str]
        Gives the API key; called for every request, so a key changed on
        disk is used from the next request on.
    base_url : str
        API root, ending in '/'.
    """

    def __init__(
        self,
        *,
        key: Callable[[], str] = api_key_from_env,
        base_url: str = BASE_URL,
    ) -> None:
        self.key = key
        self.base_url = base_url

    def request(self, *, spec: SourceSpec, method: str) -> Request:
        """
        Build an authorised request for a source.

        The key is an unredirected header, so a redirect to another host
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
        built.add_unredirected_header('Authorization', f'apikey {self.key()}')
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
            On an HTTP error, a short body or a write failure. The message
            holds the source name and the status or error class only.
        """
        built = self.request(spec=spec, method='GET')
        try:
            with open_source(
                built=built,
                name=spec.name,
                timeout_s=timeout_s,
                limiter=limiter,
            ) as response:
                return stream_to(response=response, out=out)
        except (http.client.HTTPException, OSError) as error:
            message = f'{spec.name}: {type(error).__name__}'
            raise SourceError(message) from error
