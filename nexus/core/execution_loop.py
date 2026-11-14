from nexus.core.lifecycle import Task, TaskStatus

def run_task(task: Task, execute_step, planner=None):
    task.start()
    try:
        steps = planner.create_plan(task.goal) if planner else [task.goal]

        for step in steps:
            if task.steps >= task.max_steps:
                break
            if task.status == TaskStatus.CANCELLED:
                return {"status": "cancelled"}

            result = execute_step(step, task.steps + 1)
            task.steps += 1

            if result.get("done"):
                task.complete()
                return {**result, "status": "completed", "steps": task.steps}

        if task.steps >= task.max_steps:
            task.fail("Maximum step limit reached")
            return {"status": "failed", "error": task.error}

        task.complete()
        return {"status": "completed", "steps": task.steps}

    except Exception as exc:
        task.fail(exc)
        return {
            "status": "failed",
            "error": str(exc),
            "error_type": type(exc).__name__,
        }
