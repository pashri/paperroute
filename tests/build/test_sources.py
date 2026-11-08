"""Tests for deciding on and recording timetable downloads."""

from pathlib import Path
from typing import Final

import pytest

from paperroute.build.source_state import SourceState, load_states, save_states
from paperroute.build.sources import needs_download

SAME: Final[SourceState] = SourceState(
    etag='"1"',
    last_modified='Thu, 15 Oct 2026 10:00:00 GMT',
    sha256='abc',
)


@pytest.mark.parametrize(
    ('stored', 'fresh', 'zip_exists', 'expected'),
    [
        (None, SAME, True, True),
        (SAME, SAME, False, True),
        (SAME, SourceState(etag='"2"', last_modified=SAME.last_modified,
                           sha256=None), True, True),
        (SAME, SourceState(etag=SAME.etag, last_modified='Fri',
                           sha256=None), True, True),
        (SAME, SourceState(etag=None, last_modified=None, sha256=None),
         True, True),
        (SAME, SourceState(etag=SAME.etag, last_modified=SAME.last_modified,
                           sha256=None), True, False),
    ],
    ids=[
        'no-stored-state',
        'missing-zip',
        'etag-change',
        'last-modified-change',
        'neither-header',
        'all-same',
    ],
)
def test_needs_download_matrix(
    stored: SourceState | None,
    fresh: SourceState,
    zip_exists: bool,
    expected: bool,
) -> None:
    """Download only when the headers can't prove the zip is current."""
    assert needs_download(
        stored=stored,
        fresh=fresh,
        zip_exists=zip_exists,
    ) is expected


def test_states_round_trip(tmp_path: Path) -> None:
    """Saved states load back unchanged; a missing file loads as empty."""
    path = tmp_path / 'sources' / 'sources.json'
    assert not load_states(path=path)
    path.parent.mkdir()
    save_states(path=path, states={'metro': SAME})
    assert load_states(path=path) == {'metro': SAME}
    assert [entry.name for entry in path.parent.iterdir()] == [
        'sources.json',
    ]
