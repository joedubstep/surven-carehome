"""v3 sequence (Joey 10/23): 4 emails on days 1, 3, 7 and 14; day 7 is "pending" in the queue and built per home at
send time (day7/build.py); homes already in the old 6-email run keep the old schedule."""
import sqlite3
from datetime import datetime, timezone

from surven_outreach import carehome as ch

NOON_ET = datetime(2026, 11, 2, 13, 0, tzinfo=timezone.utc)  # 8 AM Eastern, inside the send window
SENDERS = {ch.PRIMARY: {"cap_total": 50, "cap_new": 50}}


def db():
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.executescript(ch.SCHEMA)
    con.execute("ALTER TABLE prospects ADD COLUMN sender TEXT")  # added by connect() on the live db
    con.execute("ALTER TABLE sends ADD COLUMN sender TEXT")
    return con


def home(con, pid, step, first, last, status="active"):
    con.execute("INSERT INTO prospects (id,email,facility,first_name,tz,status,current_step,first_sent_on,last_sent_on,sender)"
                " VALUES (?,?,?,?,?,?,?,?,?,?)", (pid, f"{pid}@x.com", pid, "", "America/New_York", status, step, first,
                                                   last, ch.PRIMARY))


V3 = {"seq": "v3", "emails": {"1": {"subject": "s", "body": "b"}, "2": {"body": "b"}, "3": {"pending": True},
                              "4": {"body": "b"}}}
OLD = {"emails": {str(i): {"body": "b"} for i in range(1, 7)}}


def due(con, queue):
    return {(r["id"], step) for r, p, step, a in ch.due_items(con, queue, NOON_ET, SENDERS)}


def test_v3_day3_goes_two_days_after_day1():
    con = db()
    home(con, "on_time", 1, "2026-10-31", "2026-10-31")
    home(con, "too_soon", 1, "2026-11-01", "2026-11-01")
    assert due(con, {"on_time": V3, "too_soon": V3}) == {("on_time", 2)}


def test_v3_day7_goes_on_day7_and_is_built_at_send():
    con = db()
    home(con, "h", 2, "2026-10-27", "2026-10-29")
    home(con, "early", 2, "2026-10-28", "2026-10-30")
    assert due(con, {"h": V3, "early": V3}) == {("h", 3)}


def test_pending_day7_dry_run_builds_nothing():
    con = db()
    home(con, "h", 2, "2026-10-27", "2026-10-29")
    r = con.execute("SELECT * FROM prospects").fetchone()
    assert ch.send_one(con, {}, None, r, V3, 3, dry=True) == "dry"


def test_day7_prep_picks_homes_due_by_tomorrow_without_a_fresh_check(monkeypatch):
    from datetime import date
    from surven_outreach.day7 import build as day7
    con = db()
    home(con, "tomorrow", 2, "2026-10-27", "2026-10-29")  # day 7 = 11/2
    home(con, "later", 2, "2026-10-30", "2026-11-01")
    home(con, "checked", 2, "2026-10-27", "2026-10-29")
    monkeypatch.setattr(day7, "load", lambda: {"checked": {"checked": "2026-10-31"}})
    got = ch.day7_due(con, {"tomorrow": V3, "later": V3, "checked": V3}, date(2026, 11, 1))
    assert [r["id"] for r, p in got] == ["tomorrow"]


def test_v3_ends_after_day14_and_old_homes_keep_six():
    con = db()
    home(con, "v3_done", 4, "2026-10-10", "2026-10-23")
    home(con, "old_mid", 4, "2026-10-19", "2026-10-29")
    assert due(con, {"v3_done": V3, "old_mid": OLD}) == {("old_mid", 5)}
    assert ch.last_step(V3) == 4 and ch.last_step(OLD) == 6


def test_day7_email_carries_the_picture_inline():
    from surven_outreach.sender import build_message
    msg = build_message("Joey", "joey@surven.ai", "a@b.com", "Re: s", "text", "<m@x>", {},
                        html='<p>hi</p><img src="cid:day7">', images=[("day7", b"\xff\xd8jpg", "jpeg")])
    parts = [x.get_content_type() for x in msg.walk()]
    assert parts[:2] == ["multipart/alternative", "text/plain"] and "multipart/related" in parts
    img = next(x for x in msg.walk() if x.get_content_type() == "image/jpeg")
    assert img["Content-ID"] == "<day7>" and img.get_content_disposition() == "inline"


def test_day7_html_has_footer_and_picture():
    from surven_outreach.day7.build import _html
    h = _html("Hi Ann, top.", "day7", "ad", 520, "Reply yes.\n\nJoey")
    assert 'src="cid:day7"' in h and "Reply STOP to opt out." in h and "surven.ai" in h
    assert h.index("top.") < h.index("cid:day7") < h.index("Reply yes.")


def test_sheet_reply_stages():
    from surven_outreach.carehome_sheet import reply_stage as st
    assert st("yes", 3, "v3") == "Yes to lead magnet"
    assert st("yes", 1, "v3") == "Warm reply"
    assert st("We have one bed open currently.", 1, "v3") == "Not a fit"
    assert st("We are full", 2, "v3") == "Not a fit"
    assert st("4 rooms", 1, "v3") == "Warm reply"
    assert st("Yes. Call Lynn at +1 (907) 575-7014", 5, "") == "Warm reply"  # phone digits are not a room count


