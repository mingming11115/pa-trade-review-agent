from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

Money = Annotated[Decimal, Field(allow_inf_nan=False, max_digits=24, decimal_places=9)]
ComputedMoney = Annotated[Decimal, Field(allow_inf_nan=False)]
Quantity = Annotated[int, Field(strict=True, gt=0, le=100000)]


class Contract(BaseModel):
    model_config = ConfigDict(extra='forbid')


class TrainingConfig(Contract):
    initial_cash: Money = Field(default=Decimal('100000'), gt=0)
    fee_per_side: Money = Field(default=Decimal('0'), ge=0)
    slippage_ticks: int = Field(default=0, strict=True, ge=0, le=10000)
    profit_target: Money | None = Field(default=None, gt=0)
    loss_limit: Money | None = Field(default=None, gt=0)


class InstrumentSpec(Contract):
    symbol: str
    tick_size: Money
    multiplier: Money


class MinuteSnapshot(Contract):
    timestamp: AwareDatetime
    open: Money
    high: Money
    low: Money
    close: Money
    volume: float | None = None
    instrument_id: int = 0
    source: str = 'legacy'
    confirmed: bool = True


class ReplayOrder(Contract):
    id: str
    side: Literal['buy', 'sell']
    quantity: Quantity
    kind: Literal['market', 'limit', 'stop_market', 'stop_limit'] = 'market'
    limit_price: Money | None = None
    stop_price: Money | None = None
    stop_loss: Money | None = None
    take_profit: Money | None = None
    reduce_only: bool = False
    triggered_at: AwareDatetime | None = None
    created_at: AwareDatetime
    status: Literal['pending', 'filled', 'cancelled'] = 'pending'
    reason: str = ''


class ReplayFill(Contract):
    order_id: str
    timestamp: AwareDatetime
    side: Literal['buy', 'sell']
    quantity: Quantity
    price: Money
    fee: Money
    reason: str = ''
    ambiguous: bool = False
    realized_pnl: ComputedMoney = Decimal('0')


class PositionState(Contract):
    quantity: int = 0
    average_price: ComputedMoney = Decimal('0')
    entry_fees: ComputedMoney = Decimal('0')
    stop_loss: Money | None = None
    take_profit: Money | None = None


class AccountState(Contract):
    cash: Decimal = Decimal('100000')
    realized_pnl: Decimal = Decimal('0')
    fees: Decimal = Decimal('0')
    unrealized_pnl: Decimal = Decimal('0')
    equity: Decimal = Decimal('100000')
    peak_equity: Decimal = Decimal('100000')
    max_drawdown: Decimal = Decimal('0')
    closed_trades: int = 0
    wins: int = 0
    profit_target_reached: bool = False
    loss_limit_reached: bool = False


class EngineState(Contract):
    cursor: AwareDatetime
    config: TrainingConfig = Field(default_factory=TrainingConfig)
    position: PositionState = Field(default_factory=PositionState)
    account: AccountState = Field(default_factory=AccountState)
    orders: list[ReplayOrder] = Field(default_factory=list)
    last_price: Money | None = None
    instrument_id: int = 0


class EngineResult(Contract):
    state: EngineState
    fills: list[ReplayFill] = Field(default_factory=list)


class SessionCreate(Contract):
    mode: Literal['training', 'historical']
    symbol: Literal['ES', 'NQ', 'MES', 'MNQ', 'MGC'] = 'ES'
    start: AwareDatetime | None = None
    end: AwareDatetime | None = None
    random_start: bool = False
    config: TrainingConfig = Field(default_factory=TrainingConfig)
    trade_ids: list[str] = Field(default_factory=list, max_length=200)


class ReplayCommand(Contract):
    type: Literal['advance', 'place_order', 'amend_order', 'cancel_order', 'update_protection', 'finish', 'abandon', 'seek']
    operation_id: str = Field(min_length=1, max_length=120)
    expected_revision: int = Field(ge=0)
    period: Literal['1m', '5m', '15m', '30m', '1h', '4h', '1d'] = '1m'
    order_id: str | None = None
    side: Literal['buy', 'sell'] = 'buy'
    kind: Literal['market', 'limit', 'stop_market', 'stop_limit'] = 'market'
    quantity: Quantity = 1
    limit_price: Money | None = None
    stop_price: Money | None = None
    stop_loss: Money | None = None
    take_profit: Money | None = None
    reduce_only: bool = False
    cursor: AwareDatetime | None = None
    target: Literal['previous', 'next'] | None = None

    @model_validator(mode='after')
    def validate_fields(self) -> ReplayCommand:
        if self.type in {'cancel_order', 'amend_order'} and not self.order_id:
            raise ValueError('必须指定订单')
        if self.type in {'place_order', 'amend_order'} and self.kind in {'limit', 'stop_limit'} and self.limit_price is None:
            raise ValueError('限价单必须填写价格')
        if self.type in {'place_order', 'amend_order'} and self.kind in {'stop_market', 'stop_limit'} and self.stop_price is None:
            raise ValueError('条件单必须填写触发价')
        if self.type == 'seek' and self.cursor is None and self.target is None:
            raise ValueError('必须指定回放时间或成交事件')
        return self


class ReplaySaveRequest(Contract):
    name: str = Field(min_length=1, max_length=200)
    operation_id: str = Field(min_length=1, max_length=120)
    expected_revision: int = Field(ge=0)


class ReplayDiscardRequest(Contract):
    expected_revision: int = Field(ge=0)


class ReplayResumeRequest(Contract):
    operation_id: str = Field(min_length=1, max_length=120)


class ReplayAnalysisRequest(Contract):
    operation_id: str = Field(min_length=1, max_length=120)
    periods: list[Literal['1m', '5m', '15m', '30m', '1h', '4h', '1d']] = Field(default_factory=lambda: ['5m'], min_length=1, max_length=7)
