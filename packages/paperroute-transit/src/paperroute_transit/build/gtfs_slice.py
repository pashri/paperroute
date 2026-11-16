"""Cut and write slices of a GTFS zip."""

import csv
import io
import zipfile
from collections.abc import Collection, Mapping, Sequence
from pathlib import Path

from paperroute_transit.build.gtfs_files import read_rows


def rows_where(
    *,
    archive: Path,
    member: str,
    key: str,
    keep: Collection[str],
) -> list[dict[str, str]]:
    """
    Read the rows of a member whose column holds a wanted value.

    Parameters
    ----------
    archive : Path
        GTFS zip.
    member : str
        File inside the zip.
    key : str
        Column compared; a row without it is left out.
    keep : Collection[str]
        Values kept.

    Returns
    -------
    list[dict[str, str]]
        The rows kept, in order; none when the member is missing.
    """
    return [
        row
        for row in read_rows(archive=archive, member=member)
        if row.get(key) in keep
    ]


def csv_text(*, table: Sequence[Mapping[str, str]]) -> str:
    """
    Write rows as CSV with a header from the first row.

    Parameters
    ----------
    table : Sequence[Mapping[str, str]]
        Rows; at least one.

    Returns
    -------
    str
        The CSV text.
    """
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(table[0]))
    writer.writeheader()
    writer.writerows(table)
    return buffer.getvalue()


def write_tables(
    *,
    out: Path,
    tables: Mapping[str, Sequence[Mapping[str, str]]],
) -> None:
    """
    Write a zip with one CSV member for each table that has rows.

    Parameters
    ----------
    out : Path
        Zip to write.
    tables : Mapping[str, Sequence[Mapping[str, str]]]
        Rows by member name.
    """
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, table in tables.items():
            if table:
                archive.writestr(name, csv_text(table=table))
