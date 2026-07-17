"""Tests for the async message bus."""

import asyncio
import pytest
from agents.phase2.message_bus import MessageBus, ACLMessage


def _make_msg(sender="a", receiver="b", performative="request", content=None):
    return ACLMessage(
        sender=sender,
        receiver=receiver,
        performative=performative,
        content=content or {},
        task_id="t1",
        session_id="s1",
        pipeline_run_id="r1",
    )


class TestSend:
    def test_send_dispatches_to_handler(self):
        bus = MessageBus()
        received = []

        async def handler(msg):
            received.append(msg)

        bus.register("b", handler)
        asyncio.run(bus.send(_make_msg()))
        assert len(received) == 1
        assert received[0].sender == "a"

    def test_send_to_unregistered_goes_to_dead_letters(self):
        bus = MessageBus()
        asyncio.run(bus.send(_make_msg(receiver="nobody")))
        assert len(bus.dead_letters.get_all()) == 1

    def test_message_log_records_all(self):
        bus = MessageBus()

        async def handler(msg):
            pass

        bus.register("b", handler)

        async def run():
            await bus.send(_make_msg())
            await bus.send(_make_msg())

        asyncio.run(run())
        assert len(bus.get_message_log()) == 2


class TestRequestResponse:
    def test_returns_handler_result(self):
        bus = MessageBus()

        async def handler(msg):
            return {"answer": 42}

        bus.register("b", handler)
        result = asyncio.run(bus.request_response(_make_msg()))
        assert result == {"answer": 42}

    def test_timeout_raises(self):
        bus = MessageBus()

        async def slow_handler(msg):
            await asyncio.sleep(10)

        bus.register("b", slow_handler)
        with pytest.raises(asyncio.TimeoutError):
            asyncio.run(bus.request_response(_make_msg(), timeout=0.1))

    def test_unregistered_raises_value_error(self):
        bus = MessageBus()
        with pytest.raises(ValueError):
            asyncio.run(bus.request_response(_make_msg(receiver="ghost")))


class TestBroadcast:
    def test_broadcast_sends_to_all(self):
        bus = MessageBus()
        results_a = []
        results_b = []

        async def handler_a(msg):
            results_a.append(msg)
            return "a_done"

        async def handler_b(msg):
            results_b.append(msg)
            return "b_done"

        bus.register("x", handler_a)
        bus.register("y", handler_b)

        results = asyncio.run(bus.broadcast(
            sender="mother",
            agent_names=["x", "y"],
            performative="request",
            content={"task": "test"},
            task_id="t1",
            session_id="s1",
            pipeline_run_id="r1",
        ))
        assert len(results_a) == 1
        assert len(results_b) == 1
        assert results == ["a_done", "b_done"]


class TestRegistration:
    def test_registered_agents_list(self):
        bus = MessageBus()

        async def handler(msg):
            pass

        bus.register("agent1", handler)
        bus.register("agent2", handler)
        assert set(bus.registered_agents) == {"agent1", "agent2"}

    def test_unregister(self):
        bus = MessageBus()

        async def handler(msg):
            pass

        bus.register("agent1", handler)
        bus.unregister("agent1")
        assert "agent1" not in bus.registered_agents

    def test_get_messages_for(self):
        bus = MessageBus()

        async def handler(msg):
            pass

        bus.register("b", handler)
        bus.register("c", handler)

        async def run():
            await bus.send(_make_msg(receiver="b", performative="request"))
            await bus.send(_make_msg(receiver="c", performative="inform"))
            await bus.send(_make_msg(receiver="b", performative="escalate"))

        asyncio.run(run())

        b_msgs = bus.get_messages_for("b")
        assert len(b_msgs) == 2

        b_requests = bus.get_messages_for("b", performative="request")
        assert len(b_requests) == 1
