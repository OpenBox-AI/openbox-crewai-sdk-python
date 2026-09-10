"""Process-level instrumentation engine."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

from openbox.core.client import GovernanceClient
from openbox.core.config import GovernanceConfig
from openbox.core.errors import OpenBoxConfigError
from openbox.utils import validate_api_key_format

if TYPE_CHECKING:
    from crewai import Crew

    from openbox.crewai.crew import GovernedCrew
    from openbox.instrumentation.otel_setup import InstrumentationSettings
    from openbox.instrumentation.span_processor import GovernanceSpanProcessor


class OpenBoxEngine:
    """Owns process-level instrumentation and shared governance configuration."""

    def __init__(self, api_url: str, config: GovernanceConfig) -> None:
        self.api_url = api_url
        self.config = config
        self._client: GovernanceClient | None = None
        self._started = False
        self._span_processor: GovernanceSpanProcessor | None = None
        self._instrumentation_settings: InstrumentationSettings | None = None

    def __enter__(self) -> OpenBoxEngine:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    @property
    def client(self) -> GovernanceClient:
        if self._client is None:
            self._client = GovernanceClient(self.api_url, self.config)
        return self._client

    @property
    def span_processor(self) -> GovernanceSpanProcessor:
        if self._span_processor is None:
            raise OpenBoxConfigError("OpenBoxEngine has not been started")
        return self._span_processor

    def ensure_started(self) -> GovernanceSpanProcessor:
        from openbox.instrumentation.otel_setup import (
            build_instrumentation_settings,
            start_engine_instrumentation,
        )

        settings = build_instrumentation_settings(self.config)
        if not self._started:
            self._span_processor = start_engine_instrumentation(self, settings)
            self._instrumentation_settings = settings
            self._started = True
        elif self._instrumentation_settings != settings:
            raise OpenBoxConfigError(
                "OpenBoxEngine instrumentation settings changed after startup. "
                "Create a new OpenBoxEngine for different DB/file instrumentation."
            )

        return self.span_processor

    def binding_for_trace(self, trace_id: int) -> dict[str, Any] | None:
        if self._span_processor is None:
            return None
        return {
            "span_processor": self._span_processor,
            "governance_client": self.client,
            "config": self.config,
            "ignored_url_prefixes": {self.api_url.rstrip("/")},
        }

    def validate_agent_api_keys(self, agents: list[Any]) -> None:
        seen_envs: set[str] = set()
        for agent in agents:
            env_prefix = getattr(agent, "env_prefix", "")
            env_name = f"{str(env_prefix).rstrip('_')}_API_KEY"
            if env_name in seen_envs:
                continue
            seen_envs.add(env_name)

            key = os.environ.get(env_name)
            if not key:
                raise OpenBoxConfigError(f"Environment variable {env_name!r} is not set")
            validate_api_key_format(key, env_name)

            identity = agent._resolve_identity() if hasattr(agent, "_resolve_identity") else None
            self.client.validate_api_key(key, identity)

    def govern(self, crew: Crew) -> GovernedCrew:
        governed_crew = self._to_governed_crew(crew)
        governed_crew.bind_openbox_engine(self)
        return governed_crew

    def _to_governed_crew(self, crew: Crew) -> GovernedCrew:
        from crewai import Crew

        from openbox.crewai.crew import GovernedCrew

        if isinstance(crew, GovernedCrew):
            return crew
        if not isinstance(crew, Crew):
            raise OpenBoxConfigError(
                "OpenBoxEngine.govern(...) expects a CrewAI Crew instance."
            )

        excluded_fields = {
            "id",
            "usage_metrics",
            "token_usage",
            "execution_logs",
            "execution_context",
            "checkpoint_inputs",
            "checkpoint_train",
            "checkpoint_kickoff_event_id",
        }
        crew_kwargs = {
            field_name: getattr(crew, field_name)
            for field_name in Crew.model_fields
            if field_name not in excluded_fields
        }
        return GovernedCrew.model_construct(**crew_kwargs)

    def close(self) -> None:
        from openbox.instrumentation.otel_setup import teardown_otel_governance

        if self._started:
            teardown_otel_governance()
        self._started = False
        self._instrumentation_settings = None
        self._span_processor = None
        if self._client is not None:
            self._client.close()
            self._client = None
