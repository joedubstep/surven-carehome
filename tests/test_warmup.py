"""Held sender domains (10/15): no cold email before the hold date, and after it only once warm-up mail has
reached the inbox, not spam, 3 days running."""
import json
from datetime import date

import pytest

from surven_outreach import carehome as ch
from surven_outreach import warmup

A = "joey@getsurven.com"


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setattr(warmup, "STATE", tmp_path / "warmup.json")
    monkeypatch.setattr(ch, "HOLD_UNTIL", {A: "2026-10-25"})
    monkeypatch.setattr(ch, "log", lambda m: None)
    day = {"today": date(2026, 10, 20)}

    class FakeDate(date):
        @classmethod
        def today(cls):
            return day["today"]

    monkeypatch.setattr(warmup, "date", FakeDate)
    return day


def spam(days):
    warmup.STATE.write_text(json.dumps({"sent": {}, "spam": {A: days}, "released": {}}))


def test_held_before_the_date_even_when_clean(world):
    spam({"2026-10-17": False, "2026-10-18": False, "2026-10-19": False})
    assert warmup.release_day(A) is None


def test_released_on_the_date_after_3_clean_days(world):
    world["today"] = date(2026, 10, 25)
    spam({"2026-10-22": False, "2026-10-23": False, "2026-10-24": False})
    assert warmup.release_day(A) == "2026-10-25"
    world["today"] = date(2026, 10, 26)  # stays released, ramp counts from the release day
    assert warmup.release_day(A) == "2026-10-25"


def test_a_spam_day_keeps_it_held(world):
    world["today"] = date(2026, 10, 25)
    spam({"2026-10-22": False, "2026-10-23": True, "2026-10-24": False})
    assert warmup.release_day(A) is None


def test_old_state_file_still_reads(world):
    warmup.STATE.write_text(json.dumps({A: "2026-10-15"}))
    assert warmup.load()["sent"] == {A: "2026-10-15"} and warmup.release_day(A) is None
