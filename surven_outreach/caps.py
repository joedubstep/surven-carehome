from datetime import date


def can_send_today(cfg, today: date) -> bool:
    return today.weekday() in cfg.send_days


def _count(con, today: date, step=None) -> int:
    q = "SELECT COUNT(*) FROM sends WHERE substr(sent_at,1,10)=?"
    args = [today.isoformat()]
    if step is not None:
        q += " AND step=?"
        args.append(step)
    return con.execute(q, args).fetchone()[0]


def new_slots_remaining(con, cfg, today: date) -> int:
    return max(0, cfg.max_new_per_day - _count(con, today, step=1))


def total_slots_remaining(con, cfg, today: date) -> int:
    return max(0, cfg.max_total_per_day - _count(con, today))
