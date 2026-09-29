import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Config:
    smtp_host: str
    smtp_port: int
    smtp_user: str
    smtp_password: str
    sender_name: str
    sender_email: str
    max_new_per_day: int
    max_total_per_day: int
    send_days: set[int]
    auto_send: bool
    source_csv: str
    db_path: str
    suppression_csv: str
    imap_host: str
    imap_user: str
    imap_password: str
    ntfy_url: str
    imap_bookings_user: str
    imap_bookings_password: str


def load_config() -> Config:
    return Config(
        smtp_host=os.getenv("SMTP_HOST", "smtp.improvmx.com"),
        smtp_port=int(os.getenv("SMTP_PORT", "587")),
        smtp_user=os.getenv("SMTP_USER", "joey@surven.ai"),
        smtp_password=os.getenv("SMTP_PASSWORD", ""),
        sender_name=os.getenv("SENDER_NAME", "Joseph"),
        sender_email=os.getenv("SENDER_EMAIL", "joey@surven.ai"),
        max_new_per_day=int(os.getenv("MAX_NEW_PER_DAY", "5")),
        max_total_per_day=int(os.getenv("MAX_TOTAL_PER_DAY", "25")),
        send_days={int(x) for x in os.getenv("SEND_DAYS", "0,1,2,3,4,5").split(",") if x.strip()},
        auto_send=os.getenv("AUTO_SEND", "false").lower() == "true",
        source_csv=os.getenv("SOURCE_CSV", ""),
        db_path=os.getenv("DB_PATH", "data/outreach.db"),
        suppression_csv=os.getenv("SUPPRESSION_CSV", "data/suppression.csv"),
        imap_host=os.getenv("IMAP_HOST", "imap.gmail.com"),
        imap_user=os.getenv("IMAP_USER", "survengeo@gmail.com"),
        imap_password=os.getenv("IMAP_PASSWORD", ""),
        ntfy_url=os.getenv("NTFY_URL", ""),
        imap_bookings_user=os.getenv("IMAP_BOOKINGS_USER", ""),
        imap_bookings_password=os.getenv("IMAP_BOOKINGS_PASSWORD", ""),
    )
