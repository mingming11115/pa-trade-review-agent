import pytest

from app.auth.service import SlidingWindowLimiter, hash_password, verify_password
from app.core.errors import AppError


def test_password_hash_is_salted_and_verifiable():
    first = hash_password("correct horse battery staple")
    second = hash_password("correct horse battery staple")
    assert first != second
    assert verify_password("correct horse battery staple", first)
    assert not verify_password("wrong password", first)


def test_sliding_window_limiter_reports_excess_requests():
    limiter = SlidingWindowLimiter()
    limiter.check("analysis:user", 2, 60, 100.0)
    limiter.check("analysis:user", 2, 60, 101.0)
    with pytest.raises(AppError) as error:
        limiter.check("analysis:user", 2, 60, 102.0)
    assert error.value.code == "rate_limited"


def test_sliding_window_expires_old_events():
    limiter = SlidingWindowLimiter()
    limiter.check("login:ip", 1, 60, 100.0)
    limiter.check("login:ip", 1, 60, 161.0)
