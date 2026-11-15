"""Row-filter expressions used in the ``filter`` section of a config.

Filters are small boolean expressions over table columns, for example
``qc_pass == True`` or ``density > 0.2 and n_atoms <= 400``. Only comparisons, boolean
operators, arithmetic on numbers and membership tests are allowed. Function calls,
attribute access and subscripts are rejected, so a config file cannot run arbitrary code.

This module only validates expressions; applying them to a table is part of the filter
step (a later milestone).
"""

from __future__ import annotations

import ast

_ALLOWED_NODES: tuple[type[ast.AST], ...] = (
    ast.Expression,
    ast.BoolOp,
    ast.And,
    ast.Or,
    ast.UnaryOp,
    ast.Not,
    ast.USub,
    ast.UAdd,
    ast.BinOp,
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.Div,
    ast.Compare,
    ast.Eq,
    ast.NotEq,
    ast.Lt,
    ast.LtE,
    ast.Gt,
    ast.GtE,
    ast.In,
    ast.NotIn,
    ast.Name,
    ast.Load,
    ast.Constant,
    ast.List,
    ast.Tuple,
)
_RESERVED = {"True", "False", "None"}


class FilterError(ValueError):
    """Raised for a filter expression that is empty, malformed or uses a forbidden construct."""


def validate_expression(expr: str) -> set[str]:
    """Check a filter expression and return the column names it refers to."""
    if not isinstance(expr, str) or not expr.strip():
        raise FilterError("filter expressions must be non-empty strings")
    try:
        tree = ast.parse(expr.strip(), mode="eval")
    except SyntaxError as err:
        raise FilterError(f"cannot parse filter {expr!r}: {err.msg}") from err
    columns: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED_NODES):
            raise FilterError(
                f"filter {expr!r} uses {type(node).__name__}, which is not allowed "
                "(only comparisons, and/or/not, + - * /, in / not in, column names and constants)"
            )
        if isinstance(node, ast.Constant) and not isinstance(node.value, (bool, int, float, str, type(None))):
            raise FilterError(f"filter {expr!r} contains an unsupported constant")
        if isinstance(node, ast.Name) and node.id not in _RESERVED:
            columns.add(node.id)
    if not columns:
        raise FilterError(f"filter {expr!r} does not refer to any column")
    return columns
