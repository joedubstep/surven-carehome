from surven_outreach.personalize import validate_personal_line


def test_validates_length_case_and_skip():
    assert validate_personal_line("SKIP") is None
    assert validate_personal_line("") is None
    assert validate_personal_line(None) is None
    assert validate_personal_line("noticed your rooftop pool doubles as a hot tub in the winter") is not None
    assert validate_personal_line("THIS IS A SHOUTY OVER LONG LINE WITH WAY TOO MANY WORDS HERE") is None
    assert validate_personal_line("great work!") is None   # exclamation banned


def test_rejects_dashes_reads_as_ai():
    # em / en / spaced-hyphen dashes read AI-written -> drop the line (Joey 2026-06-01)
    assert validate_personal_line("saw your new space — looks great") is None   # em dash
    assert validate_personal_line("saw your new space – nice") is None          # en dash
    assert validate_personal_line("saw your new space - clean") is None         # spaced hyphen as dash
    # a hyphenated compound (no surrounding spaces) is fine
    assert validate_personal_line("noticed your pool runs year-round, makes sense") is not None
