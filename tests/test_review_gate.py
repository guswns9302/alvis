from app.reviews.gate import ReviewGate


def test_review_gate_detects_git_action():
    decision = ReviewGate().evaluate("Please git commit and git push the result")
    assert decision.needs_review is True
    assert decision.reason == "git action requested"
