from decimal import Decimal
from app.replay.contracts import InstrumentSpec

# CME outright futures specifications (not calendar spread ticks):
# https://www.cmegroup.com/articles/faqs/micro-e-mini-equity-index-futures-frequently-asked-questions.html
# https://www.cmegroup.com/markets/metals/precious/e-micro-gold.contractSpecs.html
PARAMETERS = {'ES': ('0.25', '50'), 'NQ': ('0.25', '20'), 'MES': ('0.25', '5'),
              'MNQ': ('0.25', '2'), 'MGC': ('0.1', '10')}


def get_instrument(symbol: str) -> InstrumentSpec:
    tick, multiplier = PARAMETERS[symbol]
    return InstrumentSpec(symbol=symbol, tick_size=Decimal(tick), multiplier=Decimal(multiplier))
