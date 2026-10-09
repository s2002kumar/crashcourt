"""The scripted chat model: no randomness, and every response names its attempt.

A response is fixed by (thread_id, node, turn, attempt). The attempt comes from the effect sink's
counter for (thread_id, node, turn), which lives outside the worker, so a killed worker's
replacement gets the next attempt rather than repeating the first. When saved state later holds
"attempt 2" while the ledger shows attempt 1 fired the effect, the divergence is visible without
guessing. The message id carries the same key, so a re-run adds a distinguishable message.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from langchain_core.messages import AIMessage


def attempt_key(thread_id: str, node: str, turn: int) -> str:
    """The sink counter a node's model call increments."""
    return f"model/{thread_id}/{node}/{turn}"


@dataclass(frozen=True)
class ScriptedModel:
    #: The responses for each node, one per turn; turns past the end wrap around.
    script: Mapping[str, Sequence[str]]

    def respond(self, *, thread_id: str, node: str, turn: int, attempt: int) -> AIMessage:
        if attempt < 1 or turn < 0:
            raise ValueError("attempts count from 1 and turns from 0")
        try:
            lines = self.script[node]
        except KeyError:
            raise KeyError(
                f"no script for node {node!r}; scripted: {sorted(self.script)}"
            ) from None
        if not lines:
            raise ValueError(f"the script for node {node!r} is empty")
        text = lines[turn % len(lines)]
        return AIMessage(
            content=f"{text} [thread {thread_id}, node {node}, turn {turn}, attempt {attempt}]",
            id=f"ai:{thread_id}:{node}:{turn}:{attempt}",
            response_metadata={"scripted": True, "attempt": attempt, "turn": turn},
        )

    def call(
        self, next_attempt: Callable[[str], int], *, thread_id: str, node: str, turn: int
    ) -> AIMessage:
        """Take the next attempt number from the sink's counter, then respond.

        `next_attempt` maps a counter key to its next value: `Ledger.next_attempt`, or the sink
        client bound to the trial, `functools.partial(client.next_attempt, trial)`.
        """
        attempt = next_attempt(attempt_key(thread_id, node, turn))
        return self.respond(thread_id=thread_id, node=node, turn=turn, attempt=attempt)
