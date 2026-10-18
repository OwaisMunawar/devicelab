"""HMAC-signed, Slack-compatible webhook delivery."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import time

import httpx
import structlog

from devicelab.application.records import AlertRecord, WebhookRecord

log = structlog.get_logger()

_COLORS = {"info": "#2f81f7", "warning": "#d29922", "critical": "#f85149"}
SIGNATURE_VERSION = "v1"


def sign(secret: str, timestamp: str, body: bytes) -> str:
    mac = hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256)
    return f"{SIGNATURE_VERSION}={mac.hexdigest()}"


def verify(
    secret: str, timestamp: str, body: bytes, signature: str, *, tolerance_s: int = 300
) -> bool:
    """For receivers: constant-time compare plus a replay window on the timestamp."""
    try:
        if abs(time.time() - int(timestamp)) > tolerance_s:
            return False
    except ValueError:
        return False
    return hmac.compare_digest(sign(secret, timestamp, body), signature)


def build_payload(alert: AlertRecord) -> dict[str, object]:
    return {
        "text": f"[{alert.severity.upper()}] {alert.message}",
        "attachments": [
            {
                "color": _COLORS.get(alert.severity, "#888888"),
                "fields": [
                    {"title": "Kind", "value": alert.kind, "short": True},
                    {"title": "Occurrences", "value": str(alert.occurrences), "short": True},
                ],
            }
        ],
        "devicelab": {
            "alert_id": str(alert.id),
            "kind": alert.kind,
            "severity": alert.severity,
            "dedupe_key": alert.dedupe_key,
            "payload": alert.payload,
        },
    }


class WebhookNotifier:
    def __init__(
        self,
        client: httpx.AsyncClient | None = None,
        *,
        attempts: int = 3,
        base_delay_s: float = 0.5,
    ) -> None:
        self._client = client or httpx.AsyncClient(timeout=5.0)
        self._attempts = attempts
        self._base_delay_s = base_delay_s

    async def notify(self, alert: AlertRecord, webhooks: list[WebhookRecord]) -> None:
        body = json.dumps(build_payload(alert), separators=(",", ":")).encode()
        await asyncio.gather(*(self._deliver(hook, alert, body) for hook in webhooks))

    async def _deliver(self, hook: WebhookRecord, alert: AlertRecord, body: bytes) -> None:
        for attempt in range(1, self._attempts + 1):
            timestamp = str(int(time.time()))
            try:
                response = await self._client.post(
                    hook.url,
                    content=body,
                    headers={
                        "Content-Type": "application/json",
                        "X-DeviceLab-Event": "alert",
                        "X-DeviceLab-Timestamp": timestamp,
                        "X-DeviceLab-Signature": sign(hook.secret, timestamp, body),
                    },
                )
                if response.status_code < 300:
                    return
                log.warning("webhook_rejected", webhook=hook.name, status=response.status_code)
            except httpx.HTTPError as exc:
                log.warning("webhook_failed", webhook=hook.name, error=str(exc))
            if attempt < self._attempts:
                await asyncio.sleep(self._base_delay_s * 2 ** (attempt - 1))
        log.error("webhook_gave_up", webhook=hook.name, alert=str(alert.id))

    async def aclose(self) -> None:
        await self._client.aclose()