def test_day7_names_and_cities_are_cleaned():
    from surven_outreach.day7.build import ad_name, clean_city, home_name
    assert home_name({"home": "Plaza, The"}) == "The Plaza"
    assert home_name({"home": "Bridgewater At Waterman Village (the)"}) == "The Bridgewater At Waterman Village"
    assert home_name({"home": "Theodore House"}) == "Theodore House"
    assert ad_name("Oakbridge Terrace Assisted Living Residence At Buckingham's Choice") == \
        "Oakbridge Terrace At Buckingham's Choice"
    assert ad_name("Sunstar Village") == "Sunstar Village"
    assert clean_city("Foulk Road Wilmington") == "Wilmington"
    assert clean_city("Washington Court Hou") == "Washington Court House"
    assert clean_city("Mcminnville") == "McMinnville"
    assert clean_city("St. Paul") == "St. Paul"


def test_photo_rank_puts_house_and_residents_before_stock_aerials():
    from surven_outreach.day7.photo import rank
    pics = [{"u": "/m-adobestock-1816725053.jpg", "a": 1e12, "alt": ""},
            {"u": "/promise-live-800.avif", "a": 58320, "alt": "A resident laughing with her daughter"},
            {"u": "/logo.png", "a": 9e5, "alt": ""},
            {"u": "https://static.wixstatic.com/media/fa89d0_d9~mv2.jpg/v1/fill/w_460,h_460/a.jpg", "a": 211600,
             "alt": "Exterior view of the home"}]
    got = rank(pics, "https://home.com/")
    assert not any("adobestock" in u for u in got)  # a stock-library picture is not the home
    assert "https://static.wixstatic.com/media/fa89d0_d9~mv2.jpg" in got[:2]
    assert not any("logo" in u for u in got)


def test_ai_pictures_are_rejected_and_real_ones_kept():
    import io
    from PIL import Image
    from surven_outreach.day7.photo import ai_made, good_photo
    im = Image.effect_noise((1000, 700), 80).convert("RGB")
    buf = io.BytesIO(); im.save(buf, "JPEG"); real = buf.getvalue()
    tagged = real[:2] + b"c2pa jumbf digitalSourceType trainedAlgorithmicMedia generator ChatGPT" + real[2:]
    assert not ai_made(real) and ai_made(tagged)
    assert good_photo(tagged) is None


def test_maps_listing_must_be_the_same_address():
    from surven_outreach.day7.maps import same_place, sized
    assert same_place("205 GILES AVE, OMAHA, TX 75571", "205 Giles Ave, Omaha, TX 75571")
    assert same_place("865 E IRON AVE, DOVER, OH", "865 E Iron Ave, Dover, OH 44622", "Dover")
    assert not same_place("205 GILES AVE, OMAHA, TX 75571", "210 Giles Ave, Omaha, TX 75571", "", "Indigo", "Bob's Auto")
    assert same_place("3160 Gracefield Road, Silver Spring, MD 20904", "3140 Gracefield Rd, Silver Spring, MD 20904",
                      "", "Riderwood Village", "Riderwood Village")  # one campus, two numbers
    assert not same_place("205 GILES AVE, OMAHA, TX 75571", "205 Main St, Dallas, TX 75201")
    assert sized("https://lh3.googleusercontent.com/grass-cs/AB12=w408-h306-k-no") == \
        "https://lh3.googleusercontent.com/grass-cs/AB12=w1600-h1200-k-no"


def test_outside_score_tells_a_building_from_a_room():
    import io
    from PIL import Image
    from surven_outreach.day7.photo import outside
    out = Image.new("RGB", (960, 960), (90, 150, 230))  # sky
    out.paste((70, 140, 60), (0, 500, 960, 960))  # lawn
    room = Image.new("RGB", (960, 960), (225, 215, 200))
    b1, b2 = io.BytesIO(), io.BytesIO()
    out.save(b1, "JPEG"); room.save(b2, "JPEG")
    assert outside(b1.getvalue()) > 0.5 and outside(b2.getvalue()) < 0.05


def test_image_model_tells_what_a_photo_shows():
    import io
    from PIL import Image
    from surven_outreach.day7 import looks
    out = Image.new("RGB", (640, 480), (90, 150, 230))
    out.paste((70, 140, 60), (0, 300, 640, 480))
    buf = io.BytesIO(); out.save(buf, "JPEG")
    seen = looks.look(buf.getvalue())
    if seen is None:  # no model download possible here
        return
    assert set(seen) >= {"building", "people", "inside", "sign", "bad", "top"}
    assert abs(sum(v for k, v in seen.items() if k != "top") - 1) < 0.01


def test_phone_picture_is_the_real_screenshot_with_new_words():
    import io
    from datetime import date
    from PIL import Image
    from surven_outreach.day7 import phone, shot
    base = Image.open(shot.BASE).convert("RGB")
    body = phone.ads_email("Kevin", "Morningstar Of Pasadena", date(2026, 8, 20), date(2026, 10, 24))
    im = Image.open(io.BytesIO(shot.render(phone.draft_text("E38", "Kevin T", body), "E38", "Kevin T"))).convert("RGB")
    assert im.width == base.width
    assert im.crop((0, 0, 924, 330)).tobytes() == base.crop((0, 0, 924, 330)).tobytes()  # status bar + header untouched
    assert im.crop((0, im.height - 180, 924, im.height)).tobytes() == base.crop((0, 1820, 924, 2000)).tobytes()
    long = phone.ads_email("Jo", "The Tradition Lovers Lane Assisted Living And Memory Care", date(2025, 12, 11))
    assert len(shot.wrap(phone.draft_text("E81", "Jo M", long))) > shot.LINES0  # a long name adds lines
    assert "tour by itself" in body
