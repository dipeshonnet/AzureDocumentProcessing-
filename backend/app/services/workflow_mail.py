"""Optional staff invitation delivery. Tokens are never logged."""
import smtplib
import ssl
from email.message import EmailMessage
from urllib.parse import urlsplit

from app.config import AppSettings


def send_invitation(settings: AppSettings, email: str, path: str) -> str:
    origin = urlsplit(settings.public_app_url or "")
    sender = settings.smtp_from or settings.smtp_username
    if not settings.smtp_host or not sender or origin.scheme not in {"http", "https"} or not origin.netloc or origin.username or origin.password or origin.query or origin.fragment:
        return "not_configured"
    try:
        message = EmailMessage()
        message["Subject"] = "Your Admission Analyser staff invitation"
        message["From"] = sender
        message["To"] = email
        message.set_content("You have been invited to the admissions workspace. Activate your membership within seven days:\n\n"
                            + settings.public_app_url.rstrip("/") + "/" + path)
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as smtp:
            if settings.smtp_use_tls:
                smtp.starttls(context=ssl.create_default_context())
            if settings.smtp_username and settings.smtp_password:
                smtp.login(settings.smtp_username, settings.smtp_password)
            smtp.send_message(message)
    except (smtplib.SMTPException, OSError, ValueError):
        return "failed"
    return "sent"
