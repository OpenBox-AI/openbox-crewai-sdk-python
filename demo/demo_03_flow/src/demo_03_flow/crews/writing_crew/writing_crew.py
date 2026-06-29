"""Writing crew — Technical Writer produces final report."""

from crewai import Crew, Process, Task
from crewai.agents.agent_builder.base_agent import BaseAgent
from crewai.project import CrewBase, agent, crew, task

from openbox import OpenBoxAgent, OpenBoxTask


@CrewBase
class WritingCrew:
    """Single agent: technical writer produces polished reports."""

    agents: list[BaseAgent]
    tasks: list[Task]

    agents_config = "config/agents.yaml"
    tasks_config = "config/tasks.yaml"

    @agent
    def technical_writer(self) -> OpenBoxAgent:
        return OpenBoxAgent(
            config=self.agents_config["technical_writer"],  # type: ignore[index]
            env_prefix="OPENBOX_TECHNICAL_WRITER",
        )

    @task
    def write_report(self) -> OpenBoxTask:
        return OpenBoxTask(
            config=self.tasks_config["write_report"],  # type: ignore[index]
            activity_type="writing",
        )

    @crew
    def crew(self) -> Crew:
        return Crew(
            name="writing-crew",
            agents=self.agents,
            tasks=self.tasks,
            process=Process.sequential,
            verbose=True,
        )
