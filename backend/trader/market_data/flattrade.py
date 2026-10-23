"""Flattrade live market data. It speaks the same NorenAPI as Shoonya (same websocket frames, REST calls and exchange tokens),
so it reuses that provider and only changes the addresses, the symbol master and the user id."""

from __future__ import annotations

import csv
import io
import urllib.request
from datetime import date
from typing import Any, Callable

from trader.brokers.flattrade import auth as fl
from trader.brokers.shoonya import auth
from trader.domain import Instrument
from trader.market_data.shoonya import ShoonyaMarketDataProvider, instruments_from_rows

MASTER_COLUMNS = {"Token": "Token", "Lotsize": "LotSize", "Symbol": "Symbol", "Tradingsymbol": "TradingSymbol", "Instrument": "Instrument",
                  "Expiry": "Expiry", "Strike": "StrikePrice", "Optiontype": "OptionType"}


def build_instruments(master_csv: str, today: date) -> list[Instrument]:
    """Flattrade's index-derivatives master has other column names than Shoonya's; map them, then share the builder."""
    rows = ({MASTER_COLUMNS[k]: v for k, v in r.items() if k in MASTER_COLUMNS} for r in csv.DictReader(io.StringIO(master_csv)))
    return instruments_from_rows(rows, today)


def _download() -> str:
    with urllib.request.urlopen(fl.MASTER_URL, timeout=60) as resp:  # noqa: S310 - fixed https host
        return resp.read().decode("utf-8", "replace")


def post(path: str, payload: dict, token: str | None = None) -> Any:
    return auth.post(path, payload, token, host=fl.HOST)


class FlattradeMarketDataProvider(ShoonyaMarketDataProvider):
    name = "flattrade"
    label = "Flattrade"
    ws_urls = fl.WS_URLS

    def _user_id(self, settings) -> str:  # noqa: ANN001
        return settings.flattrade_client_id

    def _build(self, text: str, today: date) -> list[Instrument]:
        return build_instruments(text, today)

    def _download_master(self) -> str:
        return _download()

    def _default_http(self) -> Callable[..., Any]:
        return post
