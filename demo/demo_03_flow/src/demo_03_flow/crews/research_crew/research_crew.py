"""Research crew — Senior Researcher + Data Analyst."""

from crewai import Crew, Process, Task
from crewai.agents.agent_builder.base_agent import BaseAgent
from crewai.project import CrewBase, agent, crew, task

from openbox import OpenBoxAgent, OpenBoxTask


@CrewBase
class ResearchCrew:
    """Two agents: senior researcher gathers facts, data analyst extracts insights."""

    agents: list[BaseAgent]
    tasks: list[Task]

    agents_config = "config/agents.yaml"
    tasks_config = "config/tasks.yaml"

    @agent
    def senior_researcher(self) -> OpenBoxAgent:
        return OpenBoxAgent(
            config=self.agents_config["senior_researcher"],  # type: ignore[index]
            env_prefix="OPENBOX_SENIOR_RESEARCHER",
        )

    @agent
    def data_analyst(self) -> OpenBoxAgent:
        return OpenBoxAgent(
            config=self.agents_config["data_analyst"],  # type: ignore[index]
            env_prefix="OPENBOX_DATA_ANALYST",
        )

    @task
    def research_topic(self) -> OpenBoxTask:
        return OpenBoxTask(
            config=self.tasks_config["research_topic"],  # type: ignore[index]
            activity_type="research",
        )

    @task
    def analyze_findings(self) -> OpenBoxTask:
        return OpenBoxTask(
            config=self.tasks_config["analyze_findings"],  # type: ignore[index]
            activity_type="analysis",
        )

    @crew
    def crew(self) -> Crew:
        return Crew(
            name="research-crew",
            agents=self.agents,
            tasks=self.tasks,
            process=Process.sequential,
            verbose=True,
        )
