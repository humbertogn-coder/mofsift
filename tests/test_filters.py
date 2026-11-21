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


# ------------------------------------------------------------------ evaluation
@pytest.fixture
def table():
    import pandas as pd

    return pd.DataFrame(
        {
            "qc_pass": pd.array([True, False, pd.NA, True], dtype="boolean"),
            "xtb_gap": pd.array([0.5, 2.0, pd.NA, pd.NA], dtype="Float64"),
            "n_atoms": pd.array([10, 400, 50, 700], dtype="Int64"),
            "topology": pd.array(["pcu", "dia", pd.NA, "nbo"], dtype="string"),
        },
        index=pd.Index(["ms-1", "ms-2", "ms-3", "ms-4"], name="id"),
    )


def _answers(expr, table):
    from mofsift.filters import evaluate

    return [
        None if a is None else bool(a)
        for a in evaluate(expr, table).astype(object).where(lambda s: s.notna(), None)
    ]


@pytest.mark.parametrize(
    "expr, expected",
    [
        ("qc_pass == True", [True, False, None, True]),
        ("qc_pass", [True, False, None, True]),
        ("not qc_pass", [False, True, None, False]),
        ("xtb_gap < 1", [True, False, None, None]),
        ("0 < xtb_gap < 1", [True, False, None, None]),
        ("n_atoms / 2 + 1 > 26", [False, True, False, True]),
        ("-n_atoms < -100", [False, True, False, True]),
        ("1 < n_atoms", [True, True, True, True]),
        ("topology != 'pcu'", [False, True, None, True]),
        ("topology in ['pcu', 'nbo']", [True, False, None, True]),
        ("topology not in ['pcu', 'nbo']", [False, True, None, False]),
        ("id in ['ms-1', 'ms-4']", [True, False, False, True]),
        # a missing value leaves the answer open only when it matters
        ("xtb_gap < 1 or n_atoms > 600", [True, False, None, True]),
        ("xtb_gap < 1 and n_atoms < 100", [True, False, None, False]),
        # == None and != None ask whether a value was computed
        ("xtb_gap == None", [False, False, True, True]),
        ("xtb_gap != None and xtb_gap < 1", [True, False, False, False]),
        ("topology in ['pcu', None]", [True, False, True, False]),
    ],
)
def test_filters_have_three_answers(table, expr, expected):
    assert _answers(expr, table) == expected


@pytest.mark.parametrize(
    "expr, message",
    [
        ("xtb_gapp < 1", "the table has no column 'xtb_gapp'; did you mean 'xtb_gap'?"),
        ("topology > 1", "cannot compare text with number"),
        ("qc_pass > False", "true/false values can only be compared with == or !="),
        ("xtb_gap + 1", "a filter must be a comparison or a true/false column"),
        ("n_atoms in topology", "the right side of 'in' must be a list of constants"),
        ("qc_pass and n_atoms", "need comparisons or true/false columns"),
        ("topology + 1 > 2", "+ - * / need numbers on both sides"),
        ("xtb_gap < None", "None can only be compared with == or !="),
        ("[1, 2] == n_atoms", "a list can only be used on the right of 'in'"),
    ],
)
def test_filters_that_cannot_be_applied_say_why(table, expr, message):
    from mofsift.filters import evaluate

    with pytest.raises(FilterError) as err:
        evaluate(expr, table)
    assert message in str(err.value) and repr(expr) in str(err.value)


def test_columns_read_from_a_hand_made_table_work_too():
    import pandas as pd

    from mofsift.filters import evaluate

    plain = pd.DataFrame({"x": [1.0, float("nan"), 3.0], "label": ["a", None, "c"]}, index=["r1", "r2", "r3"])
    assert evaluate("x > 2", plain).astype(object).tolist()[::2] == [False, True]
    assert evaluate("x > 2", plain).isna().tolist() == [False, True, False]
    assert evaluate("label == 'a' or x > 2", plain).isna().tolist() == [False, True, False]


