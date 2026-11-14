"""Task execution with verification, retry, cancellation and resume.

The central invariant: a task is only ``completed`` when every step was
executed *and* its result passed verification. A step executor claiming
``done`` is not sufficient evidence - that is exactly how the previous version
reported success while doing nothing.
"""

from __future__ import annotations

import time

from nexus.core.lifecycle import Task, TaskStatus

# Outcomes of a single step attempt.
STEP_OK = "ok"
STEP_RETRY = "retry"        # transient failure, worth another attempt
STEP_FAILED = "failed"      # permanent failure for this step


def _classify(result: dict) -> str:
    """Decide whether a step result may advance the task."""
    if not isinstance(result, dict):
        return STEP_FAILED
    if result.get("error"):
        return STEP_RETRY if result.get("retryable", True) else STEP_FAILED
    output = result.get("output")
    has_output = output is not None and str(output).strip() != ""
    # Evidence is required, always. A self-declared `verified: True` with no
    # output is exactly the unverifiable claim this gate exists to reject.
    if result.get("done") and has_output:
        return STEP_OK
    if result.get("done") and not has_output:
        return STEP_FAILED
    return STEP_OK if has_output else STEP_FAILED


def run_task(
    task: Task,
    execute_step,
    planner=None,
    *,
    verify=None,
    max_retries: int | None = None,
    journal=None,
    checkpoint=None,
    resume: bool = False,
) -> dict:
    """Run `task` to completion.

    execute_step(step, index) -> dict with keys like
        {"output": str, "verified": bool, "done": bool, "error": str}
    verify(step, result) -> (bool, reason)   optional independent verifier
    """
    retries_allowed = task.max_retries if max_retries is None else max_retries

    steps: list[str] = []
    done_count = 0

    if resume and checkpoint is not None:
        saved = checkpoint.load(task.task_id)
        if saved:
            steps = saved.get("plan") or []
            done_count = int(saved.get("done_count") or 0)
            task.steps = done_count
            if journal:
                journal.record("resumed", done_count=done_count, plan_size=len(steps))

    task.start()
    if journal:
        journal.record("start", goal=task.goal, max_steps=task.max_steps,
                       max_retries=retries_allowed)

    try:
        if not steps:
            steps = _plan_with_retry(task, planner, retries_allowed, journal)
            if journal:
                journal.record("plan", steps=steps, source="planner" if planner else "single")
            if checkpoint is not None:
                checkpoint.save(task.task_id, {"plan": steps, "done_count": 0,
                                               "goal": task.goal})

        plan_truncated = len(steps) > task.max_steps
        if plan_truncated:
            # Execute up to the budget, then fail loudly: silently dropping the
            # remaining steps would report success for unfinished work.
            steps = steps[: task.max_steps]
            if journal:
                journal.record("plan_exceeds_budget", max_steps=task.max_steps)

        results: list[dict] = []
        for index, step in enumerate(steps, start=1):
            if index <= done_count:
                # Already executed in a previous run: do not redo verified work.
                continue
            if task.status is TaskStatus.CANCELLED:
                if journal:
                    journal.record("cancelled", at_step=index)
                return {"status": "cancelled", "steps": task.steps,
                        "results": results}

            outcome = _run_step(
                task, step, index, execute_step, verify,
                retries_allowed, journal,
            )
            if outcome["status"] != "ok":
                task.fail(outcome["error"])
                if journal:
                    journal.record("failed", step=index, error=outcome["error"])
                return {
                    "status": "failed",
                    "steps": task.steps,
                    "error": outcome["error"],
                    "failed_step": index,
                    "results": results + [outcome.get("result") or {}],
                }

            task.steps += 1
            done_count += 1
            results.append(outcome["result"])
            if journal:
                journal.record("step_ok", step=index, attempts=outcome["attempts"])
            if checkpoint is not None:
                checkpoint.save(task.task_id, {"plan": steps, "done_count": done_count,
                                               "goal": task.goal})

        if plan_truncated:
            task.fail(
                f"plan has more than max_steps={task.max_steps} steps; "
                "the remainder was not executed"
            )
            if journal:
                journal.record("failed", error=task.error, reason="max_steps")
            return {"status": "failed", "steps": task.steps, "results": results,
                    "error": task.error}

        # Everything executed and verified. Only here may the task complete.
        task.complete()
        if journal:
            journal.record("completed", steps=task.steps)
        if checkpoint is not None:
            checkpoint.clear(task.task_id)
        return {
            "status": "completed",
            "steps": task.steps,
            "results": results,
            "output": _summarize(results),
            "verified": True,
        }

    except Exception as exc:  # planning failure, backend down, etc.
        if journal:
            journal.record("error", error=str(exc), error_type=type(exc).__name__)
        task.fail(str(exc))
        return {
            "status": "failed",
            "steps": task.steps,
            "error": str(exc),
            "error_type": type(exc).__name__,
        }


