"""Phone one-time codes: issue, rate-limit and check. Codes are 6 digits, expire in minutes, allow a few guesses and are
single-use. Only a hash is stored."""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from trader.accounts.repo import AccountError, _utc
from trader.db.models import LoginOtpRow

MAX_ATTEMPTS = 5
MAX_SENDS_PER_HOUR = 5
RESEND_AFTER_S = 30
_E164 = re.compile(r"^\+[1-9]\d{9,14}$")


def normalise_phone(raw: str) -> str:
    """'98765 43210' -> '+919876543210' (a bare 10-digit number is taken as Indian); anything else needs its +country code."""
    p = re.sub(r"[\s\-()]", "", raw.strip())
    if re.fullmatch(r"[6-9]\d{9}", p):
        p = "+91" + p
    elif re.fullmatch(r"0[6-9]\d{9}", p):
        p = "+91" + p[1:]
    if not _E164.match(p):
        raise AccountError("Enter a valid mobile number with its country code, for example +91 98765 43210.")
    return p


def _hash(phone: str, code: str) -> str:
    return hashlib.sha256(f"{phone}:{code}".encode()).hexdigest()


async def issue(s: AsyncSession, phone: str, ttl_s: int, now: datetime | None = None) -> str:
    """Create a code for `phone` (returns it so the caller can send it). Limits how often one number can be texted."""
    now = now or datetime.now(timezone.utc)
    recent = (await s.execute(select(LoginOtpRow).where(LoginOtpRow.phone == phone, LoginOtpRow.created_at > now - timedelta(hours=1))
                              .order_by(LoginOtpRow.created_at.desc()))).scalars().all()
    if len(recent) >= MAX_SENDS_PER_HOUR:
        raise AccountError("Too many codes requested for this number. Try again in an hour.")
    if recent and (now - _utc(recent[0].created_at)).total_seconds() < RESEND_AFTER_S:
        raise AccountError("A code was just sent. Wait a few seconds before asking for another.")
    code = f"{secrets.randbelow(10**6):06d}"
    for old in recent:  # only the newest code is valid
        old.consumed = True
    s.add(LoginOtpRow(phone=phone, code_hash=_hash(phone, code), created_at=now, expires_at=now + timedelta(seconds=ttl_s)))
    await s.flush()
    return code


async def check(s: AsyncSession, phone: str, code: str, now: datetime | None = None) -> str | None:
    """None if `code` is the live code for `phone` (and consumes it), else a message safe to show. Returns instead of
    raising so the caller can still COMMIT the attempt counter of a wrong guess."""
    now = now or datetime.now(timezone.utc)
    row = (await s.execute(select(LoginOtpRow).where(LoginOtpRow.phone == phone, LoginOtpRow.consumed.is_(False))
                           .order_by(LoginOtpRow.created_at.desc()).limit(1))).scalars().first()
    if row is None or _utc(row.expires_at) < now:
        return "That code has expired. Ask for a new one."
    if row.attempts >= MAX_ATTEMPTS:
        row.consumed = True
        return "Too many wrong attempts. Ask for a new code."
    row.attempts += 1
    if not hmac.compare_digest(row.code_hash, _hash(phone, code.strip())):
        return "That code is not right."
    row.consumed = True
    return None


async def purge(s: AsyncSession, now: datetime | None = None) -> None:
    from sqlalchemy import delete

    await s.execute(delete(LoginOtpRow).where(LoginOtpRow.expires_at < (now or datetime.now(timezone.utc)) - timedelta(days=1)))
