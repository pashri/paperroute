"""Write GTFS zip files from rows for tests."""

import csv
import io
import zipfile
from pathlib import Path


def to_csv(rows: list[dict[str, str]]) -> str:
    """
    Render rows as CSV with a header of every key used.

    Parameters
    ----------
    rows : list[dict[str, str]]
        Records.

    Returns
    -------
    str
        CSV text.
    """
    fields = list(dict.fromkeys(key for row in rows for key in row))
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=fields)
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue()


def write_feed(*, path: Path, files: dict[str, list[dict[str, str]]]) -> Path:
    """
    Write a GTFS zip with one CSV member per file name.

    Parameters
    ----------
    path : Path
        Zip file to create.
    files : dict[str, list[dict[str, str]]]
        Member name (for example 'stops.txt') to its rows.

    Returns
    -------
    Path
        The zip path.
    """
    with zipfile.ZipFile(path, 'w') as archive:
        for name, rows in files.items():
            archive.writestr(name, to_csv(rows))
    return path
