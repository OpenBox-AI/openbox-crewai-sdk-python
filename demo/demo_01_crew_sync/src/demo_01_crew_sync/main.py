#!/usr/bin/env python
"""Sync crew demo — 1 governed agent signing every governance request with its DID.

Prerequisite: provision an identity for the researcher agent in the OpenBox
Platform (Agent Settings -> API Access -> Provision Identity). Paste the
returned DID and base64 private key into .env as OPENBOX_RESEARCHER_DID and
OPENBOX_RESEARCHER_PRIVATE_KEY.
"""

import logging

from dotenv import load_dotenv

from demo_01_crew_sync.crew import ResearchCrew
from openbox import create_openbox_engine

load_dotenv()

logging.basicConfig(level=logging.INFO)


def run():
    """Run the governed crew."""
    with create_openbox_engine() as engine:
        crew = ResearchCrew().crew()
        governed_crew = engine.govern(crew)
        result = governed_crew.kickoff()
        print(result)


if __name__ == "__main__":
    run()
