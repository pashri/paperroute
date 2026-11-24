"""Tests for refresh: heads."""

from pathlib import Path

from paperroute_device.adapters.rate_limit import SyncStartLimiter
from paperroute_sydney.city import SYDNEY_REF
from paperroute_transit.build import refresh as refresh_module
from paperroute_transit.build.source_state import SourceError, SourceState
from paperroute_transit.build.sources import BuildParts, SourceSpec


class RecordingDownloader:
    """A downloader that records the sources it is asked about."""

    def __init__(self) -> None:
        self.heads: list[str] = []
        self.failing: set[str] = set()

    def head(
        self,
        *,
        spec: SourceSpec,
        timeout_s: float,
        limiter: SyncStartLimiter | None,
    ) -> SourceState:
        """
        Record a HEAD and give fixed validators.

        Parameters
        ----------
        spec : SourceSpec
            Source.
        timeout_s : float
            Socket timeout in seconds.
        limiter : SyncStartLimiter | None
            Spaces out request starts.

        Returns
        -------
        SourceState
            Validators naming the source.

        Raises
        ------
        SourceError
            For a source in failing.
        """
        assert timeout_s > 0
        assert limiter is refresh_module.LIMITER
        self.heads.append(spec.name)
        if spec.name in self.failing:
            raise SourceError(f'{spec.name}: HTTP 404')
        return SourceState(etag=spec.name, last_modified=None, sha256=None)

    def download(
        self,
        *,
        spec: SourceSpec,
        out: Path,
        timeout_s: float,
        limiter: SyncStartLimiter | None,
    ) -> str:
        """
        Refuse to download.

        Parameters
        ----------
        spec : SourceSpec
            Source.
        out : Path
            File to write.
        timeout_s : float
            Socket timeout in seconds.
        limiter : SyncStartLimiter | None
            Spaces out request starts.

        Raises
        ------
        AssertionError
            Always; the HEAD test never downloads.
        """
        raise AssertionError(f'{spec.name} {out} {timeout_s} {limiter}')


def test_head_all_asks_the_downloader_per_source() -> None:
    """Each source is asked about once, in the parts' order."""
    recording = RecordingDownloader()
    sources = (
        SourceSpec(name='c', path='z'),
        SourceSpec(name='a', path='x'),
        SourceSpec(name='b', path='y'),
    )
    fresh = refresh_module.head_all(
        parts=BuildParts(
            city_ref=SYDNEY_REF,
            sources=sources,
            downloader=recording,
        ),
    )
    assert recording.heads == ['c', 'a', 'b']
    assert list(fresh) == ['c', 'a', 'b']
    assert fresh['a'] is not None and fresh['a'].etag == 'a'


def test_head_all_gives_none_for_a_failed_source() -> None:
    """A failed HEAD is None and the next source is still asked."""
    recording = RecordingDownloader()
    recording.failing.add('a')
    fresh = refresh_module.head_all(
        parts=BuildParts(
            city_ref=SYDNEY_REF,
            sources=(
                SourceSpec(name='a', path='x'),
                SourceSpec(name='b', path='y'),
            ),
            downloader=recording,
        ),
    )
    assert recording.heads == ['a', 'b']
    assert fresh['a'] is None
    assert fresh['b'] is not None


def test_read_meta_of_missing_file_is_empty(tmp_path: Path) -> None:
    """An unreadable timetable has no meta."""
    assert not refresh_module.read_meta(path=tmp_path / 'missing.db')
