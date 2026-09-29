from pathlib import Path

from surven_outreach.clean import clean_prospects

FIX = Path(__file__).parent / "fixtures" / "sample_prospects.csv"


def test_clean_drops_corrupt_and_empty_and_dedupes():
    rows = clean_prospects(str(FIX))
    emails = [r["email"] for r in rows]
    # corrupt (email=0) and no-email rows dropped
    assert "0" not in emails
    assert all(e for e in emails)
    # deduped: vetinbrooklyn appears once
    assert emails.count("vetinbrooklyn@gmail.com") == 1
    # kept the richer duplicate (has first_name + website)
    vet = next(r for r in rows if r["email"] == "vetinbrooklyn@gmail.com")
    assert vet["first_name"] == "Pamela"
    assert vet["website"] == "vetinbrooklyn.com"


def test_clean_strips_legal_suffix_and_township(tmp_path):
    csv = tmp_path / "polish.csv"
    csv.write_text(
        "name,company_name,first_name,category,address,email,website,about,description\n"
        'Smooth Sailing Celebrations LLC,Smooth Sailing Celebrations LLC,Camille,Event venue,"1 Pier, Weehawken Township, NJ",camille@smoothsailing.com,smoothsailing.com,"yacht events",\n',
        encoding="utf-8",
    )
    r = clean_prospects(str(csv))[0]
    assert r["business"] == "Smooth Sailing Celebrations"
    assert r["city"] == "Weehawken"


def test_clean_dedupes_to_one_contact_per_business(tmp_path):
    csv = tmp_path / "biz.csv"
    csv.write_text(
        "name,company_name,first_name,category,address,email,website,about,description\n"
        'Acme Roofing,Acme Roofing,Tony,Roofing,"1 A St, Newark, NJ",tony@acmeroofing.com,acmeroofing.com,"since 2001",\n'
        'Acme Roofing,Acme Roofing,Mike,Roofing,"1 A St, Newark, NJ",mike@acmeroofing.com,acmeroofing.com,,\n',
        encoding="utf-8",
    )
    rows = clean_prospects(str(csv))
    acme = [r for r in rows if "acmeroofing.com" in r["website"]]
    assert len(acme) == 1
    assert acme[0]["email"] == "tony@acmeroofing.com"  # higher score (has about text)


def test_clean_extracts_city_and_orders_best_first():
    rows = clean_prospects(str(FIX))
    roof = next(r for r in rows if r["email"] == "info@roofleakenders.com")
    assert roof["city"] == "Newark"
    # best-personalizing (name + about + domain-matched email) ranks before weaker rows
    assert rows[0]["personalization_score"] >= rows[-1]["personalization_score"]
    # the domain-matched roofing row should be the strongest
    assert rows[0]["email"] == "info@roofleakenders.com"
