"""
XMPP connectivity test — verifies that a Spade agent can connect
to the local Prosody server using the mother_agent credentials.
"""
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from dotenv import load_dotenv
from spade.agent import Agent

load_dotenv(dotenv_path=Path(__file__).parent.parent.parent / ".env")


class TestAgent(Agent):

    async def setup(self):
        self.connected = True


async def main():
    jid = os.getenv("MOTHER_AGENT_JID", "mother_agent@localhost")
    password = os.getenv("MOTHER_AGENT_PASSWORD", "phase2dev")

    agent = TestAgent(jid=jid, password=password, verify_security=False)

    try:
        await agent.start(auto_register=True)

        if agent.is_alive():
            print("XMPP connection OK")
            await agent.stop()
            sys.exit(0)
        else:
            print("FAIL — agent did not start")
            sys.exit(1)

    except Exception as e:
        print(f"FAIL — {e}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
