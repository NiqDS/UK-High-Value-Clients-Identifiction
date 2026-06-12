"""App wiring: health/heartbeat monitor (and main-loop assembly later)."""

from .health import HealthMonitor, HealthState

__all__ = ["HealthMonitor", "HealthState"]
