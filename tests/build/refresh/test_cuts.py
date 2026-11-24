"""Tests for refresh: cuts."""

import json
import struct
import sys
import zipfile
from dataclasses import replace
from pathlib import Path
from typing import Final

import pytest

from paperroute_sydney.city import SYDNEY
from paperroute_transit.build import refresh as refresh_module
from paperroute_transit.build.attempts import without_optional
from paperroute_transit.build.gtfs_files import Source
from paperroute_transit.build.handoff import Fetched, Handoff
from paperroute_transit.build.refresh import cut_into, refresh
from paperroute_transit.build.source_state import (
    SourceError,
    SourceState,
    load_states,
)
from paperroute_transit.build.sources import (
    BuildParts,
    SourceCut,
    SourceSpec,
    file_sha256,
)
from paperroute_transit.build.swap import RefreshReport
from paperroute_transit.build.timetable import Window, build_timetable
from paperroute_transit.core.city import MergeReport
from tests.build.feed_server import FeedServer, Served
from tests.build.feeds import CUT_COMMENT, commented, damaged, raising, refused
from tests.build.refresh.helpers import DAYS, metro_zip, parts_for

EXTRA: Final[str] = '/v2/gtfs/schedule/extra'


def extra_parts(
    *, server: FeedServer, cut: SourceCut, rule: str | None = None,
) -> BuildParts:
    """
    Sydney's build parts and one optional source, cut after download.

    Parameters
    ----------
    server : FeedServer
        Feed server.
    cut : SourceCut
        The extra source's cut.
    rule : str | None
        The extra source's cut rule.

    Returns
    -------
    BuildParts
        The parts.
    """
    parts = parts_for(server=server)
    extra = SourceSpec(
        name='extra', path=EXTRA.removeprefix('/'), cut=cut, optional=True,
        cut_rule=rule,
    )
    return replace(parts, sources=(*parts.sources, extra))


def serve_extra(
    *, server: FeedServer, tmp_path: Path, comment: bytes, etag: str = '"1"',
) -> None:
    """
    Serve a small feed as the extra source.

    Parameters
    ----------
    server : FeedServer
        Feed server.
    tmp_path : Path
        Directory for the zip.
    comment : bytes
        Zip comment, to change the bytes but not the contents.
    etag : str
        The ETag served.
    """
    server.state.files[EXTRA] = Served(
        body=metro_zip(tmp_path=tmp_path, comment=comment), etag=etag,
    )


def run_extra(
    *,
    server: FeedServer,
    data_dir: Path,
    cut: SourceCut = commented,
    force: bool = False,
    rule: str | None = None,
) -> RefreshReport:
    """
    Refresh Sydney's sources and the extra one.

    Parameters
    ----------
    server : FeedServer
        Feed server.
    data_dir : Path
        Data directory.
    cut : SourceCut
        The extra source's cut.
    force : bool
        Rebuild even when nothing changed.
    rule : str | None
        The extra source's cut rule.

    Returns
    -------
    RefreshReport
        The refresh's report.
    """
    return refresh(
        data_dir=data_dir,
        city=SYDNEY,
        parts=extra_parts(server=server, cut=cut, rule=rule),
        days=DAYS,
        force=force,
    )


def test_a_cut_source_stores_its_cut(
    server: FeedServer, data_dir: Path, tmp_path: Path,
) -> None:
    """The cut, not the download, is stored, under its own digest."""
    serve_extra(server=server, tmp_path=tmp_path, comment=b'one')
    report = run_extra(server=server, data_dir=data_dir)
    stored = data_dir / 'sources' / 'extra.zip'
    with zipfile.ZipFile(stored) as archive:
        assert archive.comment == CUT_COMMENT
    states = load_states(path=data_dir / 'sources' / 'sources.json')
    assert states['extra'].sha256 == file_sha256(path=stored)
    assert 'extra' in report.changed and report.rebuilt


def test_a_cut_stored_under_another_rule_is_cut_again(
    server: FeedServer, data_dir: Path, tmp_path: Path,
) -> None:
    """Unchanged validators do not keep a cut made by a different rule."""
    serve_extra(server=server, tmp_path=tmp_path, comment=b'one')
    run_extra(server=server, data_dir=data_dir, rule='A,B')
    report = run_extra(server=server, data_dir=data_dir, rule='A,C')
    assert 'extra' in report.downloaded
    rule = data_dir / 'sources' / 'extra.rule'
    assert rule.read_text(encoding='utf-8').startswith('A,C\n')


