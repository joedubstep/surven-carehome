from surven_outreach.templates import render

P = {"first_name": "Tony", "business": "Roof Leak Enders", "category": "roofing", "city": "Newark",
     "personal_line": "saw you hit 500 reviews — that's no accident"}


def test_render_email1_fills_and_signs():
    out = render(1, P)
    assert "hey Tony," in out["body"]
    assert "saw you hit 500 reviews" in out["body"]
    assert "Roof Leak Enders" in out["body"]
    assert out["body"].rstrip().endswith("thanks,\nJoseph")
    assert out["subject"] == "quick q Tony"


def test_render_no_name_uses_fallback_not_blank():
    out = render(1, {**P, "first_name": ""})
    assert "[NAME]" not in out["body"]
    assert "hey ," not in out["body"]
    assert out["subject"] == "quick q"


def test_render_no_personal_line_drops_it_cleanly():
    out = render(1, {**P, "personal_line": ""})
    assert "[PERSONAL]" not in out["body"]
    assert "\n\n\n" not in out["body"]


def test_render_followup_has_no_leaked_tokens_and_signs():
    out = render(2, P)
    for tok in ("[NAME]", "[BUSINESS]", "[CATEGORY]", "[CITY]", "[PERSONAL]", "%SIGNOFF%"):
        assert tok not in out["body"]
    assert out["body"].rstrip().endswith("thanks,\nJoseph")
    assert out["subject"] is None  # threaded reply


def test_render_no_city_drops_clause():
    out = render(1, {**P, "city": ""})
    assert "[CITY]" not in out["body"]
    assert " in \"" not in out["body"]
