from app.analysis.execution.manager import aggregate_review_status
from app.analysis.tasks.models import ExecutionStatus


def test_review_parent_is_completed_when_all_children_succeed() -> None:
    assert aggregate_review_status(["completed", "completed"]) is ExecutionStatus.completed


def test_review_parent_preserves_partial_success_as_warning() -> None:
    assert aggregate_review_status(["completed", "failed"]) is ExecutionStatus.completed_with_warnings


def test_review_parent_fails_when_no_child_succeeds() -> None:
    assert aggregate_review_status(["failed", "cancelled"]) is ExecutionStatus.failed
