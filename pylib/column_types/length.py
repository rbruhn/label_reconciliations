"""Reconcile line lengths."""
import json
import math
import re
import statistics as stats

from .. import cell
from ..util import P


SCALE_RE = re.compile(
    r"(?P<scale> [0-9.]+ ) \s* (?P<units> (mm|cm|dm|m) ) \b",
    flags=re.VERBOSE | re.IGNORECASE,
)


def reconcile(group, args=None):  # noqa
    raw_lines = [json.loads(ln) for ln in group]

    lines = [ln for ln in raw_lines if ln.get("x1")]

    raw_count = len(raw_lines)
    count = len(lines)

    if not count:
        note = f'There are no lines in {raw_count} {P("records", raw_count)}.'
        return cell.empty(note=note)

    note = (
        f'There {P("was", count)} {count} '
        f'{P("line", raw_count)} in {raw_count} {P("record", raw_count)}'
    )

    x1 = stats.mean([ln["x1"] for ln in lines])
    y1 = stats.mean([ln["y1"] for ln in lines])
    x2 = stats.mean([ln["x2"] for ln in lines])
    y2 = stats.mean([ln["y2"] for ln in lines])

    return cell.ok(note=note, length_pixels=math.sqrt((x1 - x2) ** 2 + (y1 - y2) ** 2))


def reconcile_row(reconciled_row, args=None):  # noqa
    """Calculate lengths using units and pixel_lengths."""
    units, factor = None, None
    for field, value in reconciled_row.items():
        if field.find("length pixels") > -1 and (match := SCALE_RE.search(field)):
            units = match.group("units")
            factor = value / float(match.group("scale"))
            break

    if not units:
        return

    for key, value in reconciled_row.items():
        if key.find("length pixels") > -1:
            field = key.replace("pixels", units)
            reconciled_row[field] = value * factor