def test_missing_columns_are_named_per_row(table):
    from mofsift.filters import evaluate, missing_columns

    expr = "xtb_gap < 1 and topology == 'pcu'"
    answer = evaluate(expr, table)
    assert missing_columns(expr, table, answer.isna()).to_dict() == {"ms-3": "topology, xtb_gap"}


def test_text_of_a_hand_made_table_keeps_its_missing_values_undecided():
    import pandas as pd

    from mofsift.filters import evaluate

    # with pandas 3 such a column arrives as text whose missing values compare as plain false
    plain = pd.DataFrame({"label": ["a", None, "c"], "flag": [True, None, False]})
    assert evaluate("label != 'a'", plain).isna().tolist() == [False, True, False]
    assert evaluate("label == 'a'", plain).isna().tolist() == [False, True, False]
    assert evaluate("label != 'a'", plain).fillna(False).tolist() == [False, False, True]
    assert evaluate("not flag", plain).isna().tolist() == [False, True, False]


def test_a_division_by_zero_leaves_the_row_undecided():
    import pandas as pd

    from mofsift.filters import evaluate, missing_columns

    table = pd.DataFrame(
        {
            "area": pd.array([4.0, 4.0, 0.0, pd.NA], dtype="Float64"),
            "vol": pd.array([2.0, 0.0, 0.0, 1.0], dtype="Float64"),
            "n": pd.array([3, 3, 3, 3], dtype="Int64"),
        }
    )
    answer = evaluate("area / vol > 1", table)
    assert answer.isna().tolist() == [False, True, True, True] and bool(answer[0])
    # rows 1 and 2 have every value: nothing is missing, the division has no answer
    assert missing_columns("area / vol > 1", table, answer.isna()).tolist() == ["", "", "area"]
    assert evaluate("n / 0 > 1", table).isna().all()
    # a guard written by the user decides those rows
    assert evaluate("vol != 0 and area / vol > 1", table).fillna(True).tolist() == [True, False, False, True]


@pytest.mark.parametrize(
    "expr, expected",
    [
        ("n_atoms + 99999999999999999999999 > 0", [True, True, True, True]),
        ("n_atoms + 9223372036854775807 > 0", [True, True, True, True]),  # no wrap-around
        ("n_atoms < 99999999999999999999999", [True, True, True, True]),
        ("n_atoms in [-1, 10, +50]", [True, False, True, False]),
        ("n_atoms not in [10.0]", [False, True, True, True]),
        ("n_atoms in []", [False, False, False, False]),
    ],
)
def test_numbers_of_any_size_and_sign(table, expr, expected):
    assert _answers(expr, table) == expected


@pytest.mark.parametrize(
    "expr, message",
    [
        ("qc_pass in [1]", "cannot compare boolean with number"),
        ("topology in [1, 2]", "cannot compare text with number"),
        ("n_atoms in ['pcu']", "cannot compare number with text"),
        ("n_atoms in [[1]]", "must be a list of constants"),
        ("n_atoms in [-'a']", "must be a list of constants"),
        ("1 / 0 > n_atoms", "division by zero"),
        ("n_atoms * " + "9" * 400 + " > 1", "a number is out of range"),
    ],
)
def test_lists_and_constants_that_make_no_sense_are_errors(table, expr, message):
    from mofsift.filters import evaluate

    with pytest.raises(FilterError, match=message):
        evaluate(expr, table)


def test_whatever_goes_wrong_is_reported_as_a_problem_of_the_filter(table, monkeypatch):
    import mofsift.filters as filters

    def broken(series):
        raise RuntimeError("boom")

    monkeypatch.setattr(filters, "_nullable", broken)
    with pytest.raises(FilterError, match=r"cannot be evaluated \(RuntimeError: boom\)"):
        filters.evaluate("n_atoms > 1", table)
