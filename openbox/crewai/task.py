"""OpenBoxTask — CrewAI Task with activity_type for guardrail matching."""

from __future__ import annotations

from crewai import Task


class OpenBoxTask(Task):
    activity_type: str
