"""Configuration for supplier truck arrival scheduling.

All business parameters that affect time windows, arrival-time filtering, and
solver behavior live here so the optimization logic does not hard-code them.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class OptimizerConfig:
    """Runtime configuration for preprocessing and CP-SAT solving.

    `arrival_window_start_hours` and `arrival_window_end_hours` are offsets
    relative to each hourly consumption bucket. The default means deliveries
    may arrive from T-8 through T-3 hours before the consuming hour.
    """

    arrival_window_start_hours: int = -8
    arrival_window_end_hours: int = -3
    forbidden_arrival_hours: frozenset[int] = field(
        default_factory=lambda: frozenset({1, 2, 7, 12, 13, 18})
    )
    solver_time_limit_seconds: float = 30.0
    num_search_workers: int = 8
    maximize_load_weight: int = 1

    def __post_init__(self) -> None:
        if self.arrival_window_start_hours > self.arrival_window_end_hours:
            raise ValueError("arrival_window_start_hours must be <= arrival_window_end_hours")
        if any(hour < 0 or hour > 23 for hour in self.forbidden_arrival_hours):
            raise ValueError("forbidden_arrival_hours must only contain values from 0 to 23")
        if self.solver_time_limit_seconds <= 0:
            raise ValueError("solver_time_limit_seconds must be positive")
        if self.num_search_workers <= 0:
            raise ValueError("num_search_workers must be positive")
        if self.maximize_load_weight < 0:
            raise ValueError("maximize_load_weight must be non-negative")