def test_a_rule_file_for_another_cut_is_not_trusted(
    server: FeedServer, data_dir: Path, tmp_path: Path,
) -> None:
    """The right rule beside a different cut's digest cuts again."""
    serve_extra(server=server, tmp_path=tmp_path, comment=b'one')
    run_extra(server=server, data_dir=data_dir, rule='A,B')
    rule = data_dir / 'sources' / 'extra.rule'
    rule.write_text('A,B\n' + 'ff' * 32 + '\n', encoding='utf-8')
    report = run_extra(server=server, data_dir=data_dir, rule='A,B')
    assert 'extra' in report.downloaded
    digest = file_sha256(path=data_dir / 'sources' / 'extra.zip')
    assert rule.read_text(encoding='utf-8') == f'A,B\n{digest}\n'


def test_a_one_line_rule_file_is_cut_again(
    server: FeedServer, data_dir: Path, tmp_path: Path,
) -> None:
    """A rule file with no digest is not trusted."""
    serve_extra(server=server, tmp_path=tmp_path, comment=b'one')
    run_extra(server=server, data_dir=data_dir, rule='A,B')
    rule = data_dir / 'sources' / 'extra.rule'
    rule.write_text('A,B', encoding='utf-8')
    report = run_extra(server=server, data_dir=data_dir, rule='A,B')
    assert 'extra' in report.downloaded


