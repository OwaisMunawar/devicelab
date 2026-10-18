"""S3-compatible artifact storage (AWS S3, MinIO, R2). The client is injected so tests can
substitute a stub and deployments can bring their own credentials chain."""

from __future__ import annotations

import asyncio
from typing import Any

from devicelab.domain.errors import NotFoundError


class S3ArtifactStore:
    def __init__(self, client: Any, bucket: str, prefix: str = "") -> None:
        self._client = client
        self._bucket = bucket
        self._prefix = prefix

    def _key(self, key: str) -> str:
        return f"{self._prefix}{key}"

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        await asyncio.to_thread(
            self._client.put_object,
            Bucket=self._bucket,
            Key=self._key(key),
            Body=data,
            ContentType=content_type,
        )

    async def get(self, key: str) -> bytes:
        try:
            response = await asyncio.to_thread(
                self._client.get_object, Bucket=self._bucket, Key=self._key(key)
            )
        except self._client.exceptions.NoSuchKey:
            raise NotFoundError("artifact content is gone", code="artifact_missing") from None
        body: bytes = await asyncio.to_thread(response["Body"].read)
        return body

    async def delete(self, key: str) -> None:
        await asyncio.to_thread(self._client.delete_object, Bucket=self._bucket, Key=self._key(key))


def create_s3_client(endpoint_url: str | None, region: str) -> Any:
    import boto3  # noqa: PLC0415 - optional dependency, only needed for the s3 backend

    return boto3.client("s3", endpoint_url=endpoint_url, region_name=region)
