"""
Email alerts for high-priority unpaid claims (priority mineral + unpaid).
Uses SMTP; set SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD (or APP_PASSWORD) in .env.
"""

from __future__ import annotations

import logging
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from mining_os.config import settings

log = logging.getLogger("mining_os.email_alerts")


def smtp_configuration_hint() -> str:
    """Human-readable instructions when SMTP env vars are missing."""
    import os

    host = os.getenv("SMTP_HOST", "").strip()
    user = os.getenv("SMTP_USER", "").strip()
    password = (os.getenv("SMTP_PASSWORD") or os.getenv("APP_PASSWORD", "")).strip()
    missing: list[str] = []
    if not host:
        missing.append("SMTP_HOST (e.g. smtp.gmail.com)")
    if not user:
        missing.append("SMTP_USER (your email address)")
    if not password:
        missing.append("SMTP_PASSWORD or APP_PASSWORD (use an app password for Gmail)")
    return (
        "Email is not configured. Add to your project root `.env` file: "
        + "; ".join(missing)
        + ". Optional: SMTP_PORT=587. Restart the backend after saving. "
        "For Gmail, enable 2FA and create an App Password under Google Account → Security."
    )


def send_alert(to_email: str, subject: str, body_text: str, body_html: str | None = None) -> tuple[bool, str]:
    """
    Send a single email.
    Returns (True, "") on success, or (False, error_message) on failure or missing SMTP.
    """
    import os

    host = os.getenv("SMTP_HOST", "")
    port = int(os.getenv("SMTP_PORT", "587"))
    user = os.getenv("SMTP_USER", "")
    password = os.getenv("SMTP_PASSWORD") or os.getenv("APP_PASSWORD", "")
    if not host or not user or not password:
        log.warning("SMTP not configured (SMTP_HOST, SMTP_USER, SMTP_PASSWORD). Skipping send.")
        return False, smtp_configuration_hint()
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = user
        msg["To"] = to_email
        msg.attach(MIMEText(body_text, "plain"))
        if body_html:
            msg.attach(MIMEText(body_html, "html"))
        with smtplib.SMTP(host, port) as server:
            server.starttls()
            server.login(user, password)
            server.sendmail(user, to_email, msg.as_string())
        log.info("Alert email sent to %s", to_email)
        return True, ""
    except Exception as e:
        log.exception("Failed to send alert: %s", e)
        return False, f"SMTP send failed: {e}"


def send_priority_unpaid_alert(areas: list[dict]) -> tuple[bool, str]:
    """
    Send one email summarizing high-priority unpaid areas to ALERT_EMAIL.
    areas: list of area dicts with name, location_plss, minerals, status, blm_case_url, etc.
    """
    if not areas:
        return True, ""
    to = settings.ALERT_EMAIL
    subject = f"Mining_OS: {len(areas)} high-priority unpaid claim(s)"
    lines = [
        "The following areas have a priority mineral and unpaid (or unknown) status:",
        "",
    ]
    for a in areas:
        lines.append(f"- {a.get('name', 'Unknown')}")
        lines.append(f"  Location: {a.get('location_plss') or a.get('location_coords') or 'N/A'}")
        lines.append(f"  Minerals: {', '.join(a.get('minerals') or [])}")
        lines.append(f"  Status: {a.get('status', 'unknown')}")
        if a.get("blm_case_url"):
            lines.append(f"  BLM case: {a['blm_case_url']}")
        lines.append("")
    body_text = "\n".join(lines)
    body_html = "<pre>" + body_text.replace("<", "&lt;").replace(">", "&gt;") + "</pre>"
    return send_alert(to, subject, body_text, body_html)
