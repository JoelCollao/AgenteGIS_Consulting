"""
Autenticación del usuario final en agent-service
=================================================
Valida el JWT que emite el backend (`POST /api/auth/login`): firma, emisor y
expiración, con los mismos parámetros (JWT_SECRET, JWT_ALGORITHM, JWT_ISSUER).

Diferencia deliberada con `get_current_user` del backend (decisión P6): aquí
NO se consulta la base de datos para comprobar que la cuenta sigue activa.
Esa comprobación la hace el backend en cada tool, porque el token se le
reenvía tal cual (contrato §3.2).

Mismas respuestas que el backend: 401 con `WWW-Authenticate: Bearer` tanto si
falta el header como si el token no verifica, para que el interceptor del
frontend renueve la sesión igual que hoy.
"""

import logging
from dataclasses import dataclass
from typing import Any, Dict

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from agent_service.clients.backend import auth_token_var
from agent_service.config import settings

logger = logging.getLogger(__name__)

_bearer_scheme = HTTPBearer(auto_error=False, description="JWT emitido por POST /api/auth/login del backend")


@dataclass(frozen=True)
class AuthenticatedUser:
    username: str
    token: str
    claims: Dict[str, Any]


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def resolve_jwt_secret() -> str:
    """Igual que en el backend: sin secreto no se arranca."""
    if not settings.jwt_secret:
        raise ValueError("JWT_SECRET no está configurado")
    return settings.jwt_secret


def decode_access_token(token: str) -> Dict[str, Any]:
    try:
        return jwt.decode(
            token,
            resolve_jwt_secret(),
            algorithms=[settings.jwt_algorithm],
            issuer=settings.jwt_issuer,
        )
    except jwt.PyJWTError as exc:
        logger.info("[auth] Token rechazado: %s", exc)
        raise _unauthorized("Token inválido o expirado") from exc


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
) -> AuthenticatedUser:
    if credentials is None or not credentials.credentials:
        raise _unauthorized("No autenticado: falta el header Authorization")

    payload = decode_access_token(credentials.credentials)
    username = payload.get("sub")
    if not username:
        raise _unauthorized("Token sin identificador de usuario")

    # El JWT viaja al backend en cada tool (contrato §3.2). Se fija aquí, en la
    # dependencia async, para que lo vea el endpoint (misma tarea) y las tools.
    auth_token_var.set(credentials.credentials)
    return AuthenticatedUser(username=str(username), token=credentials.credentials, claims=payload)
