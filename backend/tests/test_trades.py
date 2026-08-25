from datetime import timezone
import pytest

from app.core.errors import AppError
from app.trades.service import parse_trade_file


VALID_CSV = b"""Id,ContractName,EnteredAt,ExitedAt,EntryPrice,ExitPrice,Fees,PnL,Size,Type,TradeDay,TradeDuration,Commissions
2946841476,MNQU6,2026-08-04T23:22:16+08:00,2026-08-04T23:27:14+08:00,29658.25,29644.25,1.44,56,2,Short,2026-08-04T00:00:00-05:00,297.9,1
"""

EXPORT_CSV = b"""Id,ContractName,EnteredAt,ExitedAt,EntryPrice,ExitPrice,Fees,PnL,Size,Type,TradeDay,TradeDuration,Commissions
2946834976,MNQU6,08/04/2026 23:22:16 +08:00,08/04/2026 23:27:14 +08:00,29658.25,29644.25,1.44,56,2,Short,08/04/2026 00:00:00 -05:00,00:04:57.3209790,1
"""


def test_parse_trade_csv_normalizes_contract_and_timezone() -> None:
    preview = parse_trade_file("trades.csv", VALID_CSV)

    assert preview.valid_rows == 1
    assert preview.invalid_rows == 0
    trade = preview.rows[0]
    assert trade.contract_name == "MNQU6"
    assert trade.symbol_root == "MNQ"
    assert trade.direction == "short"
    assert trade.entered_at.tzinfo == timezone.utc
    assert str(trade.reported_pnl) == "56"


def test_parse_trade_csv_reports_invalid_rows() -> None:
    content = VALID_CSV.replace(b",Short,", b",Unknown,")

    preview = parse_trade_file("trades.csv", content)

    assert preview.valid_rows == 0
    assert preview.invalid_rows == 1
    assert preview.errors[0]["row"] == 2


def test_parse_trade_export_accepts_us_datetime_and_clock_duration() -> None:
    preview = parse_trade_file("trades_export.csv", EXPORT_CSV)

    assert preview.invalid_rows == 0
    trade = preview.rows[0]
    assert trade.entered_at.isoformat() == "2026-08-04T15:22:16+00:00"
    assert str(trade.trade_duration_seconds) == "297.3209790"


def test_numbers_file_with_csv_suffix_is_rejected() -> None:
    with pytest.raises(AppError) as caught:
        parse_trade_file("trades.csv", b"PK\x03\x04Index/Document.iwa")

    assert caught.value.code == "numbers_file_unsupported"
