"""Envoi : Telegram (HTML) ou e-mail (SMTP). Les secrets ne sont jamais écrits dans les logs."""
from __future__ import annotations

import logging
import os
import smtplib
import ssl
import time
from email.message import EmailMessage

import httpx

log = logging.getLogger("ai_radar")


class NotifyError(RuntimeError):
    pass


def send_telegram(messages: list[str], token: str | None = None, chat_id: str | None = None,
                  client: httpx.Client | None = None) -> int:
    """Envoie les messages dans l'ordre. Retourne le nombre de messages envoyés."""
    token = token or os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = chat_id or os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        raise NotifyError("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID manquants")
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    own = client is None
    client = client or httpx.Client(timeout=20)
    sent = 0
    try:
        for text in messages:
            payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML",
                       "link_preview_options": {"is_disabled": True}}
            for attempt in range(4):
                try:
                    r = client.post(url, json=payload)
                except httpx.HTTPError as exc:
                    # on ne relaie jamais l'URL (elle contient le token)
                    if attempt == 3:
                        raise NotifyError(f"Telegram injoignable ({type(exc).__name__})") from None
                    time.sleep(2 ** attempt)
                    continue
                if r.status_code == 200:
                    sent += 1
                    break
                if r.status_code == 429:
                    wait = r.json().get("parameters", {}).get("retry_after", 5)
                    time.sleep(min(int(wait) + 1, 30))
                    continue
                if r.status_code >= 500 and attempt < 3:
                    time.sleep(2 ** attempt)
                    continue
                desc = ""
                try:
                    desc = r.json().get("description", "")
                except ValueError:
                    pass
                raise NotifyError(f"Telegram a refusé le message (HTTP {r.status_code} : {desc})")
            else:
                raise NotifyError("Telegram : trop de tentatives (429)")
            time.sleep(0.5)
    finally:
        if own:
            client.close()
    return sent


def send_alert(text: str) -> None:
    """Alerte courte en cas d'erreur fatale. Ne lève jamais d'exception."""
    try:
        send_telegram([f"⚠️ AI Radar a échoué : {text[:300]}"])
    except Exception as exc:  # noqa: BLE001
        log.error("Impossible d'envoyer l'alerte : %s", exc)


def send_email(subject: str, html_body: str) -> None:
    host = os.environ.get("SMTP_HOST")
    user = os.environ.get("SMTP_USER")
    password = os.environ.get("SMTP_PASSWORD")
    to = os.environ.get("EMAIL_TO") or user
    port = int(os.environ.get("SMTP_PORT", "465"))
    if not (host and user and password and to):
        raise NotifyError("SMTP_HOST / SMTP_USER / SMTP_PASSWORD / EMAIL_TO manquants")
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = subject, user, to
    msg.set_content("Ce rapport est au format HTML.")
    msg.add_alternative(html_body, subtype="html")
    try:
        if port == 465:
            with smtplib.SMTP_SSL(host, port, context=ssl.create_default_context(), timeout=30) as s:
                s.login(user, password)
                s.send_message(msg)
        else:
            with smtplib.SMTP(host, port, timeout=30) as s:
                s.starttls(context=ssl.create_default_context())
                s.login(user, password)
                s.send_message(msg)
    except (smtplib.SMTPException, OSError) as exc:
        raise NotifyError(f"envoi e-mail impossible ({type(exc).__name__})") from None
