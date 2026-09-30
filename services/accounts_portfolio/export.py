"""The Accounts portfolio as a CSV: every filtered row, every Account field
(`fields.FIELDS`), and the currency ARR is in. The cell rule and the renderer
are Organizations' own: a text cell that starts with `=`, `+`, `-`, `@`, a
tab or a carriage return is prefixed with `'`, so an account named
`=HYPERLINK(...)` is data, not an instruction."""

from services.organizations.export import build_table

from .fields import FIELDS


def table(rows, *, currency):
    return build_table(rows, FIELDS, currency)
