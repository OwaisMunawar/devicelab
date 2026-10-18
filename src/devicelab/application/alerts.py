"""Alert evaluation, deduplication, cooldown and delivery."""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from urllib.parse import urlparse
from uuid import UUID

from devicelab.application.ports import (
    AlertNotifier,
    Clock,
    EventPublisher,
    IdFactory,
    UowFactory,
)
from devicelab.application.records import AlertRecord, AlertRuleRecord, Page, WebhookRecord
from devicelab.application.support import Actor, audit, emit
from devicelab.application.tokens import generate_token
from devicelab.domain.alert_rules import (
    AlertKind,
    MetricsSnapshot,
    OfflineDevice,
    evaluate,
    should_notify,
)
from devicelab.domain.errors import NotFoundError, ValidationFailedError
from devicelab.domain.models import DeviceState

DEFAULT_RULES: tuple[tuple[str, AlertKind, dict[str, Any], int], ...] = (
    ("Device offline", AlertKind.DEVICE_OFFLINE, {"minutes": 5}, 900),
    (
        "High failure rate",
        AlertKind.FAILURE_RATE,
        {"window_s": 3600, "threshold": 0.3, "min_executions": 10},
        1800,
    ),
    ("Queue wait over SLA", AlertKind.QUEUE_WAIT, {"sla_s": 300}, 900),
    ("Lease reclaimed", AlertKind.LEASE_RECLAIMED, {}, 60),
)
# Kinds that describe a standing condition resolve themselves when the condition clears.
_SELF_RESOLVING = {AlertKind.DEVICE_OFFLINE, AlertKind.FAILURE_RATE, AlertKind.QUEUE_WAIT}
_RECLAIM_LOOKBACK = timedelta(minutes=15)


