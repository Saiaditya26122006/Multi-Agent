"""
Agent communication test — verifies two Spade agents can exchange
ACL messages over the local Prosody XMPP server.
"""
import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from dotenv import load_dotenv
from spade.agent import Agent
from spade.behaviour import CyclicBehaviour, OneShotBehaviour
from spade.message import Message

load_dotenv(dotenv_path=Path(__file__).parent.parent.parent / ".env")

SENDER_JID = os.getenv("MOTHER_AGENT_JID", "mother_agent@localhost")
SENDER_PWD = os.getenv("MOTHER_AGENT_PASSWORD", "phase2dev")
RECEIVER_JID = os.getenv("OPPORTUNITY_ANALYST_JID", "opportunity_analyst@localhost")
RECEIVER_PWD = os.getenv("OPPORTUNITY_ANALYST_PASSWORD", "phase2dev")


class ReceiverListenBehaviour(CyclicBehaviour):

    async def run(self):
        msg = await self.receive(timeout=10)
        if msg is None:
            return

        performative = msg.get_metadata("performative")
        content = json.loads(msg.body)
        sender_jid = str(msg.sender)

        if performative == "request":
            self.agent.received_request = True
            self.agent.received_task_id = content.get("task_id", "")

            reply = Message(to=sender_jid.split("/")[0])
            reply.set_metadata("performative", "inform")
            reply.set_metadata("task_id", content.get("task_id", ""))
            reply.body = json.dumps({"status": "received", "task_id": content.get("task_id", "")})
            await self.send(reply)


class SenderListenBehaviour(CyclicBehaviour):

    async def run(self):
        msg = await self.receive(timeout=10)
        if msg is None:
            return

        performative = msg.get_metadata("performative")
        content = json.loads(msg.body)

        if performative == "inform" and content.get("status") == "received":
            self.agent.received_inform = True
            self.agent.inform_task_id = content.get("task_id", "")


class ReceiverAgent(Agent):

    def __init__(self, jid: str, password: str):
        super().__init__(jid, password, verify_security=False)
        self.received_request = False
        self.received_task_id = ""

    async def setup(self):
        self.add_behaviour(ReceiverListenBehaviour())


class SenderAgent(Agent):

    def __init__(self, jid: str, password: str):
        super().__init__(jid, password, verify_security=False)
        self.received_inform = False
        self.inform_task_id = ""

    async def setup(self):
        self.add_behaviour(SenderListenBehaviour())


async def main():
    receiver = ReceiverAgent(jid=RECEIVER_JID, password=RECEIVER_PWD)
    sender = SenderAgent(jid=SENDER_JID, password=SENDER_PWD)

    try:
        await receiver.start(auto_register=True)
        await sender.start(auto_register=True)

        if not receiver.is_alive() or not sender.is_alive():
            print("FAIL — one or both agents failed to start")
            sys.exit(1)

        # Give agents a moment to fully initialize behaviours
        await asyncio.sleep(1)

        # Send request via a OneShotBehaviour (send() only works inside behaviours)
        class SendRequestBehaviour(OneShotBehaviour):
            async def run(self):
                msg = Message(to=RECEIVER_JID)
                msg.set_metadata("performative", "request")
                msg.set_metadata("task_id", "test-001")
                msg.body = json.dumps({
                    "task_name": "test_task",
                    "bp_section": "1",
                    "task_id": "test-001",
                })
                await self.send(msg)

        send_behaviour = SendRequestBehaviour()
        sender.add_behaviour(send_behaviour)
        await send_behaviour.join(timeout=5)

        # Wait for round-trip (max 15 seconds)
        elapsed = 0
        while elapsed < 15:
            if sender.received_inform:
                break
            await asyncio.sleep(0.5)
            elapsed += 0.5

        if not receiver.received_request:
            print("FAIL — receiver did not get the request message")
            sys.exit(1)

        if not sender.received_inform:
            print("FAIL — sender did not get the inform reply")
            sys.exit(1)

        if sender.inform_task_id != "test-001":
            print(f"FAIL — task_id mismatch: expected 'test-001', got '{sender.inform_task_id}'")
            sys.exit(1)

        print("Agent communication OK")

    except Exception as e:
        print(f"FAIL — {e}")
        sys.exit(1)

    finally:
        if sender.is_alive():
            await sender.stop()
        if receiver.is_alive():
            await receiver.stop()


if __name__ == "__main__":
    asyncio.run(main())
