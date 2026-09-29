import subprocess
import urllib.request


def push_ntfy(topic_url, title, message, opener=urllib.request.urlopen) -> bool:
    """Send a phone/desktop push via ntfy. Returns False (no-op) if no topic configured."""
    if not topic_url:
        return False
    req = urllib.request.Request(
        topic_url,
        data=message.encode("utf-8"),
        headers={"Title": title, "Priority": "high", "Tags": "fire"},
    )
    opener(req, timeout=10)
    return True


def windows_toast(title, message) -> None:
    """Best-effort native Windows toast via BurntToast. Silently no-ops if unavailable."""
    t = title.replace("'", "''")
    m = message.replace("'", "''")
    ps = (
        "if (Get-Module -ListAvailable -Name BurntToast) { "
        f"Import-Module BurntToast; New-BurntToastNotification -Text '{t}', '{m}' }}"
    )
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
            timeout=15, capture_output=True,
        )
    except Exception:
        pass


def notify_booking(cfg, subject) -> bool:
    """Fire phone + desktop alert for a new Cal.com booking."""
    title = "New call booked 📅"
    message = subject or "Someone booked a call on Cal.com"
    pushed = False
    try:
        pushed = push_ntfy(getattr(cfg, "ntfy_url", ""), title, message)
    except Exception:
        pass
    windows_toast(title, message)
    return pushed


def notify_reply(cfg, business, first_name, snippet="") -> bool:
    """Fire phone + desktop alerts for a GENUINE reply (callers must filter bounces/auto-replies)."""
    title = "Cold email reply"
    who = (f"{first_name} at " if first_name else "") + business
    message = f"{who} replied. {snippet}".strip()
    pushed = False
    try:
        pushed = push_ntfy(getattr(cfg, "ntfy_url", ""), title, message)
    except Exception:
        pass
    windows_toast(title, message)
    return pushed
