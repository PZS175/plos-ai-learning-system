"""SM-2 spaced repetition algorithm implementation.

Based on the SuperMemo-2 algorithm (Piotr Wozniak, 1985).

Ratings:
    0 - forgot (blackout)
    1 - hard (incorrect but remembered)
    2 - good (correct with difficulty)
    3 - easy (correct with ease)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from ..core.enums import FlashcardRating
from ..core.constants import (
    SM2_INITIAL_EASE,
    SM2_MIN_EASE,
    SM2_FIRST_INTERVAL,
    SM2_SECOND_INTERVAL,
)


@dataclass
class SM2Result:
    """Result of an SM-2 review update."""
    ease: float
    interval: int          # days
    repetitions: int
    next_review: datetime


class SM2Scheduler:
    """SM-2 scheduler for flashcard reviews."""

    @staticmethod
    def review(
        rating: FlashcardRating,
        current_ease: float = SM2_INITIAL_EASE,
        current_interval: int = 0,
        current_repetitions: int = 0,
        review_time: Optional[datetime] = None,
    ) -> SM2Result:
        """Calculate new SM-2 parameters after a review.

        Args:
            rating: User performance rating.
            current_ease: Current ease factor.
            current_interval: Current interval in days.
            current_repetitions: Number of successful repetitions so far.
            review_time: Time of review. Defaults to now.

        Returns:
            SM2Result with updated ease, interval, repetitions, next review time.
        """
        if review_time is None:
            review_time = datetime.now()

        if rating == FlashcardRating.FORGOT:
            # Failed completely: reset repetitions, keep interval short
            repetitions = 0
            interval = SM2_FIRST_INTERVAL
            ease = max(SM2_MIN_EASE, current_ease - 0.2)
        else:
            # Adjust ease factor based on quality
            quality = rating.value + 1  # map 1..3 -> 2..4
            ease = current_ease + (0.1 - (4 - quality) * (0.08 + (4 - quality) * 0.02))
            ease = max(SM2_MIN_EASE, ease)

            if current_repetitions == 0:
                interval = SM2_FIRST_INTERVAL
            elif current_repetitions == 1:
                interval = SM2_SECOND_INTERVAL
            else:
                interval = int(round(current_interval * ease))

            repetitions = current_repetitions + 1

        next_review = review_time + timedelta(days=interval)
        return SM2Result(
            ease=round(ease, 2),
            interval=interval,
            repetitions=repetitions,
            next_review=next_review,
        )
