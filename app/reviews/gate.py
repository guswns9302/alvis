from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ReviewDecision:
    needs_review: bool
    reason: str | None = None


class ReviewGate:
    def evaluate(self, summary: str, changed_files: list[str] | None = None, retry_count: int = 0) -> ReviewDecision:
        lowered = summary.lower()
        changed_files = changed_files or []
        if "git push" in lowered or "git commit" in lowered:
            return ReviewDecision(True, "git action requested")
        if "delete " in lowered or "remove " in lowered:
            return ReviewDecision(True, "destructive action detected")
        if retry_count >= 2:
            return ReviewDecision(True, "retry threshold exceeded")
        if len(changed_files) >= 10:
            return ReviewDecision(True, "large change-set")
        return ReviewDecision(False, None)
