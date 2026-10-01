"""The Pipelines book as a CSV: every row the list would page through (the
chosen stages, every page), every field (`fields.fields_for`), and the
currency MRR is in. Organizations' cell rule and renderer, shared through
`build_table`: a text cell that starts with `=`, `+`, `-`, `@`, a tab or a
carriage return is prefixed with `'`, so a deal titled `=HYPERLINK(...)` is
data, not an instruction."""

from services.organizations.export import build_table

from .fields import fields_for


def table(rows, *, kind, currency):
    return build_table(rows, fields_for(kind), currency)
