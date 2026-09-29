from surven_outreach.bookings import scan_bookings
from surven_outreach.db import connect, init_db


class FakeCal:
    def __init__(self, bookings):
        self._b = bookings

    def find_calcom_bookings(self):
        return self._b


def test_scan_bookings_notifies_once_per_booking(tmp_path):
    db = connect(str(tmp_path / "t.db")); init_db(db)
    cal = FakeCal([
        {"message_id": "<b1@cal>", "subject": "Discovery Call between Surven and Jane Roe"},
        {"message_id": "<b2@cal>", "subject": "Discovery Call between Surven and John Doe"},
    ])
    pushed = []
    fresh = scan_bookings(db, cal, lambda s: pushed.append(s))
    assert len(fresh) == 2 and len(pushed) == 2

    # second scan: same emails still in the inbox, already notified -> no re-push
    fresh2 = scan_bookings(db, cal, lambda s: pushed.append(s))
    assert fresh2 == [] and len(pushed) == 2
