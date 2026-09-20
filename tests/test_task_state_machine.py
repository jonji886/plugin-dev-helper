"""状态机、错误分类、重试策略单元测试。"""

from __future__ import annotations

import pytest

from agent.runtime.errors import ErrorTaxonomy, FailureClass, RetryPolicy, classify_failure
from agent.runtime.state_machine import (
    IllegalTransitionError,
    TaskStateMachine,
    TaskStatus,
)


def test_legal_transitions():
    sm = TaskStateMachine()
    assert sm.can_transition(TaskStatus.CREATED, TaskStatus.GENERATING)
    assert sm.can_transition(TaskStatus.GENERATING, TaskStatus.VALIDATING)
    assert sm.can_transition(TaskStatus.VALIDATING, TaskStatus.BUILDING)
    assert sm.can_transition(TaskStatus.VALIDATING, TaskStatus.REPAIRING)
    assert sm.can_transition(TaskStatus.REPAIRING, TaskStatus.VALIDATING)
    assert sm.can_transition(TaskStatus.BUILDING, TaskStatus.VERIFIED)
    assert sm.can_transition(TaskStatus.VERIFIED, TaskStatus.COMPLETED)


def test_illegal_transition_rejected():
    sm = TaskStateMachine()
    assert not sm.can_transition(TaskStatus.COMPLETED, TaskStatus.REPAIRING)
    assert not sm.can_transition(TaskStatus.CREATED, TaskStatus.COMPLETED)
    with pytest.raises(IllegalTransitionError):
        sm.transition(TaskStatus.COMPLETED, TaskStatus.REPAIRING)


def test_terminal_states():
    sm = TaskStateMachine()
    assert sm.is_terminal(TaskStatus.COMPLETED)
    assert sm.is_terminal(TaskStatus.FAILED)
    assert sm.is_terminal(TaskStatus.CANCELLED)
    assert not sm.is_terminal(TaskStatus.GENERATING)


@pytest.mark.parametrize("taxonomy,expected", [
    (ErrorTaxonomy.PROVIDER, FailureClass.RETRYABLE),
    (ErrorTaxonomy.MCP, FailureClass.RETRYABLE),
    (ErrorTaxonomy.API_HALLUCINATION, FailureClass.REPAIRABLE),
    (ErrorTaxonomy.MANIFEST, FailureClass.REPAIRABLE),
    (ErrorTaxonomy.BUILD, FailureClass.REPAIRABLE),
    (ErrorTaxonomy.PERMISSION, FailureClass.NON_RETRYABLE),
    (ErrorTaxonomy.KNOWLEDGE_MISSING, FailureClass.NON_RETRYABLE),
])
def test_classify_failure(taxonomy, expected):
    assert classify_failure(taxonomy) == expected


def test_retry_policy_respects_taxonomy_and_count():
    policy = RetryPolicy(max_attempts=3)
    # 可重试 Taxonomy，未超次数
    assert policy.should_retry(1, ErrorTaxonomy.PROVIDER)
    assert policy.should_retry(2, ErrorTaxonomy.PROVIDER)
    assert not policy.should_retry(3, ErrorTaxonomy.PROVIDER)
    # 不可重试 Taxonomy 永远不重试
    assert not policy.should_retry(1, ErrorTaxonomy.PERMISSION)


def test_retry_backoff_increases():
    policy = RetryPolicy(max_attempts=5, backoff_base_seconds=1.0, backoff_factor=2.0)
    assert policy.next_backoff_seconds(1) == 1.0
    assert policy.next_backoff_seconds(2) == 2.0
    assert policy.next_backoff_seconds(3) == 4.0
