"""Row-filter expressions used in the ``filter`` section of a config.

Filters are small boolean expressions over table columns, for example
``qc_pass == True`` or ``density > 0.2 and n_atoms <= 400``. Only comparisons, boolean
operators, arithmetic on numbers and membership tests are allowed. Function calls,
attribute access and subscripts are rejected, so a config file cannot run arbitrary code.

A filter is evaluated on every row with three possible answers: true, false, or
*undecided* when the answer depends on a value that is missing. ``a > 1 or b > 2`` is true
for a row with ``a = 5`` even when ``b`` is missing, and undecided for a row with ``a = 0``
and no ``b``. The filter step keeps only the rows for which every filter is true, and
records the undecided ones with the columns that were missing. ``column == None`` and
``column != None`` test for a missing value. A division by zero gives no number, so the
row is undecided as well.
"""

from __future__ import annotations

import ast
import difflib
import math
import operator
from collections.abc import Callable
from typing import Any

import numpy as np
import pandas as pd

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


# ------------------------------------------------------------------ evaluation
ID_NAME = "id"  # the record ID is the index of the table; a filter can name it like a column

_ARITHMETIC: dict[type[ast.AST], Callable[[Any, Any], Any]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
}
_ORDERING: dict[type[ast.AST], Callable[[Any, Any], Any]] = {
    ast.Lt: operator.lt,
    ast.LtE: operator.le,
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
}


def _kind(value: Any) -> str:
    """'boolean', 'number', 'text' or 'none' for a constant or a column."""
    if isinstance(value, pd.Series):
        if pd.api.types.is_bool_dtype(value.dtype):
            return "boolean"
        return "number" if pd.api.types.is_numeric_dtype(value.dtype) else "text"
    if value is None:
        return "none"
    if isinstance(value, bool):
        return "boolean"
    return "number" if isinstance(value, (int, float)) else "text"


_INT64 = 2**63 - 1


def _nullable(series: pd.Series) -> pd.Series:
    """The column in a dtype that keeps a missing value missing through every operation.

    Columns written by the stages already have one. A column of a hand-made table arrives
    as NumPy numbers, as objects or, with pandas 3, as text whose missing values compare
    as plain false; all of these are converted.
    """
    dtype = series.dtype
    if not pd.api.types.is_extension_array_dtype(dtype):
        series = series.convert_dtypes()
        dtype = series.dtype
    if isinstance(dtype, pd.StringDtype) and dtype.na_value is not pd.NA:
        series = series.astype("string")
    return series


