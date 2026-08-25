from datetime import datetime, timezone

import pytest

from app.core.contracts import resolve_contract_symbol


def at(year: int, month: int, day: int) -> datetime:
    return datetime(year, month, day, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    ("root", "start", "end", "expected"),
    [
        ("ES", at(2022, 6, 1), at(2022, 6, 6), "ESM2"),
        ("MES", at(2022, 6, 18), at(2022, 6, 20), "MESU2"),
        ("NQ", at(2026, 8, 1), at(2026, 8, 7), "NQU6"),
        ("GC", at(2022, 6, 1), at(2022, 6, 6), "GCQ2"),
        ("MGC", at(2022, 12, 1), at(2022, 12, 6), "MGCG3"),
        ("CL", at(2022, 6, 1), at(2022, 6, 6), "CLN2"),
        ("MCL", at(2022, 12, 1), at(2022, 12, 6), "MCLF3"),
    ],
)
def test_resolve_contract_symbol(root, start, end, expected) -> None:
    assert resolve_contract_symbol(root, start, end) == expected


def test_resolve_contract_symbol_preserves_concrete_symbol() -> None:
    assert resolve_contract_symbol("ESM2", at(2022, 6, 1), at(2022, 6, 6)) == "ESM2"
