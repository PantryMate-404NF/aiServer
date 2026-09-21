"""임계값 캘리브레이션 검사. 목표 정밀도 미달이면 None, Wilson 하한은 관측보다 낮습니다.

파트 C 가 09-18 에 붙였다가 평가 파이프라인 롤백(PR #14)으로 함께 사라진 것을
되살렸습니다. 검사 내용은 그대로이고 import 경로만 맞췄습니다.
"""

from __future__ import annotations

from features.recommend.ingest.threshold import _wilson_lower, calibrate, min_samples_for


def test_returns_none_when_no_threshold_reaches_the_target() -> None:
    result = calibrate([0.9, 0.8, 0.7, 0.6], [1, 0, 1, 0], target_precision=0.99)
    assert result.threshold is None
    assert result.usable is False
    assert result.n_total == 4


def test_wilson_lower_bound_is_below_the_observed_precision() -> None:
    assert _wilson_lower(10, 10) < 1.0
    assert _wilson_lower(0, 10) == 0.0
    assert _wilson_lower(50, 100) < 0.5


def test_non_conservative_picks_the_lowest_threshold_meeting_precision() -> None:
    result = calibrate(
        [0.9, 0.8, 0.3, 0.2], [1, 1, 0, 0], target_precision=0.99, conservative=False
    )
    assert result.threshold == 0.8
    assert result.precision == 1.0
    assert result.coverage == 0.5


def test_min_samples_grows_with_the_target() -> None:
    assert min_samples_for(0.9) < min_samples_for(0.99)
