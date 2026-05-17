"""Governance configuration."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class GovernanceConfig:
    on_api_error: str = "fail_open"  # "fail_open" or "fail_closed"
    on_fallback: str = "log_warning"  # "log_warning" or "fail_closed"

    api_timeout: float = 30.0
    max_body_size: int | None = None

    send_task_start_event: bool = True  # Layer 1
    send_task_completed_event: bool = True  # Layer 2
    llm_level_governance: bool = True  # Layer 3

    hitl_enabled: bool = True
    hitl_poll_interval: float = 5.0

    exclude_crews: set[str] = field(default_factory=set)
    exclude_crews_hitl: set[str] = field(default_factory=set)

    instrument_databases: bool = True
    db_libraries: set[str] | None = None  # None = all available

    instrument_file_io: bool = False

    debug_log: bool = False
