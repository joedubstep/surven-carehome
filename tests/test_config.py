from surven_outreach.config import load_config


def test_config_parses_caps_and_send_days(monkeypatch):
    monkeypatch.setenv("MAX_NEW_PER_DAY", "5")
    monkeypatch.setenv("MAX_TOTAL_PER_DAY", "25")
    monkeypatch.setenv("SEND_DAYS", "0,1,2,3,4,5")
    monkeypatch.setenv("AUTO_SEND", "false")
    monkeypatch.setenv("SMTP_PASSWORD", "x")
    cfg = load_config()
    assert cfg.max_new_per_day == 5
    assert cfg.max_total_per_day == 25
    assert cfg.send_days == {0, 1, 2, 3, 4, 5}
    assert cfg.auto_send is False