def _plan_with_retry(task, planner, max_retries, journal) -> list[str]:
    """Plan with the same retry budget as steps.

    A transient backend hiccup during planning should not abort the task, and a
    permanently invalid plan must still surface as a failure rather than being
    replaced by a silent one-step fallback.
    """
    if planner is None:
        return [task.goal]
    last_error = None
    for attempt in range(1, max_retries + 2):
        try:
            return planner.create_plan(task.goal)
        except Exception as exc:
            last_error = exc
            if journal:
                journal.record("plan_error", attempt=attempt,
                               error=str(exc), error_type=type(exc).__name__)
            if attempt > max_retries:
                break
    raise last_error if last_error else RuntimeError("planning failed")


def _run_step(task, step, index, execute_step, verify, max_retries, journal) -> dict:
    """Execute one step with bounded retries. Returns {status, error, attempts}."""
    last_error = ""
    attempts = 0
    for attempt in range(1, max_retries + 2):
        attempts = attempt
        if task.status is TaskStatus.CANCELLED:
            return {"status": "cancelled", "error": "cancelled", "attempts": attempts}
        try:
            result = execute_step(step, index) or {}
        except Exception as exc:
            last_error = str(exc)
            if journal:
                journal.record("step_error", step=index, attempt=attempt,
                               error=last_error, error_type=type(exc).__name__)
            if attempt <= max_retries:
                time.sleep(0)  # keep the loop non-blocking; real backoff is caller's
                continue
            return {"status": "failed", "error": last_error, "attempts": attempts}

        verdict = _classify(result)
        if verdict == STEP_OK and verify is not None:
            try:
                ok, reason = verify(step, result)
            except Exception as exc:
                ok, reason = False, f"verifier crashed: {exc}"
            result = {**result, "verified": bool(ok), "verify_reason": reason}
            if not ok:
                verdict = STEP_RETRY if attempt <= max_retries else STEP_FAILED
                last_error = f"verification failed: {reason}"
                if journal:
                    journal.record("verify_failed", step=index, attempt=attempt,
                                   reason=reason)
                if verdict == STEP_RETRY:
                    continue
                return {"status": "failed", "error": last_error,
                        "attempts": attempts, "result": result}

        if verdict == STEP_OK:
            return {"status": "ok", "error": "", "attempts": attempts,
                    "result": result}

        last_error = result.get("error") or "step produced no verifiable result"
        if journal:
            journal.record("step_not_ok", step=index, attempt=attempt,
                           verdict=verdict, error=last_error)
        if verdict == STEP_RETRY and attempt <= max_retries:
            continue
        return {"status": "failed", "error": last_error, "attempts": attempts,
                "result": result}

    return {"status": "failed", "error": last_error, "attempts": attempts}


def _summarize(results: list[dict]) -> str:
    parts = []
    for i, result in enumerate(results, start=1):
        text = result.get("output")
        if text is None:
            text = ""
        parts.append(f"[{i}] {str(text).strip()}")
    return "\n".join(parts)
