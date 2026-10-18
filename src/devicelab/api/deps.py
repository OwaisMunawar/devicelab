from __future__ import annotations

from collections.abc import Callable
from typing import Annotated

from fastapi import Depends, Query, Request

from devicelab.application.auth import Principal
from devicelab.container import Services
from devicelab.domain.errors import UnauthorizedError
from devicelab.domain.models import Role


def get_services(request: Request) -> Services:
    services: Services = request.app.state.services
    return services


ServicesDep = Annotated[Services, Depends(get_services)]


async def current_principal(request: Request, services: ServicesDep) -> Principal:
    header = request.headers.get("authorization", "")
    scheme, _, secret = header.partition(" ")
    if scheme.lower() != "bearer" or not secret:
        raise UnauthorizedError("missing bearer token", code="missing_api_key")
    principal = await services.auth.authenticate(secret.strip())
    request.state.principal = principal
    return principal


PrincipalDep = Annotated[Principal, Depends(current_principal)]


def require(role: Role) -> Callable[[Principal], Principal]:
    def check(principal: PrincipalDep) -> Principal:
        principal.require(role)
        return principal

    return check


Viewer = Annotated[Principal, Depends(require(Role.VIEWER))]
Operator = Annotated[Principal, Depends(require(Role.OPERATOR))]
Admin = Annotated[Principal, Depends(require(Role.ADMIN))]


class Pagination:
    def __init__(
        self,
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> None:
        self.limit = limit
        self.offset = offset


Paging = Annotated[Pagination, Depends()]
