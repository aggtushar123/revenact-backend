"""The portfolio as a CSV: every filtered row, the 34 table fields, and the
currency the money columns are in.

A spreadsheet runs a cell that starts with `=`, `+`, `-`, `@`, a tab or a
carriage return as a formula, so any text cell that does is prefixed with `'`.
A company named `=HYPERLINK(...)` is data, not an instruction. Numbers are
written as numbers (an NPS of -80 stays -80).
"""

import csv
import io

from rest_framework.renderers import BaseRenderer

from .fields import FIELDS

FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def cell(value):
    if value is None:
        return ""
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, (int, float)):
        return value
    text = str(value)
    return "'" + text if text.startswith(FORMULA_PREFIXES) else text


def build_table(rows, fields, currency):
    """The shared shape behind every portfolio's CSV export (Organizations,
    Accounts, Pipelines): a header of `fields`' labels plus "Currency", then
    one escaped (`cell`) row per item. `currency` is either a fixed value
    (Accounts, Pipelines: one workspace currency) or a callable of the row
    (Organizations: each customer bills in its own)."""
    header = [field.label for field in fields] + ["Currency"]
    get_currency = currency if callable(currency) else lambda _row: currency
    body = [[cell(field.value(row)) for field in fields] + [get_currency(row)] for row in rows]
    return [header, *body]


def table(rows):
    return build_table(rows, FIELDS, lambda row: row["details"]["commercial"]["currency"])


class CSVRenderer(BaseRenderer):
    """The export view's only renderer, so it answers `Accept: text/csv` and
    `*/*` alike. An error (401, 403) is written as a one-cell CSV rather than
    switching format mid-download."""

    media_type = "text/csv"
    format = "csv"
    charset = "utf-8"

    def render(self, data, accepted_media_type=None, renderer_context=None):
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        if isinstance(data, list):
            writer.writerows(data)
        else:
            detail = data.get("detail", "") if isinstance(data, dict) else data
            writer.writerows([["detail"], [str(detail)]])
        return buffer.getvalue().encode(self.charset)
