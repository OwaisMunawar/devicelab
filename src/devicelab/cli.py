"""`devicelab`: administration commands for the control plane."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

import uvicorn

from devicelab.adapters.db.migrate import upgrade
from devicelab.api.app import create_app
from devicelab.application.support import Actor
from devicelab.config import Settings
from devicelab.domain.models import Role
from devicelab.runtime import build_runtime


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="devicelab")
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve", help="run the API and scheduler")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    sub.add_parser("migrate", help="apply database migrations")
    key = sub.add_parser("create-key", help="create an API key and print it once")
    key.add_argument("--name", required=True)
    key.add_argument("--role", choices=[r.value for r in Role], default="admin")
    sub.add_parser("openapi", help="print the OpenAPI document")
    args = parser.parse_args(argv)
    settings = Settings()

    if args.command == "serve":
        uvicorn.run(
            create_app(settings),
            host=args.host,
            port=args.port,
            ws_max_size=settings.ws_max_message_bytes,
            log_config=None,
        )
    elif args.command == "migrate":
        upgrade(settings.database_url)
        print("database is up to date")
    elif args.command == "create-key":
        asyncio.run(_create_key(settings, args.name, Role(args.role)))
    else:
        json.dump(create_app(settings).openapi(), sys.stdout, indent=2)


async def _create_key(settings: Settings, name: str, role: Role) -> None:
    runtime = build_runtime(settings)
    try:
        secret, record = await runtime.services.auth.create_key(
            Actor("cli", Role.ADMIN), name, role
        )
        print(f"{record.name} ({record.role}): {secret}")
        print("Store this now; it cannot be shown again.")
    finally:
        await runtime.aclose()
