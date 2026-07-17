"""
In-process async message bus replacing SPADE/XMPP messaging.

Agents register by name and communicate via async handlers.
Supports fire-and-forget, request-response (with timeout), broadcast,
and dead letter logging. Keeps the ACL message format for audit trails.
"""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Coroutine, Literal

logger = logging.getLogger(__name__)

Performative = Literal[
    "request", "inform", "escalate", "propose", "refuse", "revise", "query"
]


@dataclass
class ACLMessage:
    sender: str
    receiver: str
    performative: Performative
    content: dict[str, Any]
    task_id: str
    session_id: str
    pipeline_run_id: str
    correlation_id: str = ""
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


HandlerType = Callable[[ACLMessage], Coroutine[Any, Any, Any]]


class DeadLetter:
    """Holds messages that could not be delivered."""

    def __init__(self) -> None:
        self._letters: list[ACLMessage] = []

    def add(self, message: ACLMessage, reason: str) -> None:
        self._letters.append(message)
        logger.warning(
            "Dead letter: %s -> %s [%s] reason=%s",
            message.sender, message.receiver, message.performative, reason,
        )

    def get_all(self) -> list[ACLMessage]:
        return list(self._letters)

    def clear(self) -> None:
        self._letters.clear()


class MessageBus:
    """Async in-process message bus for agent-to-agent communication."""

    def __init__(self, supabase_client: Any | None = None) -> None:
        self._handlers: dict[str, HandlerType] = {}
        self._message_log: list[ACLMessage] = []
        self._response_futures: dict[str, asyncio.Future] = {}
        self._supabase = supabase_client
        self.dead_letters = DeadLetter()
        self._correlation_counter: int = 0

    def register(self, agent_name: str, handler: HandlerType) -> None:
        """Register an async handler for an agent by name."""
        if agent_name in self._handlers:
            logger.warning(
                "Overwriting existing handler for agent '%s'", agent_name
            )
        self._handlers[agent_name] = handler
        logger.info("Registered handler for agent '%s'", agent_name)

    def unregister(self, agent_name: str) -> None:
        """Remove an agent's handler."""
        self._handlers.pop(agent_name, None)

    @property
    def registered_agents(self) -> list[str]:
        return list(self._handlers.keys())

    async def send(self, message: ACLMessage) -> None:
        """Fire-and-forget: dispatch message to handler, log to audit trail."""
        self._message_log.append(message)
        logger.debug(
            "Message: %s -> %s [%s] task=%s",
            message.sender,
            message.receiver,
            message.performative,
            message.task_id,
        )

        handler = self._handlers.get(message.receiver)
        if handler is None:
            self.dead_letters.add(message, "no_handler_registered")
            return

        try:
            result = await handler(message)
        except Exception:
            logger.exception(
                "Handler for '%s' raised an exception on task=%s",
                message.receiver,
                message.task_id,
            )
            raise

        # If this is a response to a request_response call, resolve the future
        if message.correlation_id and message.correlation_id in self._response_futures:
            future = self._response_futures.pop(message.correlation_id)
            if not future.done():
                future.set_result(result)

        if self._supabase is not None:
            await self._persist(message)

    async def request_response(
        self,
        message: ACLMessage,
        timeout: float = 60.0,
    ) -> Any:
        """Send a message and await the handler's return value (request-response pattern)."""
        self._correlation_counter += 1
        correlation_id = f"rr_{self._correlation_counter}_{message.task_id}"
        message.correlation_id = correlation_id

        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        self._response_futures[correlation_id] = future

        self._message_log.append(message)
        logger.debug(
            "Request-Response: %s -> %s [%s] task=%s corr=%s",
            message.sender, message.receiver, message.performative,
            message.task_id, correlation_id,
        )

        handler = self._handlers.get(message.receiver)
        if handler is None:
            self._response_futures.pop(correlation_id, None)
            self.dead_letters.add(message, "no_handler_registered")
            raise ValueError(f"No handler for '{message.receiver}'")

        try:
            result = await asyncio.wait_for(handler(message), timeout=timeout)
            self._response_futures.pop(correlation_id, None)
            if self._supabase is not None:
                await self._persist(message)
            return result
        except asyncio.TimeoutError:
            self._response_futures.pop(correlation_id, None)
            self.dead_letters.add(message, f"timeout_after_{timeout}s")
            raise

    async def broadcast(
        self,
        sender: str,
        agent_names: list[str],
        performative: Performative,
        content: dict[str, Any],
        *,
        task_id: str,
        session_id: str,
        pipeline_run_id: str,
    ) -> list[Any]:
        """Send the same message to multiple agents in parallel. Returns results."""
        messages = [
            ACLMessage(
                sender=sender,
                receiver=name,
                performative=performative,
                content=content,
                task_id=task_id,
                session_id=session_id,
                pipeline_run_id=pipeline_run_id,
            )
            for name in agent_names
        ]
        results = await asyncio.gather(
            *(self._send_and_capture(msg) for msg in messages),
            return_exceptions=True,
        )
        return results

    async def _send_and_capture(self, message: ACLMessage) -> Any:
        """Send a message and return the handler's return value."""
        self._message_log.append(message)
        handler = self._handlers.get(message.receiver)
        if handler is None:
            self.dead_letters.add(message, "no_handler_registered")
            return None
        result = await handler(message)
        if self._supabase is not None:
            await self._persist(message)
        return result

    def get_message_log(self) -> list[ACLMessage]:
        """Return the full ordered message log for audit."""
        return list(self._message_log)

    def get_messages_for(
        self, agent_name: str, performative: Performative | None = None
    ) -> list[ACLMessage]:
        """Filter message log by receiver and optionally performative."""
        return [
            m for m in self._message_log
            if m.receiver == agent_name
            and (performative is None or m.performative == performative)
        ]

    async def _persist(self, message: ACLMessage) -> None:
        """Persist message to Supabase events_logs if client is available."""
        try:
            self._supabase.table("events_logs").insert(
                {
                    "agent_name": message.sender,
                    "action": f"message_sent:{message.performative}",
                    "input_summary": f"to={message.receiver} task={message.task_id}",
                    "output_summary": str(message.content)[:500],
                    "timestamp": message.timestamp.isoformat(),
                    "session_id": message.session_id,
                    "pipeline_run_id": message.pipeline_run_id,
                }
            ).execute()
        except Exception:
            logger.exception("Failed to persist message to Supabase")