class _Evaluator:
    def __init__(self, expr: str, table: pd.DataFrame):
        self.expr, self.table = expr, table

    def fail(self, message: str) -> FilterError:
        return FilterError(f"filter {self.expr!r}: {message}")

    def column(self, name: str) -> pd.Series:
        table = self.table
        if name in table.columns:
            series = table[name]
        elif name == ID_NAME:
            series = pd.Series(table.index.astype("string"), index=table.index)
        else:
            close = difflib.get_close_matches(name, [ID_NAME, *map(str, table.columns)], n=1)
            hint = f"; did you mean {close[0]!r}?" if close else ""
            raise self.fail(f"the table has no column {name!r}{hint}")
        return _nullable(series)

    def constant(self, value: Any) -> Any:
        """A constant of the expression; an integer too large for the tables becomes a float."""
        if isinstance(value, int) and not isinstance(value, bool) and abs(value) > _INT64:
            try:
                return float(value)
            except OverflowError as err:
                raise self.fail("a number is out of range") from err
        return value

    def value(self, node: ast.AST) -> Any:
        if isinstance(node, ast.Constant):
            return self.constant(node.value)
        if isinstance(node, ast.Name):
            return self.column(node.id)
        if isinstance(node, ast.UnaryOp):
            return self.unary(node)
        if isinstance(node, ast.BinOp):
            return self.arithmetic(node)
        if isinstance(node, ast.BoolOp):
            return self.boolean(node)
        if isinstance(node, ast.Compare):
            return self.compare(node)
        raise self.fail("a list can only be used on the right of 'in' or 'not in'")

    def truth(self, node: ast.AST) -> Any:
        value = self.value(node)
        if _kind(value) != "boolean":
            raise self.fail(
                "'and', 'or' and 'not' need comparisons or true/false columns on both sides "
                f"(found {_kind(value)})"
            )
        return value

    def unary(self, node: ast.UnaryOp) -> Any:
        if isinstance(node.op, ast.Not):
            value = self.truth(node.operand)
            return ~value if isinstance(value, pd.Series) else not value
        value = self.value(node.operand)
        if _kind(value) != "number":
            raise self.fail("a sign needs a number")
        return -value if isinstance(node.op, ast.USub) else value

    def arithmetic(self, node: ast.BinOp) -> Any:
        left, right = self.value(node.left), self.value(node.right)
        if _kind(left) != "number" or _kind(right) != "number":
            raise self.fail("+ - * / need numbers on both sides")
        # in floating point, so that integers cannot overflow or wrap around
        left, right = (v.astype("Float64") if isinstance(v, pd.Series) else float(v) for v in (left, right))
        try:
            result = _ARITHMETIC[type(node.op)](left, right)
        except ZeroDivisionError as err:
            raise self.fail("division by zero") from err
        except OverflowError as err:
            raise self.fail("a number is out of range") from err
        if isinstance(result, pd.Series):
            # a division by zero has no answer: the row is left undecided, like a missing value
            finite = np.isfinite(result.to_numpy(dtype=float, na_value=np.nan))
            return result.mask(~finite)
        if not math.isfinite(result):
            raise self.fail("a number is out of range")
        return result

    def boolean(self, node: ast.BoolOp) -> Any:
        values = [self.truth(v) for v in node.values]
        combine = operator.and_ if isinstance(node.op, ast.And) else operator.or_
        out = values[0]
        for value in values[1:]:
            # a column on the left, so that pandas applies its three-valued logic
            out = combine(value, out) if not isinstance(out, pd.Series) else combine(out, value)
        return out

    def compare(self, node: ast.Compare) -> Any:
        # a < b < c is (a < b) and (b < c)
        operands = [node.left, *node.comparators]
        parts = [
            self.compare_pair(left, op, right)
            for left, op, right in zip(operands, node.ops, operands[1:], strict=False)
        ]
        out = parts[0]
        for part in parts[1:]:
            out = (out & part) if isinstance(out, pd.Series) else (part & out)
        return out

    def members(self, node: ast.AST) -> list[Any]:
        problem = self.fail(
            "the right side of 'in' must be a list of constants, like topology in ['pcu', 'dia']"
        )
        if not isinstance(node, (ast.List, ast.Tuple)):
            raise problem
        out: list[Any] = []
        for element in node.elts:
            sign = 1
            if isinstance(element, ast.UnaryOp) and isinstance(element.op, (ast.USub, ast.UAdd)):
                sign = -1 if isinstance(element.op, ast.USub) else 1
                element = element.operand
                if not isinstance(element, ast.Constant) or _kind(element.value) != "number":
                    raise problem
            if not isinstance(element, ast.Constant):
                raise problem
            value = self.constant(element.value)
            out.append(sign * value if sign == -1 else value)
        return out

    def compare_pair(self, left_node: ast.AST, op: ast.cmpop, right_node: ast.AST) -> Any:
        if isinstance(op, (ast.In, ast.NotIn)):
            left, members = self.value(left_node), self.members(right_node)
            for member in members:
                if member is not None and _kind(member) != _kind(left):
                    raise self.fail(f"cannot compare {_kind(left)} with {_kind(member)}")
            if isinstance(left, pd.Series):
                inside = left.isin([m for m in members if m is not None]).astype("boolean")
                # a missing value is "in" the list only when the list names None
                inside = inside.mask(left.isna(), True if None in members else pd.NA)
            else:
                inside = left in members
            if isinstance(op, ast.In):
                return inside
            return ~inside if isinstance(inside, pd.Series) else not inside

        left, right = self.value(left_node), self.value(right_node)
        kinds = _kind(left), _kind(right)
        equality = isinstance(op, (ast.Eq, ast.NotEq))
        if "none" in kinds:  # == None and != None test for a missing value
            if not equality:
                raise self.fail("None can only be compared with == or !=")
            other = right if kinds[0] == "none" else left
            missing = other.isna().astype("boolean") if isinstance(other, pd.Series) else other is None
            if isinstance(op, ast.Eq):
                return missing
            return ~missing if isinstance(missing, pd.Series) else not missing
        if kinds[0] != kinds[1]:
            raise self.fail(f"cannot compare {kinds[0]} with {kinds[1]}")
        if not equality and kinds[0] == "boolean":
            raise self.fail("true/false values can only be compared with == or !=")
        compare = (
            operator.eq
            if isinstance(op, ast.Eq)
            else operator.ne
            if isinstance(op, ast.NotEq)
            else _ORDERING[type(op)]
        )
        result = compare(left, right)
        return result.astype("boolean") if isinstance(result, pd.Series) else bool(result)


def evaluate(expr: str, table: pd.DataFrame) -> pd.Series:
    """Evaluate a filter on every row of ``table``.

    Returns a nullable boolean Series with the index of the table: True, False, or missing
    where the answer depends on a missing value. Raises :class:`FilterError` when the
    expression names a column the table does not have, or compares values of different
    kinds (a number with text, for example).
    """
    validate_expression(expr)
    evaluator = _Evaluator(expr.strip(), table)
    tree = ast.parse(expr.strip(), mode="eval")
    try:
        result = evaluator.value(tree.body)
    except FilterError:
        raise
    except Exception as err:  # values that cannot be combined: report it as a problem of the filter
        raise evaluator.fail(f"cannot be evaluated ({type(err).__name__}: {err})") from err
    if _kind(result) != "boolean":
        raise evaluator.fail("a filter must be a comparison or a true/false column")
    if not isinstance(result, pd.Series):
        result = pd.Series(result, index=table.index)
    return result.astype("boolean")


def missing_columns(expr: str, table: pd.DataFrame, rows: pd.Series) -> pd.Series:
    """For the rows selected by the mask ``rows``: the columns named in ``expr`` that are missing.

    Returns text such as ``"lcd, pld"`` per selected row (empty text when none is missing).
    """
    names = sorted(validate_expression(expr))
    index = table.index[rows.to_numpy(dtype=bool)]
    code = pd.Series(0, index=index, dtype="int64")
    for bit, name in enumerate(names):
        if name in table.columns:
            code += table.loc[index, name].isna().to_numpy(dtype="int64") << bit
    labels = {int(c): ", ".join(n for bit, n in enumerate(names) if int(c) >> bit & 1) for c in code.unique()}
    return code.map(labels).astype("string")
