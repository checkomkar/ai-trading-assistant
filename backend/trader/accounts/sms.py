"""Sending the one-time code. `console` logs it (local testing only); `twilio` sends a real SMS.

Another provider (MSG91, Fast2SMS, Firebase...) is one more small class with `async send(phone, text)`."""

from __future__ import annotations

import httpx

from trader.config import Settings
from trader.logging import get_logger

log = get_logger(__name__)


class SmsUnavailable(RuntimeError):
    """No usable SMS provider, or it refused; the message is safe to show."""


class ConsoleSender:
    """Prints the code to the server log. Never for production: anyone with the log could sign in."""

    name = "console"

    async def send(self, phone: str, text: str) -> None:
        log.warning("sms_console", phone=phone, text=text)


class TwilioSender:
    name = "twilio"

    def __init__(self, sid: str, token: str, sender: str) -> None:
        self.sid, self.token, self.sender = sid, token, sender

    async def send(self, phone: str, text: str) -> None:
        url = f"https://api.twilio.com/2010-04-01/Accounts/{self.sid}/Messages.json"
        try:
            async with httpx.AsyncClient(timeout=15) as c:
                r = await c.post(url, auth=(self.sid, self.token), data={"To": phone, "From": self.sender, "Body": text})
        except httpx.HTTPError as exc:
            raise SmsUnavailable("Could not reach the SMS provider. Try again.") from exc
        if r.status_code >= 400:
            log.error("sms_twilio_failed", status=r.status_code, body=r.text[:300])
            raise SmsUnavailable("The SMS could not be sent. Check the number and try again.")


def build_sender(st: Settings):
    p = st.sms_provider.strip().lower()
    if p == "twilio" and st.twilio_account_sid and st.twilio_auth_token and st.twilio_from:
        return TwilioSender(st.twilio_account_sid, st.twilio_auth_token, st.twilio_from)
    if p == "console" and not st.auth_cookie_secure:  # a secure (production) deployment must use a real provider
        return ConsoleSender()
    return None
