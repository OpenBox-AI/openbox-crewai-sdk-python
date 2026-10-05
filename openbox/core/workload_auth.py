"""Per-credential workload token cache shared by sync and async governance."""

from __future__ import annotations

import asyncio
import threading
import time
from urllib.parse import urlencode

import httpx

from openbox.core.errors import OpenBoxConfigError, OpenBoxNetworkError
from openbox.core.workload_identity import (
    AUTH_BOOTSTRAP_PATH,
    WorkloadAccessToken,
    WorkloadBootstrapDocument,
    build_private_key_jwt,
    load_workload_private_key,
    parse_bootstrap_response,
    parse_token_response,
)
from openbox.utils import suppress_governance_http


class WorkloadAuth:
    def __init__(self, private_key: str) -> None:
        self._signer = load_workload_private_key(private_key)
        self._lock = threading.Lock()
        self._document: WorkloadBootstrapDocument | None = None
        self._token: WorkloadAccessToken | None = None
        self._unavailable_until = 0.0

    def token(self, http: httpx.Client, api_url: str, headers: dict[str, str]) -> str | None:
        with self._lock:
            if self._token is not None and self._token.is_fresh():
                return self._token.value
            if time.monotonic() < self._unavailable_until:
                return None
            document = self._document
            with suppress_governance_http():
                if document is None:
                    try:
                        response = http.get(
                            api_url + AUTH_BOOTSTRAP_PATH,
                            headers={**headers, "Accept": "application/json"},
                        )
                    except httpx.HTTPError:
                        raise OpenBoxNetworkError(
                            "Workload identity bootstrap is unavailable."
                        ) from None
                    document = parse_bootstrap_response(response.status_code, response.content)
                    if document is None:
                        self._unavailable_until = time.monotonic() + 60
                        return None
                    # Remember advertised authority even if token exchange fails.
                    # A later retry must not downgrade after a transient outage.
                    self._document = document
                body = urlencode(
                    {
                        "grant_type": "client_credentials",
                        "client_id": document.client_id,
                        "client_assertion_type": (
                            "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"
                        ),
                        "client_assertion": build_private_key_jwt(self._signer, document),
                    }
                )
                try:
                    response = http.post(
                        document.token_endpoint,
                        content=body,
                        headers={
                            "Accept": "application/json",
                            "Content-Type": "application/x-www-form-urlencoded",
                        },
                    )
                except httpx.InvalidURL:
                    raise OpenBoxConfigError("Invalid workload token endpoint URL.") from None
                except httpx.HTTPError:
                    raise OpenBoxNetworkError("Workload token exchange is unavailable.") from None
                token = parse_token_response(response.status_code, response.content)
            self._document, self._token = document, token
            return token.value

    async def atoken(self, http: httpx.Client, api_url: str, headers: dict[str, str]) -> str | None:
        # One lock/cache for both kinds of callers. Running the exchange in a
        # worker avoids blocking the event loop or deadlocking a synchronous
        # instrumentation hook against an async refresh on that same loop.
        return await asyncio.to_thread(self.token, http, api_url, headers)
