"""Pick the best model that the machine can actually run.

Two-stage selection:
  1. filter by the hardware memory budget (cheap, no I/O)
  2. rank the survivors by size, largest first - the largest model that fits
     is usually the most capable one - then verify real availability lazily.

The ranking used to be missing entirely, which made selection depend on the
order Ollama happened to return entries in.
"""

from __future__ import annotations

from nexus.hardware.profile import get_profile
from nexus.models.estimator import estimate_model_memory
from nexus.models.registry import discover_models


class ModelSelector:
    def __init__(self, probe=None):
        # Injectable for tests; defaults to the real inference probe.
        self._probe = probe

    def select(self, context_tokens: int | None = None, *, probe: bool = False,
               limit: int = 3) -> dict:
        profile = get_profile()
        hardware = profile["hardware"]
        plan = profile["profile"]
        ctx = context_tokens or plan["context_tokens"]
        budget = plan["model_budget_mb"]

        models = discover_models()
        candidates = []
        for model in models:
            estimate = estimate_model_memory(model, ctx)
            if not estimate:
                # Unknown size: keep it, but flag it so it ranks last.
                if model.get("backend") == "llama.cpp":
                    candidates.append({**model, "estimated_memory_mb": None,
                                       "fits_budget": None})
                continue
            fits = estimate["estimated_memory_mb"] <= budget
            candidates.append({**model, **estimate, "fits_budget": fits})

        fitting = [c for c in candidates if c.get("fits_budget") is True]
        unknown = [c for c in candidates if c.get("fits_budget") is None]
        fitting.sort(key=lambda c: c["estimated_memory_mb"], reverse=True)
        ranked = fitting + unknown

        probe_results = {}
        if probe and ranked:
            probe_fn = self._probe
            if probe_fn is None:
                from nexus.models.probe import probe_model

                probe_fn = probe_model
            verified = []
            for item in ranked:
                result = probe_fn(item["backend"], item["name"])
                probe_results[f"{item['backend']}:{item['name']}"] = result
                if result.get("available"):
                    verified.append({**item, "probe": result})
            ranked = verified

        return {
            "hardware_profile": plan,
            "ram_budget_mb": budget,
            "context_tokens": ctx,
            "discovered_models": models,
            "candidates": ranked[:limit] if limit else ranked,
            "rejected": [c for c in candidates if c.get("fits_budget") is False],
            "probe_results": probe_results,
            "note": "Estimates are approximate, not runtime guarantees.",
        }
