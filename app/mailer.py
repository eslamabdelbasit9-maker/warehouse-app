"""إرسال الإيميلات: Microsoft Graph أو SMTP أو صندوق داخلي للتجربة.
كل رسالة تُسجَّل في جدول mail_log."""
import smtplib
import threading
from email.mime.text import MIMEText

import httpx
import msal

from .config import settings
from .db import SessionLocal
from .models import MailLog

_token_cache = {}


def _graph_token():
    app = _token_cache.get("app")
    if app is None:
        app = msal.ConfidentialClientApplication(
            settings.ENTRA_CLIENT_ID,
            authority=f"https://login.microsoftonline.com/{settings.ENTRA_TENANT_ID}",
            client_credential=settings.ENTRA_CLIENT_SECRET,
        )
        _token_cache["app"] = app
    res = app.acquire_token_for_client(scopes=["https://graph.microsoft.com/.default"])
    if "access_token" not in res:
        raise RuntimeError(res.get("error_description", "تعذر الحصول على توكن Graph"))
    return res["access_token"]


def _send_graph(to, subject, html):
    body = {
        "message": {
            "subject": subject,
            "body": {"contentType": "HTML", "content": html},
            "toRecipients": [{"emailAddress": {"address": a}} for a in to],
        },
        "saveToSentItems": True,
    }
    r = httpx.post(
        f"https://graph.microsoft.com/v1.0/users/{settings.MAIL_SENDER}/sendMail",
        json=body, headers={"Authorization": f"Bearer {_graph_token()}"}, timeout=30,
    )
    if r.status_code >= 300:
        raise RuntimeError(f"Graph {r.status_code}: {r.text[:300]}")


def _send_brevo(to, subject, html):
    r = httpx.post("https://api.brevo.com/v3/smtp/email", timeout=30,
                   headers={"api-key": settings.BREVO_API_KEY, "accept": "application/json"},
                   json={"sender": {"email": settings.MAIL_SENDER, "name": settings.MAIL_SENDER_NAME},
                         "to": [{"email": a} for a in to], "subject": subject, "htmlContent": html})
    if r.status_code >= 300:
        raise RuntimeError(f"Brevo {r.status_code}: {r.text[:300]}")


def _send_smtp(to, subject, html):
    msg = MIMEText(html, "html", "utf-8")
    msg["Subject"] = subject
    msg["From"] = settings.MAIL_SENDER or settings.SMTP_USER
    msg["To"] = ", ".join(to)
    with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=30) as s:
        s.starttls()
        if settings.SMTP_USER:
            s.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
        s.sendmail(msg["From"], to, msg.as_string())


def _deliver(log_id, to, subject, html):
    db = SessionLocal()
    try:
        log = db.get(MailLog, log_id)
        try:
            if settings.MAIL_MODE == "graph":
                _send_graph(to, subject, html)
            elif settings.MAIL_MODE == "smtp":
                _send_smtp(to, subject, html)
            elif settings.MAIL_MODE == "brevo":
                _send_brevo(to, subject, html)
            log.status = "sent" if settings.MAIL_MODE in ("graph", "smtp", "brevo") else "outbox"
        except Exception as e:  # noqa: BLE001
            log.status, log.error = "failed", str(e)[:1000]
        db.commit()
    finally:
        db.close()


def send_mail(to, subject, html, background=True):
    to = sorted({a.strip() for a in to if a and a.strip()})
    if not to:
        return
    db = SessionLocal()
    try:
        log = MailLog(to=", ".join(to), subject=subject, html=html, status="queued")
        db.add(log)
        db.commit()
        log_id = log.id
    finally:
        db.close()
    if background and settings.MAIL_MODE in ("graph", "smtp", "brevo"):
        threading.Thread(target=_deliver, args=(log_id, to, subject, html), daemon=True).start()
    else:
        _deliver(log_id, to, subject, html)
