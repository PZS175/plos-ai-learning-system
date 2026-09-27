"""SM-2 间隔重复算法单元测试（核心算法此前零覆盖）。"""

from __future__ import annotations

from datetime import datetime, timedelta

from plos.core.constants import (
    SM2_FIRST_INTERVAL,
    SM2_MIN_EASE,
    SM2_SECOND_INTERVAL,
)
from plos.core.enums import FlashcardRating
from plos.db.sm2 import SM2Scheduler


def test_forgot_resets_progress_and_eases_down():
    result = SM2Scheduler.review(
        FlashcardRating.FORGOT,
        current_ease=2.5,
        current_interval=10,
        current_repetitions=3,
    )
    assert result.repetitions == 0
    assert result.interval == SM2_FIRST_INTERVAL
    assert result.ease == pytest_approx(2.3)


def test_good_first_review_schedules_second_interval():
    result = SM2Scheduler.review(FlashcardRating.GOOD, current_repetitions=0)
    assert result.repetitions == 1
    assert result.interval == SM2_FIRST_INTERVAL


def test_good_second_review_schedules_third_interval():
    first = SM2Scheduler.review(FlashcardRating.GOOD, current_repetitions=0)
    second = SM2Scheduler.review(
        FlashcardRating.GOOD,
        current_ease=first.ease,
        current_interval=first.interval,
        current_repetitions=first.repetitions,
    )
    assert second.repetitions == 2
    assert second.interval == SM2_SECOND_INTERVAL


def test_ease_moves_toward_limits():
    # 连续「简单」应提升 ease
    result = SM2Scheduler.review(FlashcardRating.EASY, current_ease=2.5, current_repetitions=2)
    assert result.ease > 2.5
    # 连续「忘记」不应低于下限
    ease = 2.5
    for _ in range(10):
        ease = SM2Scheduler.review(FlashcardRating.FORGOT, current_ease=ease).ease
    assert ease == pytest_approx(SM2_MIN_EASE)


def test_next_review_in_future():
    now = datetime(2026, 1, 1, 12, 0, 0)
    result = SM2Scheduler.review(
        FlashcardRating.GOOD,
        current_repetitions=1,
        review_time=now,
    )
    assert result.next_review == now + timedelta(days=result.interval)
    assert result.interval > 0


def pytest_approx(value: float) -> float:
    return round(value, 2)
