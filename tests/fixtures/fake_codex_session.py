from __future__ import annotations

import sys


def emit_result(task_id: str, goal: str) -> None:
    summary = f"synthetic result for {task_id}"
    changed_file = "README.md" if "review" not in goal.lower() else "SUMMARY.md"
    print("ALVIS_RESULT_START", flush=True)
    print("STATUS: done", flush=True)
    print(f"SUMMARY: {summary}", flush=True)
    print("QUESTION_FOR_LEADER:", flush=True)
    print("REQUESTED_CONTEXT:", flush=True)
    print("FOLLOWUP_SUGGESTION:", flush=True)
    print("DEPENDENCY_NOTE:", flush=True)
    print("CHANGED_FILES:", flush=True)
    print(f"- {changed_file}", flush=True)
    print("TEST_RESULTS:", flush=True)
    print("- synthetic test result", flush=True)
    print("RISK_FLAGS:", flush=True)
    print("ALVIS_RESULT_END", flush=True)


def main() -> int:
    current_task_id: str | None = None
    current_goal = ""
    for raw_line in sys.stdin:
        line = raw_line.strip()
        if line.startswith("task_id:"):
            current_task_id = line.split(":", 1)[1].strip()
        elif line.startswith("goal:"):
            current_goal = line.split(":", 1)[1].strip()
        elif line.startswith("- run_id:") and current_task_id:
            emit_result(current_task_id, current_goal)
            current_task_id = None
            current_goal = ""
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
