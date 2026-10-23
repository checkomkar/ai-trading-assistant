"""Brokers a customer can bring their own API keys for (PRD §23.1: the platform never pools a broker account).

`status`: "available" = an adapter exists in this codebase; "planned" = keys can be stored, but no adapter reads them yet.
Costs are what the broker charges the customer for API data, as reported by third-party comparisons on 2026-10-21; they
change, so the UI says "check with your broker" and the broker's page is linked.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Field_:
    key: str
    label: str
    secret: bool = True  # secrets are never sent back to the browser, only "saved"


@dataclass(frozen=True)
class BrokerSpec:
    id: str
    name: str
    fields: tuple[Field_, ...]
    status: str  # available | planned
    data_note: str
    docs_url: str
    daily_login: bool = False  # the access token expires daily and the customer signs in again
    login_kind: str = "oauth"  # oauth: the broker's own sign-in page (Fyers); password: password + 2FA code typed in the app (Shoonya)


CATALOG: dict[str, BrokerSpec] = {b.id: b for b in (
    BrokerSpec("fyers", "Fyers", (Field_("client_id", "App ID (e.g. XXXX-100)", False), Field_("secret_key", "Secret key")),
               "available", "API and live data are free.", "https://myapi.fyers.in/docsv3", daily_login=True),
    BrokerSpec("upstox", "Upstox", (Field_("api_key", "API key", False), Field_("api_secret", "API secret")),
               "planned", "Developer API listed as free, with WebSocket data.", "https://upstox.com/developer/api-documentation"),
    BrokerSpec("angelone", "Angel One (SmartAPI)", (Field_("api_key", "API key", False), Field_("client_code", "Client code", False),
                                                    Field_("totp_secret", "TOTP secret")),
               "planned", "SmartAPI data is free.", "https://smartapi.angelbroking.com/docs"),
    BrokerSpec("dhan", "Dhan", (Field_("client_id", "Client ID", False), Field_("access_token", "Access token")),
               "planned", "Data API is a paid add-on (about ₹499 / month).", "https://dhanhq.co/docs/v2/"),
    BrokerSpec("flattrade", "Flattrade", (Field_("api_key", "API key", False), Field_("api_secret", "API secret")),
               "available", "Free API with live data. Each day you sign in on Flattrade's own page; we never see your password.",
               "https://pi.flattrade.in/docs", daily_login=True),
    BrokerSpec("zerodha", "Zerodha (Kite Connect)", (Field_("api_key", "API key", False), Field_("api_secret", "API secret")),
               "planned", "Live and historical data need the paid Connect plan (about ₹500 / month).", "https://kite.trade/docs/connect/v3/"),
    BrokerSpec("shoonya", "Shoonya (Finvasia)", (Field_("user_id", "User ID (e.g. FA12345)", False), Field_("api_key", "API key (secret code)"),
                                                 Field_("vendor_code", "Vendor code (e.g. FA12345_U)", False),
                                                 Field_("imei", "Device ID (any short text, e.g. striktek)", False)),
               "available", "Free API with live data. Each day you sign in with your password and the 6-digit code; they are never stored.",
               "https://github.com/Shoonya-Dev/ShoonyaApi-py", daily_login=True, login_kind="password"),
)}
