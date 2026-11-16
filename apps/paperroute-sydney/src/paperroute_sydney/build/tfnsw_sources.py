"""Sydney's timetable zips and how to fetch them from TfNSW."""

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Final
from urllib.request import Request

from paperroute_device.adapters.rate_limit import SyncStartLimiter
from paperroute_sydney.adapters.tfnsw import (
    BASE_URL,
    NoApiKey,
    api_key_from_env,
)
from paperroute_transit.build.source_state import SourceError, SourceState
from paperroute_transit.build.sources import SourceSpec, UrlDownloader

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
        self.plain = UrlDownloader(base_url=base_url, headers=self.headers)

    def headers(self, *, spec: SourceSpec) -> Mapping[str, str]:
        """
        Give the Authorization header for a source.

        Parameters
        ----------
        spec : SourceSpec
            Source.

        Returns
        -------
        Mapping[str, str]
            The header.

        Raises
        ------
        SourceError
            When the key can't be read; the message names the key file,
            never the key.
        """
        try:
            key = self.key()
        except NoApiKey as error:
            raise SourceError(f'{spec.name}: {error}') from error
        return {'Authorization': f'apikey {key}'}

    def request(self, *, spec: SourceSpec, method: str) -> Request:
        """
        Build an authorised request for a source.

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
        return self.plain.request(spec=spec, method=method)

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
        return self.plain.head(spec=spec, timeout_s=timeout_s, limiter=limiter)

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
        """
        return self.plain.download(
            spec=spec, out=out, timeout_s=timeout_s, limiter=limiter,
        )
