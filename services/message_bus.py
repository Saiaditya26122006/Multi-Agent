"""
Custom Async Message Bus for Agent-to-Agent Communication

Replaces Redis with in-memory asyncio.Queue for low-latency agent messaging.
All persistent state goes to Supabase sessions table (not this bus).

Features:
- Microsecond latency (in-memory)
- Message tracing and audit trail
- ACK/timeout handling
- Dead letter queue for failures
- No external dependencies
"""

import asyncio
import json
import logging
import uuid
from typing import Dict, Any, Optional, Callable
from datetime import datetime
from collections import defaultdict
from dataclasses import dataclass, asdict
from enum import Enum

logger = logging.getLogger(__name__)


class MessageStatus(str, Enum):
    """Message delivery status"""
    PENDING = "pending"
    DELIVERED = "delivered"
    FAILED = "failed"
    TIMEOUT = "timeout"


@dataclass
class MessageEnvelope:
    """Message wrapper with metadata for tracing and reliability"""
    msg_id: str
    sender: str
    recipient: str
    payload: Dict[str, Any]
    session_id: Optional[str] = None
    pipeline_run_id: Optional[str] = None
    sent_at: Optional[str] = None
    status: MessageStatus = MessageStatus.PENDING
    retries: int = 0
    max_retries: int = 3
    timeout: int = 30
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for logging"""
        data = asdict(self)
        data["status"] = self.status.value
        return data


class MessageBus:
    """
    High-performance in-memory message bus for agent coordination.

    Design:
    - Per-agent queues (asyncio.Queue)
    - Automatic timeout/retry handling
    - Full message tracing
    - Dead letter queue for failures
    - No external infrastructure (Redis/AMQP)
    """

    def __init__(self, max_retries: int = 3, default_timeout: int = 30):
        """
        Initialize message bus.

        Args:
            max_retries: Max delivery attempts per message
            default_timeout: Default timeout in seconds
        """
        self.queues: Dict[str, asyncio.Queue] = defaultdict(asyncio.Queue)
        self.message_history: Dict[str, MessageEnvelope] = {}
        self.dead_letter_queue: asyncio.Queue = asyncio.Queue()
        self.max_retries = max_retries
        self.default_timeout = default_timeout
        self.acks: Dict[str, asyncio.Event] = {}
        logger.info("[MessageBus] Initialized with max_retries=%d, timeout=%ds",
                   max_retries, default_timeout)

    async def send(
        self,
        sender: str,
        recipient: str,
        payload: Dict[str, Any],
        session_id: Optional[str] = None,
        pipeline_run_id: Optional[str] = None,
        timeout: Optional[int] = None,
        require_ack: bool = False,
    ) -> str:
        """
        Send message to recipient (non-blocking).

        Args:
            sender: Agent name sending the message
            recipient: Agent name receiving the message
            payload: Message content (dict)
            session_id: Link to CEO session
            pipeline_run_id: Link to pipeline execution
            timeout: Delivery timeout in seconds
            require_ack: Wait for ACK before returning

        Returns:
            Message ID

        Raises:
            asyncio.TimeoutError: If require_ack=True and ACK not received
        """
        msg_id = str(uuid.uuid4())
        envelope = MessageEnvelope(
            msg_id=msg_id,
            sender=sender,
            recipient=recipient,
            payload=payload,
            session_id=session_id,
            pipeline_run_id=pipeline_run_id,
            sent_at=datetime.now().isoformat(),
            timeout=timeout or self.default_timeout,
            max_retries=self.max_retries
        )

        # Store in history
        self.message_history[msg_id] = envelope

        # Log message
        logger.info(
            "[MessageBus] SEND %s → %s (msg_id=%s, session=%s)",
            sender, recipient, msg_id[:8], session_id[:8] if session_id else "N/A"
        )

        # Queue the message
        await self.queues[recipient].put(envelope)

        # Optionally wait for ACK
        if require_ack:
            ack_event = asyncio.Event()
            self.acks[msg_id] = ack_event
            try:
                await asyncio.wait_for(ack_event.wait(), timeout=envelope.timeout)
                envelope.status = MessageStatus.DELIVERED
                logger.info("[MessageBus] ACK received for msg_id=%s", msg_id[:8])
            except asyncio.TimeoutError:
                envelope.status = MessageStatus.TIMEOUT
                await self.dead_letter_queue.put(envelope)
                logger.error("[MessageBus] ACK timeout for msg_id=%s", msg_id[:8])
                raise
            finally:
                self.acks.pop(msg_id, None)

        return msg_id

    async def receive(
        self,
        recipient: str,
        timeout: Optional[int] = None,
    ) -> Optional[MessageEnvelope]:
        """
        Receive next message for recipient (blocking).

        Args:
            recipient: Agent name
            timeout: How long to wait before returning None

        Returns:
            MessageEnvelope or None if timeout
        """
        try:
            envelope = await asyncio.wait_for(
                self.queues[recipient].get(),
                timeout=timeout or self.default_timeout
            )
            logger.info(
                "[MessageBus] RECV %s ← %s (msg_id=%s)",
                recipient, envelope.sender, envelope.msg_id[:8]
            )
            return envelope
        except asyncio.TimeoutError:
            logger.debug("[MessageBus] No message for %s after %ds",
                        recipient, timeout or self.default_timeout)
            return None

    async def send_ack(self, msg_id: str) -> bool:
        """
        Send ACK for received message.

        Args:
            msg_id: Message ID to acknowledge

        Returns:
            True if ACK sent, False if msg not found
        """
        if msg_id not in self.acks:
            logger.warning("[MessageBus] ACK for unknown msg_id=%s", msg_id[:8])
            return False

        self.acks[msg_id].set()
        logger.debug("[MessageBus] ACK sent for msg_id=%s", msg_id[:8])
        return True

    async def retry_message(
        self,
        envelope: MessageEnvelope,
        error: str,
    ) -> bool:
        """
        Retry failed message or send to DLQ.

        Args:
            envelope: Failed message envelope
            error: Error message

        Returns:
            True if retrying, False if sent to DLQ
        """
        envelope.retries += 1
        envelope.error = error

        if envelope.retries < envelope.max_retries:
            # Exponential backoff
            backoff = 2 ** (envelope.retries - 1)
            logger.warning(
                "[MessageBus] Retry %d/%d for msg_id=%s after %ds (error: %s)",
                envelope.retries, envelope.max_retries, envelope.msg_id[:8],
                backoff, error
            )
            await asyncio.sleep(backoff)
            await self.queues[envelope.recipient].put(envelope)
            return True
        else:
            # Max retries exceeded, move to DLQ
            envelope.status = MessageStatus.FAILED
            await self.dead_letter_queue.put(envelope)
            logger.error(
                "[MessageBus] Max retries exceeded for msg_id=%s, moved to DLQ",
                envelope.msg_id[:8]
            )
            return False

    async def get_dlq_message(self, timeout: int = 1) -> Optional[MessageEnvelope]:
        """
        Get next message from dead letter queue.

        Args:
            timeout: How long to wait

        Returns:
            Failed message envelope or None
        """
        try:
            return await asyncio.wait_for(
                self.dead_letter_queue.get(),
                timeout=timeout
            )
        except asyncio.TimeoutError:
            return None

    async def dlq_size(self) -> int:
        """Get number of messages in DLQ"""
        return self.dead_letter_queue.qsize()

    async def queue_size(self, recipient: str) -> int:
        """Get number of pending messages for recipient"""
        return self.queues[recipient].qsize()

    def get_message_history(self, msg_id: str) -> Optional[MessageEnvelope]:
        """Get message from history (for auditing)"""
        return self.message_history.get(msg_id)

    def get_history_by_session(self, session_id: str) -> list:
        """Get all messages for a session (for debugging)"""
        return [
            env for env in self.message_history.values()
            if env.session_id == session_id
        ]

    def get_stats(self) -> Dict[str, Any]:
        """Get message bus statistics"""
        queue_stats = {
            recipient: self.queues[recipient].qsize()
            for recipient in self.queues.keys()
        }
        return {
            "total_messages_sent": len(self.message_history),
            "pending_queues": queue_stats,
            "dlq_size": self.dead_letter_queue.qsize(),
            "history_size": len(self.message_history),
        }

    async def clear(self):
        """Clear all queues (testing only)"""
        self.queues.clear()
        self.message_history.clear()
        while not self.dead_letter_queue.empty():
            try:
                self.dead_letter_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
        logger.info("[MessageBus] Cleared all messages")


# Global instance
message_bus = MessageBus(max_retries=3, default_timeout=30)


async def process_dlq_messages(
    on_error_callback: Optional[Callable] = None,
) -> int:
    """
    Process dead letter queue (called periodically or on-demand).

    Args:
        on_error_callback: Async function to call for each failed message

    Returns:
        Number of messages processed
    """
    count = 0
    while True:
        failed_envelope = await message_bus.get_dlq_message(timeout=0.1)
        if not failed_envelope:
            break

        count += 1
        logger.warning(
            "[MessageBus] Processing DLQ message: %s → %s (error: %s)",
            failed_envelope.sender,
            failed_envelope.recipient,
            failed_envelope.error
        )

        if on_error_callback:
            try:
                await on_error_callback(failed_envelope)
            except Exception as e:
                logger.error("[MessageBus] Error callback failed: %s", e)

    if count > 0:
        logger.info("[MessageBus] Processed %d DLQ messages", count)

    return count
