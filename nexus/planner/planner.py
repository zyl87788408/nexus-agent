"""Goal decomposition.

Small local models rarely emit clean JSON: they wrap it in markdown fences or
prefix it with prose. Failing outright on that made planning unusable with the
very models this engine targets, so the parser is tolerant - but it still
rejects anything that is not a genuine step list rather than inventing one.
"""

from __future__ import annotations

import json
import re

_MAX_STEPS = 12

PROMPT_TEMPLATE = """You are the planner inside NEXUS.
Break this goal into small executable steps.
Return JSON only:
{{"steps": ["step 1", "step 2"]}}
Goal: {goal}
"""


def _extract_json(text: str) -> str | None:
    """Pull the first JSON object out of prose or a code fence."""
    fenced = re.search(r"```(?:json)?\s*(.+?)```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1)
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end <= start:
        return None
    return text[start:end + 1]


def parse_plan(text: str) -> list[str]:
    """Parse a model response into a list of steps, or raise ValueError."""
    if not isinstance(text, str) or not text.strip():
        raise ValueError("model returned an empty plan")

    candidate = _extract_json(text)
    if candidate is None:
        raise ValueError("model response contains no JSON object")

    try:
        plan = json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise ValueError(f"model returned invalid JSON plan: {exc}") from exc

    if not isinstance(plan, dict):
        raise ValueError("plan must be a JSON object")
    steps = plan.get("steps")
    if not isinstance(steps, list) or not steps:
        raise ValueError("plan has no steps")
    if not all(isinstance(s, str) and s.strip() for s in steps):
        raise ValueError("every step must be a non-empty string")

    cleaned = [s.strip() for s in steps]
    if len(cleaned) > _MAX_STEPS:
        raise ValueError(
            f"plan has {len(cleaned)} steps, above the {_MAX_STEPS} limit"
        )
    return cleaned


class Planner:
    def __init__(self, gateway):
        self.gateway = gateway

    def create_plan(self, goal: str) -> list[str]:
        prompt = PROMPT_TEMPLATE.format(goal=goal)
        response = self.gateway.generate(prompt)
        return parse_plan(response)
