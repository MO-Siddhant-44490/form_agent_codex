"""Authentication for the WebSocket and REST surface.

Local development (Slices 1-3, plan.md §20): a per-run shared session token,
unauthenticated localhost otherwise. Cloud/enterprise: OIDC/RBAC replace this
provider without touching the transport or orchestrator."""

import hmac
import secrets
from dataclasses import dataclass, field


class AuthError(Exception):
    pass


@dataclass
class DevTokenAuth:
    """Per-run shared-secret tokens minted by the backend and presented by the
    extension on connect. Constant-time comparison; single active token per
    run."""

    _tokens: dict[str, str] = field(default_factory=dict)

    def mint(self, run_id: str) -> str:
        token = secrets.token_urlsafe(32)
        self._tokens[run_id] = token
        return token

    def verify(self, run_id: str, token: str) -> None:
        expected = self._tokens.get(run_id)
        if expected is None or not hmac.compare_digest(expected, token):
            raise AuthError("invalid or missing session token")

    def revoke(self, run_id: str) -> None:
        self._tokens.pop(run_id, None)
