from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from devicelab.application.ports import Clock, IdFactory, UowFactory
from devicelab.application.records import ApiKeyRecord
from devicelab.application.support import Actor, audit
from devicelab.application.tokens import generate_token, hash_secret
from devicelab.domain.errors import ConflictError, ForbiddenError, NotFoundError, UnauthorizedError
from devicelab.domain.models import Role

_RANK = {Role.VIEWER: 0, Role.OPERATOR: 1, Role.ADMIN: 2}


@dataclass(frozen=True, slots=True)
class Principal:
    key_id: UUID
    name: str
    role: Role

    @property
    def actor(self) -> Actor:
        return Actor(f"key:{self.name}", self.role)

    def require(self, minimum: Role) -> None:
        if _RANK[self.role] < _RANK[minimum]:
            raise ForbiddenError(
                f"requires role {minimum.value}", details={"role": self.role.value}
            )


class AuthService:
    def __init__(self, uow_factory: UowFactory, clock: Clock, ids: IdFactory) -> None:
        self._uow = uow_factory
        self._clock = clock
        self._ids = ids

    async def authenticate(self, secret: str) -> Principal:
        async with self._uow() as uow:
            record = await uow.api_keys.get_by_hash(hash_secret(secret))
            if record is None or record.revoked_at is not None:
                raise UnauthorizedError("invalid API key", code="invalid_api_key")
            now = self._clock.now()
            # Touch at most once a minute; auth must not become a write hot spot.
            if record.last_used_at is None or (now - record.last_used_at).total_seconds() > 60:
                await uow.api_keys.update(record.id, last_used_at=now)
                await uow.commit()
        return Principal(record.id, record.name, Role(record.role))

    async def create_key(self, actor: Actor, name: str, role: Role) -> tuple[str, ApiKeyRecord]:
        secret, prefix = generate_token("k")
        return secret, await self._store(actor, name, role, secret, prefix)

    async def bootstrap(self, secret: str) -> bool:
        """Create the first admin key from configuration, only when no keys exist."""
        async with self._uow() as uow:
            if await uow.api_keys.count() > 0:
                return False
        await self._store(
            Actor("bootstrap", Role.ADMIN), "bootstrap-admin", Role.ADMIN, secret, secret[:10]
        )
        return True

    async def _store(
        self, actor: Actor, name: str, role: Role, secret: str, prefix: str
    ) -> ApiKeyRecord:
        now = self._clock.now()
        record = ApiKeyRecord(
            id=self._ids(),
            name=name,
            prefix=prefix,
            key_hash=hash_secret(secret),
            role=role.value,
            created_at=now,
        )
        async with self._uow() as uow:
            try:
                await uow.api_keys.add(record)
            except ConflictError:
                raise ConflictError("API key name already exists", code="api_key_exists") from None
            await audit(
                uow, actor, "api_key.created", "api_key", record.id, now, name=name, role=role.value
            )
            await uow.commit()
        return record

    async def revoke(self, actor: Actor, key_id: UUID) -> None:
        now = self._clock.now()
        async with self._uow() as uow:
            record = await uow.api_keys.get(key_id)
            if record is None:
                raise NotFoundError("API key not found", code="api_key_not_found")
            await uow.api_keys.update(key_id, revoked_at=now)
            await audit(uow, actor, "api_key.revoked", "api_key", key_id, now)
            await uow.commit()

    async def list(self) -> list[ApiKeyRecord]:
        async with self._uow() as uow:
            return await uow.api_keys.list_all()
