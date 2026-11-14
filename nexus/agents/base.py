"""Specialist agent definitions and the contracts they fulfil."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable


@dataclass
class AgentProfile:
    name: str
    role: str
    system_prompt: str
    # Tools this specialist may use; empty means "all registry tools".
    tools: list[str] = field(default_factory=list)


@dataclass
class AgentTask:
    goal: str
    assigned_to: str = ""
    status: str = "pending"
    output: str = ""
    error: str = ""


DEFAULT_PROFILES: dict[str, AgentProfile] = {
    "researcher": AgentProfile(
        name="researcher",
        role="researcher",
        system_prompt=(
            "You are the research specialist. Gather and summarize the facts "
            "needed for the goal. Be concise and concrete."
        ),
    ),
    "engineer": AgentProfile(
        name="engineer",
        role="engineer",
        system_prompt=(
            "You are the engineering specialist. Produce the concrete artifact "
            "or procedure required, with exact steps."
        ),
    ),
    "reviewer": AgentProfile(
        name="reviewer",
        role="reviewer",
        system_prompt=(
            "You are the review specialist. Check the other agents' work for "
            "errors, gaps and contradictions. State a clear verdict."
        ),
    ),
}

Summarizer = Callable[[str], str]
