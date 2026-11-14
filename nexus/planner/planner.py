import json

class Planner:
    def __init__(self, gateway):
        self.gateway = gateway

    def create_plan(self, goal):
        prompt = f"""
You are the planning engine of NEXUS AGENT.
Break this goal into small executable steps.
Return JSON only:
{{"steps": ["step 1", "step 2"]}}
Goal: {goal}
"""
        response = self.gateway.generate(prompt)
        try:
            plan = json.loads(response)
            steps = plan.get("steps")
            if not isinstance(steps, list) or not steps:
                raise ValueError("Invalid plan")
            if not all(isinstance(s, str) and s.strip() for s in steps):
                raise ValueError("Invalid step")
            return steps
        except (json.JSONDecodeError, AttributeError):
            raise ValueError("Model returned invalid JSON plan")
