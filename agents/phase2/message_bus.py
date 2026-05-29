"""
In-process async message bus replacing SPADE/XMPP messaging.

Agents register by name (not JID) and receive messages via async handlers.
Eliminates the ~30s XMPP connection overhead for co-located agents.
"""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Coroutine, Literal

logger = logging.getLogger(__name__)

Performative = Literal[
    "request", "inform", "escalate", "propose", "refuse", "revise"
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
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


HandlerType = Callable[[ACLMessage], Coroutine[Any, Any, None]]


class MessageBus:
    """Async in-process message bus for agent-to-agent communication."""

    def __init__(self, supabase_client: Any | None = None) -> None:
        self._handlers: dict[str, HandlerType] = {}
        self._message_log: list[ACLMessage] = []
        self._supabase = supabase_client

    def register(self, agent_name: str, handler: HandlerType) -> None:
        """Register an async handler for an agent by name."""
        if agent_name in self._handlers:
            logger.warning(
                "Overwriting existing handler for agent '%s'", agent_name
            )
        self._handlers[agent_name] = handler
        logger.info("Registered handler for agent '%s'", agent_name)

    async def send(self, message: ACLMessage) -> None:
        """Dispatch a message to the registered handler and log it."""
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
            logger.error(
                "No handler registered for receiver '%s'. Message dropped.",
                message.receiver,
            )
            return

        try:
            await handler(message)
        except Exception:
            logger.exception(
                "Handler for '%s' raised an exception on task=%s",
                message.receiver,
                message.task_id,
            )
            raise

        if self._supabase is not None:
            await self._persist(message)

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
    ) -> None:
        """Send the same message to multiple agents in parallel."""
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
        await asyncio.gather(*(self.send(msg) for msg in messages))

    def get_message_log(self) -> list[ACLMessage]:
        """Return the full ordered message log for audit."""
        return list(self._message_log)

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
