#!/usr/bin/env python3
"""THROWAWAY outer-owned freshness allocation and one-shot claim seam."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import secrets
import stat
from typing import Callable, Protocol

from contract import ExecutionIdentity, ExecutionRequest


class AuthorityError(RuntimeError):
    pass


@dataclass(frozen=True)
class AuthorityGrant:
    identity: ExecutionIdentity
    broker_secret: bytes
    challenge: str


@dataclass(frozen=True)
class ClaimedExecution:
    grant: AuthorityGrant
    claim_id: str


class AuthorityStore(Protocol):
    def claim(self, grant: AuthorityGrant) -> ClaimedExecution: ...
    def consume(self, claimed: ClaimedExecution) -> None: ...


class AuthorityAllocator:
    """Generate all capability material outside the caller's request."""

    def __init__(self, token_hex: Callable[[int], str] = secrets.token_hex) -> None:
        self._token_hex = token_hex

    def allocate(self, request: ExecutionRequest) -> AuthorityGrant:
        owner_nonce = self._token_hex(16)
        broker_secret = bytes.fromhex(self._token_hex(32))
        challenge = self._token_hex(32)
        return AuthorityGrant(
            request.allocate_identity(owner_nonce), broker_secret, challenge,
        )


def claim_payload(grant: AuthorityGrant) -> bytes:
    value = {
        "schema": "meshshot.agent-boundary.authority-claim/1",
        **grant.identity.as_json(),
        "challenge": grant.challenge,
    }
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


class FileAuthorityStore:
    """Durable atomic claim/consume markers; Broker secret is never persisted."""

    def __init__(self, root: Path) -> None:
        candidate = root.absolute()
        metadata = candidate.lstat()
        if (
            candidate.resolve(strict=True) != candidate
            or not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or stat.S_IMODE(metadata.st_mode) != 0o700
        ):
            raise AuthorityError("authority store is not private")
        self.root = candidate

    def _sync_root(self) -> None:
        descriptor = os.open(
            self.root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
        )
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def claim(self, grant: AuthorityGrant) -> ClaimedExecution:
        payload = claim_payload(grant)
        claim_id = hashlib.sha256(payload).hexdigest()
        target = self.root / f"{claim_id}.claimed"
        try:
            descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError as exc:
            raise AuthorityError("authority replay") from exc
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        self._sync_root()
        return ClaimedExecution(grant, claim_id)

    def consume(self, claimed: ClaimedExecution) -> None:
        expected = hashlib.sha256(claim_payload(claimed.grant)).hexdigest()
        if claimed.claim_id != expected:
            raise AuthorityError("authority claim mismatch")
        source = self.root / f"{claimed.claim_id}.claimed"
        target = self.root / f"{claimed.claim_id}.consumed"
        try:
            os.link(source, target)
            source.unlink()
            self._sync_root()
        except (FileExistsError, FileNotFoundError) as exc:
            raise AuthorityError("authority replay") from exc
