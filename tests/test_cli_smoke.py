from pathlib import Path

from surven_outreach.cli import cmd_clean
from surven_outreach.db import connect, init_db

FIX = Path(__file__).parent / "fixtures" / "sample_prospects.csv"


def test_cli_clean_imports(tmp_path, capsys):
    cfg = type("C", (), {"source_csv": str(FIX), "suppression_csv": str(tmp_path / "sup.csv")})
    db = connect(str(tmp_path / "t.db")); init_db(db)
    cmd_clean(cfg, db)
    n = db.execute("SELECT COUNT(*) FROM prospects").fetchone()[0]
    assert n >= 3
    assert "imported_new" in capsys.readouterr().out
