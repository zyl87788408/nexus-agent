from nexus.hardware.profile import get_profile
from nexus.models.registry import discover_models
from nexus.models.estimator import estimate_model_memory

class ModelSelector:
    def select(self, context_tokens=2048):
        profile = get_profile()
        models = discover_models()
        ram = profile["hardware"]["ram_mb"]
        budget = max(512, int(ram * 0.30))
        candidates = []

        for model in models:
            estimate = estimate_model_memory(
                model, context_tokens
            )
            if estimate and estimate["estimated_memory_mb"] <= budget:
                candidates.append({
                    **model,
                    **estimate
                })

        return {
            "hardware_profile": profile["profile"],
            "ram_budget_mb": budget,
            "discovered_models": models,
            "candidates": candidates,
            "note": "Estimates are approximate, not runtime guarantees."
        }
