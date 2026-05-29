from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from openbox.core.client import GovernanceClient
from openbox.core.config import GovernanceConfig
from openbox.core.errors import GovernanceApprovalExpiredError
from openbox.core.types import ApprovalResponse, Verdict


class TestApprovalExpired:
    """Client-side safety net: if approval_expiration_time has passed and
    Core hasn't yet returned halt, the SDK raises GovernanceApprovalExpiredError."""

    def test_expired_approval_raises(self) -> None:
        config = GovernanceConfig(hitl_poll_interval=0.01)
        client = GovernanceClient.__new__(GovernanceClient)
        client._config = config
        client._debug = False
        client._traces = {}

        expired_time = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()

        mock_response = ApprovalResponse(
            verdict=Verdict.REQUIRE_APPROVAL,
            reason="Pending approval",
            approval_id="approval-001",
            approval_expiration_time=expired_time,
        )

        with patch.object(client, "poll_approval", return_value=mock_response):
            with pytest.raises(GovernanceApprovalExpiredError, match="Approval expired"):
                client.wait_for_approval(
                    workflow_id="wf-001",
                    run_id="run-001",
                    activity_id="act-001",
                    api_key="obx_test_abc123",
                )

    def test_not_expired_keeps_polling(self) -> None:
        config = GovernanceConfig(hitl_poll_interval=0.01)
        client = GovernanceClient.__new__(GovernanceClient)
        client._config = config
        client._debug = False
        client._traces = {}

        future_time = (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat()

        pending = ApprovalResponse(
            verdict=Verdict.REQUIRE_APPROVAL,
            reason="Pending",
            approval_id="approval-001",
            approval_expiration_time=future_time,
        )
        approved = ApprovalResponse(
            verdict=Verdict.ALLOW,
            reason="Approved",
            approval_id="approval-001",
        )

        with patch.object(client, "poll_approval", side_effect=[pending, approved]):
            result = client.wait_for_approval(
                workflow_id="wf-001",
                run_id="run-001",
                activity_id="act-001",
                api_key="obx_test_abc123",
            )
            assert result.verdict == Verdict.ALLOW
