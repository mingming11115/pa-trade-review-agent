from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.replay.contracts import TrainingConfig, ReplayCommand


def test_training_defaults_and_finite_costs():
    assert TrainingConfig().initial_cash == Decimal('100000')
    for value in ['NaN', 'Infinity', '-1']:
        with pytest.raises(ValidationError):
            TrainingConfig(fee_per_side=value)


def test_commands_reject_fractional_size_and_owner():
    for extra in [{'quantity': 1.5}, {'quantity': 0}, {'owner': 'someone'}]:
        with pytest.raises(ValidationError):
            ReplayCommand(type='place_order', operation_id='test', expected_revision=0, **extra)