class AlertService:
    def __init__(
        self,
        uow_factory: UowFactory,
        clock: Clock,
        ids: IdFactory,
        notifier: AlertNotifier,
        events: EventPublisher,
    ) -> None:
        self._uow = uow_factory
        self._clock = clock
        self._ids = ids
        self._notifier = notifier
        self._events = events

    async def ensure_default_rules(self) -> None:
        now = self._clock.now()
        async with self._uow() as uow:
            if await uow.alerts.list_rules():
                return
            for name, kind, params, cooldown in DEFAULT_RULES:
                await uow.alerts.add_rule(
                    AlertRuleRecord(
                        id=self._ids(),
                        name=name,
                        kind=kind.value,
                        params=params,
                        enabled=True,
                        cooldown_s=cooldown,
                        created_at=now,
                    )
                )
            await uow.commit()

    async def evaluate(self) -> int:
        """Run every enabled rule once. Returns the number of notifications sent."""
        now = self._clock.now()
        to_notify: list[AlertRecord] = []
        async with self._uow() as uow:
            rules = [r for r in await uow.alerts.list_rules() if r.enabled]
            fired_keys: set[str] = set()
            offline = await uow.devices.list_in_states([DeviceState.OFFLINE.value])
            oldest = await uow.jobs.oldest_queued_at()
            reclaimed = await uow.leases.list_reclaimed_since(now - _RECLAIM_LOOKBACK)
            devices_by_id = {d.id: d for d in offline}

            for rule in rules:
                kind = AlertKind(rule.kind)
                window = timedelta(seconds=float(rule.params.get("window_s", 3600)))
                finished, failed = await uow.stats.finished_counts(now - window)
                snapshot = MetricsSnapshot(
                    now=now,
                    offline_devices=tuple(
                        OfflineDevice(
                            str(d.id), d.name, d.site, d.state_changed_at or d.last_seen or now
                        )
                        for d in devices_by_id.values()
                    ),
                    finished_in_window=finished,
                    failed_in_window=failed,
                    oldest_queued_wait_s=(now - oldest).total_seconds() if oldest else 0.0,
                    reclaimed_leases=tuple(
                        (str(lease.id), str(lease.device_id)) for lease in reclaimed
                    ),
                )
                for candidate in evaluate(kind, rule.params, snapshot):
                    fired_keys.add(candidate.dedupe_key)
                    existing = await uow.alerts.get_open_by_key(candidate.dedupe_key)
                    if existing is None:
                        alert = AlertRecord(
                            id=self._ids(),
                            rule_id=rule.id,
                            kind=candidate.kind.value,
                            severity=candidate.severity.value,
                            dedupe_key=candidate.dedupe_key,
                            message=candidate.message,
                            payload=candidate.payload,
                            state="open",
                            first_seen=now,
                            last_seen=now,
                        )
                        await uow.alerts.add_alert(alert)
                        to_notify.append(alert)
                    else:
                        await uow.alerts.update_alert(
                            existing.id,
                            last_seen=now,
                            occurrences=existing.occurrences + 1,
                            message=candidate.message,
                            payload=candidate.payload,
                            severity=candidate.severity.value,
                        )
                        if should_notify(existing.last_notified_at, now, rule.cooldown_s):
                            to_notify.append(existing)
                    emit(uow, self._events, "alert.updated", key=candidate.dedupe_key)

            for open_alert in await uow.alerts.list_open():
                if AlertKind(open_alert.kind) in _SELF_RESOLVING and (
                    open_alert.dedupe_key not in fired_keys
                ):
                    await uow.alerts.update_alert(open_alert.id, state="resolved", resolved_at=now)
                    emit(uow, self._events, "alert.updated", key=open_alert.dedupe_key)
            webhooks = [w for w in await uow.alerts.list_webhooks() if w.enabled]
            await uow.commit()

        sent = 0
        for alert in to_notify:
            await self._notifier.notify(alert, webhooks)
            async with self._uow() as uow:
                await uow.alerts.update_alert(alert.id, last_notified_at=now)
                await uow.commit()
            sent += 1
        return sent

    async def acknowledge(self, actor: Actor, alert_id: UUID) -> AlertRecord:
        now = self._clock.now()
        async with self._uow() as uow:
            alert = await uow.alerts.get_alert(alert_id)
            if alert is None:
                raise NotFoundError("alert not found", code="alert_not_found")
            await uow.alerts.update_alert(alert_id, state="resolved", resolved_at=now)
            await audit(uow, actor, "alert.acknowledged", "alert", alert_id, now)
            emit(uow, self._events, "alert.updated", key=alert.dedupe_key)
            await uow.commit()
            updated = await uow.alerts.get_alert(alert_id)
        assert updated is not None
        return updated

    async def list_alerts(
        self, *, state: str | None = None, limit: int = 50, offset: int = 0
    ) -> Page[AlertRecord]:
        async with self._uow() as uow:
            return await uow.alerts.list_alerts(state=state, limit=limit, offset=offset)

    async def list_rules(self) -> list[AlertRuleRecord]:
        async with self._uow() as uow:
            return await uow.alerts.list_rules()

    async def update_rule(
        self,
        actor: Actor,
        rule_id: UUID,
        *,
        enabled: bool | None,
        params: dict[str, Any] | None,
        cooldown_s: int | None,
    ) -> AlertRuleRecord:
        changes: dict[str, Any] = {}
        if enabled is not None:
            changes["enabled"] = enabled
        if params is not None:
            changes["params"] = params
        if cooldown_s is not None:
            if cooldown_s < 0:
                raise ValidationFailedError("cooldown must be >= 0", code="invalid_cooldown")
            changes["cooldown_s"] = cooldown_s
        async with self._uow() as uow:
            if await uow.alerts.get_rule(rule_id) is None:
                raise NotFoundError("alert rule not found", code="rule_not_found")
            await uow.alerts.update_rule(rule_id, **changes)
            await audit(
                uow,
                actor,
                "alert_rule.updated",
                "alert_rule",
                rule_id,
                self._clock.now(),
                **changes,
            )
            await uow.commit()
            rule = await uow.alerts.get_rule(rule_id)
        assert rule is not None
        return rule

    async def create_webhook(self, actor: Actor, name: str, url: str) -> WebhookRecord:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ValidationFailedError("webhook url must be http(s)", code="invalid_webhook_url")
        secret, _ = generate_token("w")
        now = self._clock.now()
        record = WebhookRecord(
            id=self._ids(), name=name, url=url, secret=secret, enabled=True, created_at=now
        )
        async with self._uow() as uow:
            await uow.alerts.add_webhook(record)
            await audit(uow, actor, "webhook.created", "webhook", record.id, now, name=name)
            await uow.commit()
        return record

    async def delete_webhook(self, actor: Actor, webhook_id: UUID) -> None:
        async with self._uow() as uow:
            if await uow.alerts.get_webhook(webhook_id) is None:
                raise NotFoundError("webhook not found", code="webhook_not_found")
            await uow.alerts.delete_webhook(webhook_id)
            await audit(uow, actor, "webhook.deleted", "webhook", webhook_id, self._clock.now())
            await uow.commit()

    async def list_webhooks(self) -> list[WebhookRecord]:
        async with self._uow() as uow:
            return await uow.alerts.list_webhooks()


__all__ = ["DEFAULT_RULES", "AlertService"]
