"""Unit tests for the pure RTT hysteresis state machine (no I/O)."""

from __future__ import annotations

from middleware.rtt_state import DEGRADED, GOOD, POOR, RttState, Thresholds, advance

# alpha=1.0 -> EWMA is the latest sample, so the dwell logic is tested on crisp
# values. good/degraded up-thresholds 150/500, dwell band 30ms, 3 consecutive.
T = Thresholds(
    good_ms=150,
    degraded_ms=500,
    hysteresis_ms=30,
    transition_samples=3,
    alpha=1.0,
)


def _run(start: RttState, samples: list[float]) -> RttState:
    state = start
    for s in samples:
        state = advance(state, s, T)
    return state


def test_oscillating_around_threshold_does_not_flap():
    # 145/155 straddle the 150 boundary; without 3 consecutive DEGRADED samples
    # the committed tier must stay GOOD.
    state = _run(RttState(), [155, 145, 155, 145, 155, 145])
    assert state.tier == GOOD


def test_three_consecutive_past_threshold_transition_up():
    state = _run(RttState(), [155, 155, 155])
    assert state.tier == DEGRADED


def test_two_consecutive_is_not_enough_to_transition():
    state = _run(RttState(), [155, 155])
    assert state.tier == GOOD
    assert state.pending_tier == DEGRADED and state.pending_count == 2


def test_value_inside_dwell_band_holds_current_tier():
    # 130 is below 150 but above the down-threshold (150-30=120): stay DEGRADED.
    state = _run(RttState(tier=DEGRADED, ewma=200.0), [130, 130, 130])
    assert state.tier == DEGRADED


def test_three_below_down_threshold_transition_down():
    # 110 < 120 (down-threshold): de-escalate to GOOD after 3 consecutive.
    state = _run(RttState(tier=DEGRADED, ewma=200.0), [110, 110, 110])
    assert state.tier == GOOD


def test_large_jump_can_escalate_two_tiers():
    state = _run(RttState(), [800, 800, 800])
    assert state.tier == POOR


def test_ewma_smooths_samples():
    smooth = Thresholds(
        good_ms=150, degraded_ms=500, hysteresis_ms=30, transition_samples=3, alpha=0.5
    )
    state = advance(RttState(ewma=100.0, tier=GOOD), 200.0, smooth)
    assert state.ewma == 150.0  # 0.5*200 + 0.5*100
