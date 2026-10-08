"""Optional immediate alert delivery when the temporal engine confirms.

The normal report is published once, at the end of a run, so a confirmation at
t=1s is invisible until the whole clip has been processed. This module lets an
operator receive an alert at the moment the rules pass instead.

It is strictly opt-in (``--alert-webhook``). Delivery is **best effort**: a
failed or unreachable webhook is reported but never aborts analysis, never
changes the published result, and never affects the decision itself.
"""

import json
import urllib.error
import urllib.request
from typing import Any

# Bounded so a misconfigured endpoint cannot stream an unbounded body.
_MAX_RESPONSE_BYTES = 64 * 1024


class LiveAlertEmitter:
    """POSTs a minimal confirmation payload as soon as a candidate confirms."""

    def __init__(self, url: str, token: str, *, timeout: float = 10.0) -> None:
        self.url = url
        self.token = token
        self.timeout = timeout
        self.sent: list[str] = []

    def emit(self, payload: dict[str, Any]) -> bool:
        """Return True when the receiver accepted the alert.

        Never raises: an unreachable alert service must not fail an analysis.
        """
        body = json.dumps(payload, allow_nan=False).encode("utf-8")
        request = urllib.request.Request(
            self.url,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "X-Detector-Token": self.token,
                "Accept": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                accepted = 200 <= response.status < 300
                response.read(_MAX_RESPONSE_BYTES)
        except urllib.error.HTTPError as error:
            return False
        except (urllib.error.URLError, OSError, ValueError):
            return False
        if accepted:
            incident_id = str(payload.get("incident_id", ""))
            if incident_id:
                self.sent.append(incident_id)
        return accepted