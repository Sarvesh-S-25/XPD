"""Verification and password-reset emails for the hosted website, over SMTP.

Configured entirely from environment variables set in Vercel (never stored in
code or the database):

  SMTP_USER       the Gmail address that sends the mail
  SMTP_PASSWORD   a Google *app password* for that account (not its real password)
  APP_URL         the site's address, e.g. https://instant-kappa-ivory.vercel.app
  SMTP_HOST       default smtp.gmail.com
  SMTP_PORT       default 465 (implicit TLS)

APP_URL is deliberately a setting, never read from the request's Host header:
otherwise anyone could make the site email a link pointing at their own domain.
Links put the token after `#`, so it never reaches a server log.
"""
from __future__ import annotations

import os
import smtplib
import ssl
from email.message import EmailMessage

# Tests replace this with a function that records what would have been sent.
TRANSPORT = None


class MailNotConfigured(RuntimeError):
    pass


def app_url() -> str:
    url = (os.environ.get("APP_URL") or "").strip().rstrip("/")
    if not url.startswith("https://") and not url.startswith("http://127.0.0.1"):
        raise MailNotConfigured("APP_URL must be set to the site's https:// address.")
    return url


def _config() -> dict:
    user = os.environ.get("SMTP_USER", "").strip()
    pw = os.environ.get("SMTP_PASSWORD", "").replace(" ", "")      # Google shows app passwords in groups of 4
    if not user or not pw:
        raise MailNotConfigured("SMTP_USER and SMTP_PASSWORD must be set to send email.")
    return {"user": user, "password": pw, "host": os.environ.get("SMTP_HOST", "smtp.gmail.com"),
            "port": int(os.environ.get("SMTP_PORT", "465"))}


TEMPLATES = {
    "verify": ("Confirm your PromptMeter account",
               "Welcome to PromptMeter.\n\nOpen this link to confirm your email and start using it:\n\n{link}\n\n"
               "It works once and expires in 24 hours. If you didn't sign up, ignore this email."),
    "reset": ("Reset your PromptMeter password",
              "Someone asked to reset the password for this PromptMeter account.\n\n"
              "Open this link to choose a new one:\n\n{link}\n\n"
              "It works once and expires in 1 hour. If it wasn't you, ignore this email — "
              "your password stays the same."),
}


def build(to: str, kind: str, token: str) -> EmailMessage:
    subject, body = TEMPLATES[kind]
    link = f"{app_url()}/#{kind}/{token}"
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = f"PromptMeter <{os.environ.get('SMTP_USER', '').strip()}>"
    msg["To"] = to
    msg.set_content(body.format(link=link))
    return msg


def send(to: str, kind: str, token: str) -> None:
    msg = build(to, kind, token)
    if TRANSPORT is not None:
        TRANSPORT(msg)
        return
    cfg = _config()
    ctx = ssl.create_default_context()
    if cfg["port"] == 465:
        with smtplib.SMTP_SSL(cfg["host"], cfg["port"], context=ctx, timeout=15) as s:
            s.login(cfg["user"], cfg["password"])
            s.send_message(msg)
    else:
        with smtplib.SMTP(cfg["host"], cfg["port"], timeout=15) as s:
            s.starttls(context=ctx)
            s.login(cfg["user"], cfg["password"])
            s.send_message(msg)
