"""Account and session storage. Every query for owned data takes the account id from the session, never from a request."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from trader.accounts import security
from trader.billing.plans import FREE_PLAN, PAID, PLANS
from trader.db.models import AccountRow, AuthSessionRow

LOCAL_ID = "local"


class AccountError(ValueError):
    """A sign-up or login the user can fix; the message is safe to show."""


def _utc(dt: datetime | None) -> datetime | None:
    # SQLite hands timestamps back naive; they are always UTC (CLAUDE.md).
    return None if dt is None else (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc))


@dataclass(frozen=True)
class Principal:
    """Who is making the request, with the plan that applies to them right now."""

    id: str
    email: str  # the email, else the phone number, else empty
    name: str
    is_admin: bool
    plan: str  # effective plan id (a lapsed paid plan reads as the trial)
    period_start: datetime | None  # None = lifetime (trial)
    period_end: datetime | None
    local: bool = False
    subscription: str | None = None


def principal_from_row(row: AccountRow, now: datetime | None = None) -> Principal:
    now = now or datetime.now(timezone.utc)
    plan, start, end = row.plan, _utc(row.period_start), _utc(row.period_end)
    if row.is_admin:
        plan = "owner"
    elif plan in PAID and (end is None or end < now):
        plan, start, end = FREE_PLAN, None, None  # subscription lapsed: back to the (probably used-up) trial limits
    elif plan not in PLANS:
        plan = FREE_PLAN
    if plan in PAID and start is not None:
        while start + timedelta(days=30) <= now:  # usage windows roll monthly from the day the plan began
            start += timedelta(days=30)
    if plan == FREE_PLAN or plan == "owner":
        start = _utc(row.created_at) if plan == FREE_PLAN else None
    return Principal(row.id, row.email or row.phone or "", row.name, row.is_admin, plan, start, end, subscription=row.billing_subscription)


def local_principal() -> Principal:
    return Principal(LOCAL_ID, "local@localhost", "Local", True, "owner", None, None, local=True)


async def create_account(s: AsyncSession, email: str, password: str, name: str, admin_emails: set[str]) -> AccountRow:
    email = security.normalise_email(email)
    if not security.EMAIL_RE.match(email) or len(email) > 254:
        raise AccountError("Enter a valid email address.")
    if (problem := security.password_problem(password)):
        raise AccountError(problem)
    if (await s.execute(select(AccountRow.id).where(AccountRow.email == email))).first() is not None:
        raise AccountError("An account with this email already exists.")
    is_admin = email in admin_emails
    row = AccountRow(id=uuid.uuid4().hex[:16], email=email, name=name.strip()[:80], password_hash=security.hash_password(password),
                     is_admin=is_admin, plan="owner" if is_admin else FREE_PLAN, created_at=datetime.now(timezone.utc))
    s.add(row)
    await s.flush()
    return row


async def authenticate(s: AsyncSession, email: str, password: str) -> AccountRow | None:
    row = (await s.execute(select(AccountRow).where(AccountRow.email == security.normalise_email(email)))).scalars().first()
    ok = security.verify_password(password, (row.password_hash if row else None) or security.DUMMY_HASH)
    ok = ok and row is not None and row.password_hash is not None  # Google / phone-only accounts have no password
    return row if (row is not None and ok and not row.disabled) else None


async def create_session(s: AsyncSession, account_id: str, days: int) -> str:
    token, digest = security.new_token()
    now = datetime.now(timezone.utc)
    s.add(AuthSessionRow(token_hash=digest, account_id=account_id, created_at=now, expires_at=now + timedelta(days=days)))
    await s.execute(delete(AuthSessionRow).where(AuthSessionRow.expires_at < now))  # housekeeping on login
    return token


async def principal_for_token(s: AsyncSession, token: str) -> Principal | None:
    sess = await s.get(AuthSessionRow, security.token_hash(token))
    if sess is None or _utc(sess.expires_at) < datetime.now(timezone.utc):
        return None
    row = await s.get(AccountRow, sess.account_id)
    if row is None or row.disabled:
        return None
    return principal_from_row(row)


async def end_session(s: AsyncSession, token: str) -> None:
    await s.execute(delete(AuthSessionRow).where(AuthSessionRow.token_hash == security.token_hash(token)))


async def get_by_email(s: AsyncSession, email: str) -> AccountRow | None:
    return (await s.execute(select(AccountRow).where(AccountRow.email == security.normalise_email(email)))).scalars().first()


async def set_plan(s: AsyncSession, row: AccountRow, plan: str, days: int = 30, now: datetime | None = None) -> None:
    """Move an account to a plan for `days` days (a manual grant, or a payment the provider confirmed)."""
    if plan not in PLANS:
        raise AccountError(f"Unknown plan '{plan}'.")
    now = now or datetime.now(timezone.utc)
    row.plan = plan
    if plan in PAID:
        row.period_start, row.period_end = now, now + timedelta(days=days)
    else:
        row.period_start = row.period_end = None


async def account_for_google(s: AsyncSession, sub: str, email: str, name: str, admin_emails: set[str],
                             *, may_create: bool = True) -> AccountRow | None:
    """The account for a Google-verified identity. Matches the Google id first, then a verified email (so someone who
    signed up with a password can also use Google). Creates one unless `may_create` is false (closed sign-up)."""
    row = (await s.execute(select(AccountRow).where(AccountRow.google_sub == sub))).scalars().first()
    email = security.normalise_email(email)
    if row is None:
        row = (await s.execute(select(AccountRow).where(AccountRow.email == email))).scalars().first()
        if row is not None:
            row.google_sub = sub
    if row is None:
        if not may_create:
            return None
        is_admin = email in admin_emails
        row = AccountRow(id=uuid.uuid4().hex[:16], email=email, name=name.strip()[:80], google_sub=sub, is_admin=is_admin,
                         plan="owner" if is_admin else FREE_PLAN, created_at=datetime.now(timezone.utc))
        s.add(row)
    await s.flush()
    return None if row.disabled else row


async def account_for_phone(s: AsyncSession, phone: str, name: str, *, may_create: bool = True) -> AccountRow | None:
    row = (await s.execute(select(AccountRow).where(AccountRow.phone == phone))).scalars().first()
    if row is None:
        if not may_create:
            return None
        row = AccountRow(id=uuid.uuid4().hex[:16], phone=phone, name=name.strip()[:80], plan=FREE_PLAN,
                         created_at=datetime.now(timezone.utc))
        s.add(row)
        await s.flush()
    return None if row.disabled else row


# ---- profile ---------------------------------------------------------------------------------------------------------
def subscription_status(row: AccountRow, now: datetime | None = None) -> str:
    """'owner', 'trial', 'active' (paid, renewing), 'ending' (paid, renewal cancelled or never set up) or 'lapsed'."""
    now = now or datetime.now(timezone.utc)
    if row.is_admin:
        return "owner"
    end = _utc(row.period_end)
    if row.plan in PAID:
        if end is None or end < now:
            return "lapsed"
        return "active" if row.billing_subscription else "ending"
    return "trial"


async def profile(s: AsyncSession, account_id: str, current_token: str | None) -> dict | None:
    row = await s.get(AccountRow, account_id)
    if row is None:
        return None
    p = principal_from_row(row)
    now = datetime.now(timezone.utc)
    sessions = (await s.execute(select(AuthSessionRow).where(AuthSessionRow.account_id == account_id,
                                                             AuthSessionRow.expires_at > now))).scalars().all()
    plan = PLANS.get(p.plan) if p.plan in PLANS else None
    return {
        "id": row.id, "name": row.name, "email": row.email, "phone": row.phone, "is_admin": row.is_admin,
        "created_at": _utc(row.created_at).isoformat(),
        "sign_in": [m for m, on in (("password", row.password_hash), ("google", row.google_sub), ("phone", row.phone)) if on],
        "can_set_password": row.email is not None,
        "plan": {"id": p.plan, "name": plan.name if plan else ("Owner" if p.plan == "owner" else p.plan),
                 "price_inr": plan.price_inr if plan else 0},
        "status": subscription_status(row, now),
        "period_start": p.period_start.isoformat() if p.period_start else None,
        "period_end": p.period_end.isoformat() if p.period_end else None,
        "sessions": len(sessions),
        "this_session_expires": next((_utc(x.expires_at).isoformat() for x in sessions
                                      if current_token and x.token_hash == security.token_hash(current_token)), None),
    }


async def rename(s: AsyncSession, account_id: str, name: str) -> None:
    row = await s.get(AccountRow, account_id)
    if row is None:
        raise AccountError("Account not found.")
    name = " ".join(name.split())[:80]
    if not name:
        raise AccountError("Enter a name.")
    row.name = name


async def change_password(s: AsyncSession, account_id: str, current: str, new: str) -> None:
    """Change (or, for a Google / phone account with an email, set) the password."""
    row = await s.get(AccountRow, account_id)
    if row is None:
        raise AccountError("Account not found.")
    if row.password_hash is not None and not security.verify_password(current, row.password_hash):
        raise AccountError("The current password is not right.")
    if row.password_hash is None and row.email is None:
        raise AccountError("This account signs in by phone and has no email to use a password with.")
    if (problem := security.password_problem(new)):
        raise AccountError(problem)
    row.password_hash = security.hash_password(new)


async def end_other_sessions(s: AsyncSession, account_id: str, keep_token: str | None) -> int:
    keep = security.token_hash(keep_token) if keep_token else ""
    res = await s.execute(delete(AuthSessionRow).where(AuthSessionRow.account_id == account_id, AuthSessionRow.token_hash != keep))
    return res.rowcount or 0
