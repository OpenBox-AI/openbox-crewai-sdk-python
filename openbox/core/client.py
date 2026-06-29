"""Governance HTTP client for the OpenBox Core API."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any

import httpx

from openbox._version import __version__
from openbox.core.aip_signing import AgentIdentity, build_signed_identity_headers
from openbox.core.config import GovernanceConfig
from openbox.core.constants import APPROVAL_PATH, AUTH_VALIDATE_PATH, EVALUATE_PATH
from openbox.core.errors import (
    GovernanceAPIError,
    GovernanceApprovalExpiredError,
    OpenBoxAuthError,
    OpenBoxNetworkError,
)
from openbox.core.trace_logger import AgentTraceLogger
from openbox.core.types import ApprovalResponse, GovernanceResponse, Verdict
from openbox.utils import parse_iso8601_utc, validate_url

logger = logging.getLogger("openbox")


class GovernanceClient:
    """HTTP client for the OpenBox Core governance API."""

    def __init__(self, api_url: str, config: GovernanceConfig) -> None:
        self._api_url = validate_url(api_url)
        self._config = config
        self._http = httpx.Client(timeout=config.api_timeout)
        self._async_http = httpx.AsyncClient(timeout=config.api_timeout)
        self._debug = config.debug_log
        self._traces: dict[str, AgentTraceLogger] = {}

    def _get_trace(self, api_key: str) -> AgentTraceLogger | None:
        if not self._debug:
            return None
        suffix = api_key[-8:]
        if suffix not in self._traces:
            self._traces[suffix] = AgentTraceLogger(suffix, self._api_url)
        return self._traces[suffix]

    def validate_api_key(
        self,
        api_key: str,
        identity: AgentIdentity | None = None,
    ) -> None:
        url = f"{self._api_url}{AUTH_VALIDATE_PATH}"
        headers = self.build_request_headers(
            api_key=api_key,
            method="GET",
            pathname=AUTH_VALIDATE_PATH,
            body="",
            identity=identity,
        )

        try:
            response = self._http.get(url, headers=headers)
        except httpx.HTTPError as exc:
            raise OpenBoxNetworkError(
                f"Cannot reach OpenBox Core at {self._api_url}: {exc}"
            ) from exc

        if response.status_code == 200:
            return
        if response.status_code in (401, 403):
            raise OpenBoxAuthError("Invalid API key. Check your API key at dashboard.openbox.ai")
        raise OpenBoxNetworkError(
            f"Cannot reach OpenBox Core at {self._api_url}: HTTP {response.status_code}"
        )

    def evaluate(
        self,
        payload: dict[str, Any],
        api_key: str,
        identity: AgentIdentity | None = None,
    ) -> GovernanceResponse:
        """POST /api/v1/governance/evaluate

        On network error: fail_open returns ALLOW, fail_closed raises.
        On fallback_used + on_fallback=fail_closed: overrides to BLOCK.
        """
        url = f"{self._api_url}{EVALUATE_PATH}"
        body = json.dumps(payload)
        headers = self.build_request_headers(
            api_key=api_key,
            method="POST",
            pathname=EVALUATE_PATH,
            body=body,
            identity=identity,
        )

        try:
            response = self._http.post(url, content=body, headers=headers)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            logger.warning(
                "Governance API returned %s for %s: %s",
                exc.response.status_code,
                url,
                exc.response.text[:200],
            )
            return self._handle_api_error(exc)
        except httpx.HTTPError as exc:
            logger.warning("Governance API unreachable at %s: %s", url, exc)
            return self._handle_api_error(exc)

        data = response.json()
        trace = self._get_trace(api_key)
        if trace:
            trace.log(EVALUATE_PATH, payload, data)
        gov_response = GovernanceResponse.from_dict(data)

        if gov_response.fallback_used and self._config.on_fallback == "fail_closed":
            logger.warning(
                "Core returned fallback_used=true; on_fallback=fail_closed overrides to BLOCK"
            )
            gov_response.verdict = Verdict.BLOCK

        return gov_response

    def poll_approval(
        self,
        workflow_id: str,
        run_id: str,
        activity_id: str,
        api_key: str,
        identity: AgentIdentity | None = None,
    ) -> ApprovalResponse:
        """Single poll: POST /api/v1/governance/approval"""
        url = f"{self._api_url}{APPROVAL_PATH}"
        payload = {
            "workflow_id": workflow_id,
            "run_id": run_id,
            "activity_id": activity_id,
        }
        body = json.dumps(payload)
        headers = self.build_request_headers(
            api_key=api_key,
            method="POST",
            pathname=APPROVAL_PATH,
            body=body,
            identity=identity,
        )

        try:
            response = self._http.post(url, content=body, headers=headers)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            logger.warning("Approval poll failed at %s: %s", url, exc)
            raise GovernanceAPIError(f"Approval poll failed: {exc}") from exc

        data = response.json()
        trace = self._get_trace(api_key)
        if trace:
            trace.log(APPROVAL_PATH, payload, data)
        return ApprovalResponse.from_dict(data)

    def wait_for_approval(
        self,
        workflow_id: str,
        run_id: str,
        activity_id: str,
        api_key: str,
        identity: AgentIdentity | None = None,
    ) -> GovernanceResponse:
        """Blocking HITL poll loop. Polls until the human decides or the window expires."""
        while True:
            approval = self.poll_approval(workflow_id, run_id, activity_id, api_key, identity)

            if approval.verdict == Verdict.ALLOW:
                return GovernanceResponse(
                    verdict=Verdict.ALLOW,
                    reason=approval.reason,
                    approval_id=approval.approval_id,
                )

            if approval.verdict.should_stop():
                return GovernanceResponse(
                    verdict=approval.verdict,
                    reason=approval.reason,
                    approval_id=approval.approval_id,
                )

            if approval.approval_expiration_time:
                try:
                    expiry = parse_iso8601_utc(approval.approval_expiration_time)
                    if datetime.now(timezone.utc) >= expiry:
                        raise GovernanceApprovalExpiredError(
                            f"Approval expired at {approval.approval_expiration_time}"
                        )
                except ValueError:
                    logger.warning(
                        "Could not parse approval_expiration_time: %s",
                        approval.approval_expiration_time,
                    )

            logger.debug(
                "Approval pending for activity %s, polling again in %.1fs",
                activity_id,
                self._config.hitl_poll_interval,
            )
            time.sleep(self._config.hitl_poll_interval)

    async def aevaluate(
        self,
        payload: dict[str, Any],
        api_key: str,
        identity: AgentIdentity | None = None,
    ) -> GovernanceResponse:
        """Async POST /api/v1/governance/evaluate using native AsyncClient."""
        url = f"{self._api_url}{EVALUATE_PATH}"
        body = json.dumps(payload)
        headers = self.build_request_headers(
            api_key=api_key,
            method="POST",
            pathname=EVALUATE_PATH,
            body=body,
            identity=identity,
        )

        try:
            response = await self._async_http.post(url, content=body, headers=headers)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            logger.warning(
                "Governance API returned %s for %s: %s",
                exc.response.status_code,
                url,
                exc.response.text[:200],
            )
            return self._handle_api_error(exc)
        except httpx.HTTPError as exc:
            logger.warning("Governance API unreachable at %s: %s", url, exc)
            return self._handle_api_error(exc)

        data = response.json()
        trace = self._get_trace(api_key)
        if trace:
            trace.log(EVALUATE_PATH, payload, data)
        gov_response = GovernanceResponse.from_dict(data)

        if gov_response.fallback_used and self._config.on_fallback == "fail_closed":
            logger.warning(
                "Core returned fallback_used=true; on_fallback=fail_closed overrides to BLOCK"
            )
            gov_response.verdict = Verdict.BLOCK

        return gov_response

    async def apoll_approval(
        self,
        workflow_id: str,
        run_id: str,
        activity_id: str,
        api_key: str,
        identity: AgentIdentity | None = None,
    ) -> ApprovalResponse:
        """Async single poll: POST /api/v1/governance/approval."""
        url = f"{self._api_url}{APPROVAL_PATH}"
        payload = {
            "workflow_id": workflow_id,
            "run_id": run_id,
            "activity_id": activity_id,
        }
        body = json.dumps(payload)
        headers = self.build_request_headers(
            api_key=api_key,
            method="POST",
            pathname=APPROVAL_PATH,
            body=body,
            identity=identity,
        )

        try:
            response = await self._async_http.post(url, content=body, headers=headers)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            logger.warning("Approval poll failed at %s: %s", url, exc)
            raise GovernanceAPIError(f"Approval poll failed: {exc}") from exc

        data = response.json()
        trace = self._get_trace(api_key)
        if trace:
            trace.log(APPROVAL_PATH, payload, data)
        return ApprovalResponse.from_dict(data)

    async def await_for_approval(
        self,
        workflow_id: str,
        run_id: str,
        activity_id: str,
        api_key: str,
        identity: AgentIdentity | None = None,
    ) -> GovernanceResponse:
        """Async HITL poll loop. Uses asyncio.sleep instead of time.sleep."""
        while True:
            approval = await self.apoll_approval(
                workflow_id, run_id, activity_id, api_key, identity
            )

            if approval.verdict == Verdict.ALLOW:
                return GovernanceResponse(
                    verdict=Verdict.ALLOW,
                    reason=approval.reason,
                    approval_id=approval.approval_id,
                )

            if approval.verdict.should_stop():
                return GovernanceResponse(
                    verdict=approval.verdict,
                    reason=approval.reason,
                    approval_id=approval.approval_id,
                )

            if approval.approval_expiration_time:
                try:
                    expiry = parse_iso8601_utc(approval.approval_expiration_time)
                    if datetime.now(timezone.utc) >= expiry:
                        raise GovernanceApprovalExpiredError(
                            f"Approval expired at {approval.approval_expiration_time}"
                        )
                except ValueError:
                    logger.warning(
                        "Could not parse approval_expiration_time: %s",
                        approval.approval_expiration_time,
                    )

            logger.debug(
                "Approval pending for activity %s, polling again in %.1fs",
                activity_id,
                self._config.hitl_poll_interval,
            )
            await asyncio.sleep(self._config.hitl_poll_interval)

    def close(self) -> None:
        self._http.close()

    async def aclose(self) -> None:
        await self._async_http.aclose()
        self._http.close()

    def __enter__(self) -> GovernanceClient:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    async def __aenter__(self) -> GovernanceClient:
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.aclose()

    def _handle_api_error(self, exc: Exception) -> GovernanceResponse:
        if self._config.on_api_error == "fail_closed":
            raise GovernanceAPIError(
                f"Governance API error and fail_closed policy is active: {exc}"
            ) from exc
        return GovernanceResponse(verdict=Verdict.ALLOW, fallback_used=True)

    @staticmethod
    def build_auth_headers(api_key: str) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "X-OpenBox-SDK-Version": __version__,
        }

    @staticmethod
    def build_request_headers(
        *,
        api_key: str,
        method: str,
        pathname: str,
        body: str,
        identity: AgentIdentity | None = None,
    ) -> dict[str, str]:
        headers = GovernanceClient.build_auth_headers(api_key)
        if identity is not None:
            headers.update(
                build_signed_identity_headers(
                    method=method,
                    pathname=pathname,
                    body=body,
                    identity=identity,
                )
            )
        return headers
