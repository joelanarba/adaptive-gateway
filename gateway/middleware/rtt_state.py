"""
Pure RTT-tier state machine with hysteresis (no I/O — unit-testable).

The detector keeps a per-client passive estimate of link RTT and maps it to a
GOOD/DEGRADED/POOR tier. Two problems with a naive ``classify(ewma)``:

* **Flapping** — a client whose RTT hovers near a threshold flips tier every
  request. Fixed with a *dwell band* (separate up/down thresholds) plus a
  requirement of ``transition_samples`` consecutive samples before committing a
  transition.
* **Cross-worker drift** — the estimate must be shared between workers. This
  module keeps the logic pure so the caller can persist :class:`RttState` (e.g.
  in Redis) and feed it back in.

Classification runs on the EWMA-smoothed value; with ``alpha = 1.0`` the EWMA is
just the latest sample (handy for testing the dwell logic with crisp values).
"""

from __future__ import annotations

from dataclasses import dataclass

GOOD = "GOOD"
DEGRADED = "DEGRADED"
POOR = "POOR"
_ORDER = {GOOD: 0, DEGRADED: 1, POOR: 2}


@dataclass
class RttState:
    """Per-client classification state (persist this across requests/workers)."""

    ewma: float | None = None
    tier: str = GOOD
    pending_tier: str | None = None
    pending_count: int = 0


@dataclass(frozen=True)
class Thresholds:
    """Classifier knobs (sourced from settings)."""

    good_ms: float  # GOOD/DEGRADED up-threshold
    degraded_ms: float  # DEGRADED/POOR up-threshold
    hysteresis_ms: float  # dwell-band width (down-threshold = up - hysteresis)
    transition_samples: int  # consecutive samples required to commit a transition
    alpha: float  # EWMA smoothing factor in (0, 1]


def _candidate_tier(value: float, current: str, t: Thresholds) -> str:
    """Tier implied by ``value`` given the ``current`` tier and the dwell band.

    Escalation (toward POOR) uses the up-thresholds; de-escalation requires
    dropping below the down-thresholds (``up - hysteresis``), so a value inside
    the band keeps the current tier.
    """
    if value >= t.degraded_ms:
        up = POOR
    elif value >= t.good_ms:
        up = DEGRADED
    else:
        up = GOOD

    # Same tier or more severe: escalate immediately (the dwell count still gates).
    if _ORDER[up] >= _ORDER[current]:
        return up

    # Less severe than current: only de-escalate once past the dwell band.
    good_down = t.good_ms - t.hysteresis_ms
    deg_down = t.degraded_ms - t.hysteresis_ms
    if current == POOR:
        if value >= deg_down:
            return POOR
        return GOOD if value < good_down else DEGRADED
    # current == DEGRADED, up == GOOD
    return GOOD if value < good_down else DEGRADED


def advance(state: RttState, sample_ms: float, t: Thresholds) -> RttState:
    """Fold one observed link-RTT sample into the state and return the new state.

    Updates the EWMA, derives the candidate tier from it, and commits a tier
    transition only after ``transition_samples`` consecutive agreeing samples.
    """
    ewma = (
        sample_ms
        if state.ewma is None
        else t.alpha * sample_ms + (1.0 - t.alpha) * state.ewma
    )
    candidate = _candidate_tier(ewma, state.tier, t)

    if candidate == state.tier:
        return RttState(ewma=ewma, tier=state.tier, pending_tier=None, pending_count=0)

    count = state.pending_count + 1 if candidate == state.pending_tier else 1
    if count >= t.transition_samples:
        return RttState(ewma=ewma, tier=candidate, pending_tier=None, pending_count=0)
    return RttState(
        ewma=ewma, tier=state.tier, pending_tier=candidate, pending_count=count
    )