def test_a_failed_rule_write_fails_only_that_source(
    *,
    server: FeedServer,
    data_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An OSError writing the rule leaves the source out for the night."""
    def full_disk(**_: object) -> None:
        """
        Fail as a full disk does.

        Parameters
        ----------
        **_ : object
            Ignored.

        Raises
        ------
        OSError
            Always.
        """
        raise OSError('disk full')

    monkeypatch.setattr(refresh_module, 'write_rule', full_disk)
    serve_extra(server=server, tmp_path=tmp_path, comment=b'one')
    report = run_extra(server=server, data_dir=data_dir, rule='A,B')
    assert (report.failed, report.rebuilt) == (('extra',), True)
    assert not (data_dir / 'sources' / 'extra.zip').exists()


def test_a_cut_with_no_rule_file_is_cut_again(
    server: FeedServer, data_dir: Path, tmp_path: Path,
) -> None:
    """A stored cut from before rules were recorded is cut again."""
    serve_extra(server=server, tmp_path=tmp_path, comment=b'one')
    run_extra(server=server, data_dir=data_dir, rule='A,B')
    (data_dir / 'sources' / 'extra.rule').unlink()
    report = run_extra(server=server, data_dir=data_dir, rule='A,B')
    assert 'extra' in report.downloaded
    assert (data_dir / 'sources' / 'extra.rule').exists()


def test_a_cut_stored_under_the_same_rule_is_kept(
    server: FeedServer, data_dir: Path, tmp_path: Path,
) -> None:
    """Unchanged validators and a matching rule skip the download."""
    serve_extra(server=server, tmp_path=tmp_path, comment=b'one')
    run_extra(server=server, data_dir=data_dir, rule='A,B')
    report = run_extra(server=server, data_dir=data_dir, rule='A,B')
    assert 'extra' not in report.downloaded


def test_a_source_with_no_rule_leaves_no_rule_file(
    server: FeedServer, data_dir: Path, tmp_path: Path,
) -> None:
    """Sources without a rule store no rule file, and no extra state."""
    serve_extra(server=server, tmp_path=tmp_path, comment=b'one')
    run_extra(server=server, data_dir=data_dir)
    assert not list((data_dir / 'sources').glob('*.rule'))
    raw = json.loads(
        (data_dir / 'sources' / 'sources.json').read_text(encoding='utf-8'),
    )
    assert set(raw['extra']) == {'etag', 'last_modified', 'sha256'}


def test_unchanged_cut_bytes_do_not_rebuild(
    server: FeedServer, data_dir: Path, tmp_path: Path,
) -> None:
    """A new download whose cut is the same bytes changes nothing."""
    serve_extra(server=server, tmp_path=tmp_path, comment=b'one')
    run_extra(server=server, data_dir=data_dir)
    first = server.state.files[EXTRA].body
    server.state.files[EXTRA] = Served(
        body=first.removesuffix(b'one') + b'two', etag='"2"',
    )
    report = run_extra(server=server, data_dir=data_dir)
    assert ('extra' in report.downloaded, report.changed, report.rebuilt) == (
        True, (), False,
    )


def test_a_refused_cut_keeps_the_stored_cut(
    server: FeedServer, data_dir: Path, tmp_path: Path,
) -> None:
    """A download the cut refuses leaves the last good cut in place."""
    serve_extra(server=server, tmp_path=tmp_path, comment=b'one')
    run_extra(server=server, data_dir=data_dir)
    stored = (data_dir / 'sources' / 'extra.zip').read_bytes()
    before = load_states(path=data_dir / 'sources' / 'sources.json')
    serve_extra(server=server, tmp_path=tmp_path, comment=b'two', etag='"2"')
    report = run_extra(
        server=server, data_dir=data_dir, cut=refused, force=True,
    )
    assert (report.failed, report.all_failed) == (('extra',), False)
    assert (data_dir / 'sources' / 'extra.zip').read_bytes() == stored
    assert report.rebuilt and not report.changed
    after = load_states(path=data_dir / 'sources' / 'sources.json')
    assert after['extra'] == before['extra']


def test_an_optional_source_with_no_copy_is_left_out(
    server: FeedServer, data_dir: Path,
) -> None:
    """With no download and nothing stored, the rest build without it."""
    server.state.gone.add(EXTRA)
    report = run_extra(server=server, data_dir=data_dir)
    assert (report.failed, report.all_failed, report.rebuilt) == (
        ('extra',), False, True,
    )
    assert not (data_dir / 'sources' / 'extra.zip').exists()
    states = load_states(path=data_dir / 'sources' / 'sources.json')
    assert 'extra' not in states


def test_a_refused_cut_with_no_copy_is_left_out(
    server: FeedServer, data_dir: Path, tmp_path: Path,
) -> None:
    """A first download the cut refuses leaves the source out."""
    serve_extra(server=server, tmp_path=tmp_path, comment=b'one')
    report = run_extra(server=server, data_dir=data_dir, cut=refused)
    assert (report.failed, report.rebuilt) == (('extra',), True)
    assert not (data_dir / 'sources' / 'extra.zip').exists()


def test_cut_into_leaves_only_the_cut(tmp_path: Path) -> None:
    """The download is deleted and the cut's digest returned."""
    download = tmp_path / 'extra.download.zip'
    download.write_bytes(metro_zip(tmp_path=tmp_path, comment=b'one'))
    out = tmp_path / 'extra.zip'
    digest = cut_into(name='extra', cut=commented, download=download, out=out)
    assert digest == file_sha256(path=out)
    assert not download.exists()


def test_cut_into_turns_a_refusal_into_a_source_error(tmp_path: Path) -> None:
    """A cut's error is a SourceError naming the source and its class."""
    download = tmp_path / 'extra.download.zip'
    download.write_bytes(metro_zip(tmp_path=tmp_path, comment=b'one'))
    with pytest.raises(SourceError, match='extra: cut failed, ValueError'):
        cut_into(
            name='extra', cut=refused, download=download,
            out=tmp_path / 'extra.zip',
        )
    assert not download.exists()


def test_all_failed_counts_only_the_required_sources() -> None:
    """Every required source failing is a failed refresh; rail is not."""
    states = {
        'bods': SourceState(etag=None, last_modified=None, sha256='a'),
        'rail': SourceState(etag=None, last_modified=None, sha256='b'),
    }

    def fetched(*failed: str) -> Fetched:
        """
        Fetched with the given sources failed and rail optional.

        Parameters
        ----------
        *failed : str
            Names of the sources that failed.

        Returns
        -------
        Fetched
            The outcome.
        """
        return Fetched(
            states=states, downloaded=(), changed=(), failed=failed,
            optional=('rail',),
        )

    assert not fetched('rail').all_failed
    assert fetched('bods').all_failed
    assert fetched('bods', 'rail').all_failed
    assert not fetched().all_failed


def test_all_failed_is_false_with_no_states() -> None:
    """With nothing to build from, nothing counts as having failed."""
    assert not Fetched(states={}, downloaded=(), changed=()).all_failed
    assert not Fetched(
        states={}, downloaded=(), changed=(), failed=('rail',),
        optional=('rail',),
    ).all_failed
    assert not Fetched(
        states={}, downloaded=(), changed=(), failed=('rail',),
    ).all_failed


@pytest.mark.parametrize(
    'error',
    [
        RuntimeError('encrypted'),
        NotImplementedError('compression'),
        OSError('disk'),
        struct.error('bad header'),
    ],
)
def test_cut_into_turns_any_exception_into_a_source_error(
    tmp_path: Path, error: Exception,
) -> None:
    """Whatever a cut raises, the source fails with its class name."""
    download = tmp_path / 'extra.download.zip'
    download.write_bytes(metro_zip(tmp_path=tmp_path, comment=b'one'))
    with pytest.raises(
        SourceError, match=f'extra: cut failed, {type(error).__name__}',
    ):
        cut_into(
            name='extra', cut=raising(error=error), download=download,
            out=tmp_path / 'extra.zip',
        )
    assert not download.exists()


def test_cut_into_turns_a_failed_hash_into_a_source_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An OSError while hashing the cut fails the source."""

    def failing(*, path: Path) -> str:
        """
        Fail as a read error does.

        Parameters
        ----------
        path : Path
            Ignored.

        Raises
        ------
        OSError
            Always.
        """
        raise OSError(str(path))

    monkeypatch.setattr(refresh_module, 'file_sha256', failing)
    download = tmp_path / 'extra.download.zip'
    download.write_bytes(metro_zip(tmp_path=tmp_path, comment=b'one'))
    with pytest.raises(SourceError, match='extra: cut failed, OSError'):
        cut_into(
            name='extra', cut=commented, download=download,
            out=tmp_path / 'extra.zip',
        )
    assert not download.exists()


@pytest.mark.parametrize(
    'error',
    [RuntimeError('encrypted'), NotImplementedError('compression')],
)
def test_a_failing_cut_keeps_the_stored_cut(
    *,
    server: FeedServer, data_dir: Path, tmp_path: Path, error: Exception,
) -> None:
    """A cut that raises something unexpected fails the source only."""
    serve_extra(server=server, tmp_path=tmp_path, comment=b'one')
    run_extra(server=server, data_dir=data_dir)
    before = load_states(path=data_dir / 'sources' / 'sources.json')
    serve_extra(server=server, tmp_path=tmp_path, comment=b'two', etag='"2"')
    report = run_extra(
        server=server, data_dir=data_dir, cut=raising(error=error),
        force=True,
    )
    assert (report.failed, report.rebuilt) == (('extra',), True)
    after = load_states(path=data_dir / 'sources' / 'sources.json')
    assert after['extra'] == before['extra']


def test_a_damaged_download_keeps_the_stored_cut(
    server: FeedServer, data_dir: Path, tmp_path: Path,
) -> None:
    """A cut that meets a damaged member fails the source, not the refresh."""
    serve_extra(server=server, tmp_path=tmp_path, comment=b'one')
    run_extra(server=server, data_dir=data_dir)
    stored = (data_dir / 'sources' / 'extra.zip').read_bytes()
    serve_extra(server=server, tmp_path=tmp_path, comment=b'two', etag='"2"')
    before = load_states(path=data_dir / 'sources' / 'sources.json')
    report = run_extra(
        server=server, data_dir=data_dir, cut=damaged, force=True,
    )
    assert (report.failed, report.all_failed) == (('extra',), False)
    assert (data_dir / 'sources' / 'extra.zip').read_bytes() == stored
    assert report.rebuilt and not report.changed
    after = load_states(path=data_dir / 'sources' / 'sources.json')
    assert after['extra'] == before['extra']


def test_without_optional_leaves_out_every_optional_source() -> None:
    """Optional sources leave the states and are named failed, once."""
    state = SourceState(etag=None, last_modified=None, sha256='a')
    fetched = Fetched(
        states={'metro': state, 'extra': state},
        downloaded=('metro', 'extra'), changed=('metro', 'extra'),
        failed=('extra',), optional=('extra',),
    )
    assert without_optional(fetched=fetched) == Fetched(
        states={'metro': state}, downloaded=('metro',),
        changed=('metro',), failed=('extra',), optional=('extra',),
    )
    bare = Fetched(
        states={'metro': state}, downloaded=(), changed=(),
        optional=('extra',),
    )
    assert without_optional(fetched=bare) is None


def test_a_fallback_build_starts_after_the_failure_is_handled(
    *,
    server: FeedServer, data_dir: Path, tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The build without the new copy runs with no exception in hand."""
    real = build_timetable
    handling: list[BaseException | None] = []

    def build(
        *, sources: list[Source], window: Window, out: Path,
        city_ref: str, handoff: Handoff | None = None,
        tiles: Path | None = None,
    ) -> MergeReport:
        """
        Note the exception being handled, then refuse the extra source.

        Parameters
        ----------
        sources : list[Source]
            Feeds to load.
        window : Window
            Dates to cover.
        out : Path
            Timetable file.
        city_ref : str
            The city's reference.
        handoff : Handoff | None
            Passed on.
        tiles : Path | None
            Passed on.

        Returns
        -------
        MergeReport
            The real build's report.

        Raises
        ------
        ValueError
            When the extra source is among the sources.
        """
        handling.append(sys.exception())
        if any(source.name == 'extra' for source in sources):
            raise ValueError('extra rejected')
        return real(
            sources=sources, window=window, out=out, city_ref=city_ref,
            handoff=handoff, tiles=tiles,
        )

    monkeypatch.setattr(refresh_module, 'build_timetable', build)
    serve_extra(server=server, tmp_path=tmp_path, comment=b'one')
    report = run_extra(server=server, data_dir=data_dir)
    assert (report.failed, report.rebuilt) == (('extra',), True)
    assert handling == [None, None]
