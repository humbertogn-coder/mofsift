from __future__ import annotations

import pytest

from mofsift.filters import FilterError, validate_expression


@pytest.mark.parametrize(
    "expr, columns",
    [
        ("qc_pass == True", {"qc_pass"}),
        ("density > 0.2 and n_atoms <= 400", {"density", "n_atoms"}),
        ("not (metal in ['Zn', 'Mg']) or pld_A >= 3.5", {"metal", "pld_A"}),
        ("lcd_A - pld_A < 2 * 1.5", {"lcd_A", "pld_A"}),
        ("  topology != 'pcu'  ", {"topology"}),
        ("xtb_gap_eV == None", {"xtb_gap_eV"}),
    ],
)
def test_valid_expressions_return_their_columns(expr, columns):
    assert validate_expression(expr) == columns


@pytest.mark.parametrize(
    "expr",
    [
        "__import__('os').system('rm -rf /')",
        "open('x').read()",
        "df.query('a')",
        "a[0] > 1",
        "(lambda: 1)()",
        "[x for x in y]",
        "a ** 99999999 > 1",
        "a if b else c",
        "f'{a}' == 'x'",
        "a := 3",
    ],
)
def test_code_like_expressions_are_rejected(expr):
    with pytest.raises(FilterError):
        validate_expression(expr)


@pytest.mark.parametrize("expr", ["", "   ", "1 == 1", "True", "density >"])
def test_empty_constant_or_malformed_expressions_are_rejected(expr):
    with pytest.raises(FilterError):
        validate_expression(expr)


def test_non_string_is_rejected():
    with pytest.raises(FilterError):
        validate_expression(None)  # type: ignore[arg-type]
