"""Per-agent debug trace logger for governance requests/responses."""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from openbox.core.constants import APPROVAL_PATH

_DEBUG_LOG_DIR = "openbox_logs"


class AgentTraceLogger:
    """Writes to openbox_logs/agent_{key_suffix}.log when debug_log is enabled."""

    def __init__(self, key_suffix: str, api_url: str) -> None:
        os.makedirs(_DEBUG_LOG_DIR, exist_ok=True)
        filename = os.path.join(_DEBUG_LOG_DIR, f"agent_{key_suffix}.log")
        self._logger = logging.getLogger(f"openbox.governance.trace.{key_suffix}")
        self._logger.setLevel(logging.DEBUG)
        self._logger.propagate = False
        self._logger.handlers.clear()
        handler = logging.FileHandler(filename, mode="w")
        handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
        self._logger.addHandler(handler)
        self._logger.debug("OpenBox governance trace started — %s", api_url)

    @staticmethod
    def _describe_layer(endpoint: str, request: dict[str, Any]) -> str:
        if endpoint == APPROVAL_PATH:
            return "HITL approval poll"

        event_type = request.get("event_type", "")
        hook = request.get("hook_trigger", False)

        if event_type == "WorkflowStarted":
            return "Session open"
        if event_type in ("WorkflowCompleted", "WorkflowFailed"):
            return f"Session close ({request.get('status', 'unknown')})"
        if event_type == "ActivityStarted" and not hook:
            return "Layer 1 — Pre-task governance"
        if event_type == "ActivityCompleted" and not hook:
            return "Layer 2 — Post-task governance"
        if event_type == "ActivityStarted" and hook:
            spans = request.get("spans") or []
            stage = spans[0].get("stage", "unknown") if spans else "unknown"
            span_name = spans[0].get("name", "") if spans else ""
            if stage == "started":
                return f"Layer 3 — Pre-operation hook ({span_name})"
            return f"Layer 3 — Post-operation hook ({span_name})"

        return event_type or "unknown"

    def log(self, endpoint: str, request: Any, response: Any) -> None:
        layer = self._describe_layer(endpoint, request)
        self._logger.debug(
            "─── POST %s ───\nLAYER: %s\nREQUEST:\n%s\nRESPONSE:\n%s",
            endpoint,
            layer,
            json.dumps(request, indent=2, default=str),
            json.dumps(response, indent=2, default=str),
        )
