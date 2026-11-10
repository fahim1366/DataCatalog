from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Union

import jwt
from argon2 import PasswordHasher, Type
from argon2.exceptions import InvalidHash, VerificationError, VerifyMismatchError
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.core.config import settings
from backend.src.models.identity import Role, User, UserRole, UserStatus


class AuthError(Exception):
    """Base exception for authentication and token validation failures."""

    pass


class InvalidCredentialsError(AuthError):
    """Raised when authentication credentials (username/password) are invalid."""

    pass


class InactiveUserError(AuthError):
    """Raised when an authenticated user's account is SUSPENDED or DEACTIVATED."""

    pass


class InvalidTokenError(AuthError):
    """Raised when a JWT signature is invalid, forged, or the token payload is malformed."""

    pass


class TokenExpiredError(AuthError):
    """Raised when a JWT has expired."""

    pass


class JWTAuthService:
    """
    Production-grade Identity & JWT Authentication Service.

    Provides:
    - Argon2id password hashing and verification complying with RFC 9106.
    - JWT token generation with comprehensive enterprise claims (sub, username, email, roles, is_superadmin, exp, iat, jti).
    - Cryptographic signature validation and expiration enforcement.
    - Asynchronous user authentication against PostgreSQL database with status validation (ACTIVE vs SUSPENDED/DEACTIVATED).
    """

    def __init__(
        self,
        secret_key: str = settings.JWT_SECRET_KEY,
        algorithm: str = settings.JWT_ALGORITHM,
        access_token_expire_minutes: int = settings.ACCESS_TOKEN_EXPIRE_MINUTES,
        time_cost: int = 3,
        memory_cost: int = 65536,
        parallelism: int = 4,
        hash_len: int = 32,
        salt_len: int = 16,
    ) -> None:
        self.secret_key = secret_key
        self.algorithm = algorithm
        self.access_token_expire_minutes = access_token_expire_minutes
        self._password_hasher = PasswordHasher(
            time_cost=time_cost,
            memory_cost=memory_cost,
            parallelism=parallelism,
            hash_len=hash_len,
            salt_len=salt_len,
            type=Type.ID,
        )

    def hash_password(self, password: str) -> str:
        """
        Hashes a plaintext password using Argon2id.

        Args:
            password: The plaintext password string to hash.

        Returns:
            str: An RFC 9106 compliant Argon2id password hash string.
        """
        if not password:
            raise ValueError("Password cannot be empty")
        return self._password_hasher.hash(password)

    def verify_password(self, plain_password: str, hashed_password: str) -> bool:
        """
        Verifies a plaintext password against an Argon2id hash.
        Performs constant-time comparison to prevent timing attacks.

        Args:
            plain_password: The plaintext password.
            hashed_password: The stored Argon2id hash.

        Returns:
            bool: True if password matches hash, False otherwise.
        """
        if not plain_password or not hashed_password:
            return False
        try:
            return self._password_hasher.verify(hashed_password, plain_password)
        except (VerifyMismatchError, VerificationError, InvalidHash):
            return False

    def check_needs_rehash(self, hashed_password: str) -> bool:
        """
        Determines whether the password hash needs to be updated to match current
        hasher parameters.
        """
        try:
            return self._password_hasher.check_needs_rehash(hashed_password)
        except Exception:
            return True

    def create_access_token(
        self,
        data: Dict[str, Any],
        expires_delta: Optional[timedelta] = None,
    ) -> str:
        """
        Issues an encoded JWT access token with standard enterprise claims.

        Args:
            data: Custom claims payload (e.g., sub, username, email, roles, is_superadmin).
            expires_delta: Optional custom token expiration duration.

        Returns:
            str: Encoded JWT token string.
        """
        to_encode = data.copy()
        now = datetime.now(timezone.utc)

        if expires_delta is not None:
            expire = now + expires_delta
        else:
            expire = now + timedelta(minutes=self.access_token_expire_minutes)

        to_encode.setdefault("iat", int(now.timestamp()))
        to_encode.setdefault("nbf", int(now.timestamp()))
        to_encode.setdefault("jti", str(uuid.uuid4()))
        to_encode["exp"] = int(expire.timestamp())

        if "sub" in to_encode and isinstance(to_encode["sub"], uuid.UUID):
            to_encode["sub"] = str(to_encode["sub"])

        encoded_jwt = jwt.encode(
            payload=to_encode,
            key=self.secret_key,
            algorithm=self.algorithm,
        )
        return encoded_jwt

    def decode_token(self, token: str) -> Dict[str, Any]:
        """
        Decodes and validates a JWT access token's cryptographic signature and expiration.

        Args:
            token: Encoded JWT token string.

        Returns:
            Dict[str, Any]: Decoded payload claims dictionary.

        Raises:
            TokenExpiredError: If the token has passed its expiration time.
            InvalidTokenError: If the signature is forged, invalid, or the payload is corrupt.
        """
        try:
            payload = jwt.decode(
                token,
                self.secret_key,
                algorithms=[self.algorithm],
                options={
                    "require": ["exp", "iat"],
                    "verify_exp": True,
                    "verify_iat": True,
                },
            )
            return payload
        except jwt.ExpiredSignatureError as exc:
            raise TokenExpiredError("JWT token has expired") from exc
        except (jwt.InvalidSignatureError, jwt.DecodeError, jwt.InvalidTokenError, Exception) as exc:
            raise InvalidTokenError(f"Invalid JWT token: {str(exc)}") from exc

    def validate_token(self, token: str) -> Dict[str, Any]:
        """Alias for decode_token."""
        return self.decode_token(token)

    def extract_user_id(self, token: str) -> uuid.UUID:
        """
        Extracts and parses the user ID UUID from the token's 'sub' claim.
        """
        claims = self.decode_token(token)
        sub = claims.get("sub")
        if not sub:
            raise InvalidTokenError("Token missing 'sub' subject claim")
        try:
            return uuid.UUID(str(sub))
        except (ValueError, TypeError) as exc:
            raise InvalidTokenError(f"Invalid user ID in 'sub' claim: {sub}") from exc

    async def get_user_roles(
        self, session: AsyncSession, user_id: uuid.UUID
    ) -> List[str]:
        """
        Queries all active roles assigned to a user.

        Args:
            session: SQLAlchemy AsyncSession.
            user_id: User UUID.

        Returns:
            List[str]: Alphabetically sorted list of role names.
        """
        stmt = (
            select(Role.name)
            .join(UserRole, UserRole.role_id == Role.id)
            .where(UserRole.user_id == user_id)
            .order_by(Role.name.asc())
        )
        result = await session.execute(stmt)
        return list(result.scalars().all())

    async def authenticate_user(
        self,
        session: AsyncSession,
        username_or_email: str,
        password: str,
    ) -> User:
        """
        Authenticates a user by username or email and validates their password against
        the stored Argon2id hash. Validates that the account status is ACTIVE.

        Args:
            session: SQLAlchemy AsyncSession.
            username_or_email: The user's username or email address.
            password: The raw password provided during login.

        Returns:
            User: The authenticated active User model instance.

        Raises:
            InvalidCredentialsError: If user does not exist or password does not match.
            InactiveUserError: If user status is SUSPENDED or DEACTIVATED.
        """
        stmt = select(User).where(
            or_(User.username == username_or_email, User.email == username_or_email)
        )
        result = await session.execute(stmt)
        user = result.scalar_one_or_none()

        if user is None:
            raise InvalidCredentialsError("Invalid username or password")

        if not self.verify_password(password, user.hashed_password):
            raise InvalidCredentialsError("Invalid username or password")

        if user.status != UserStatus.ACTIVE:
            raise InactiveUserError(
                f"User account '{user.username}' is not active (status: {user.status.value})"
            )

        return user

    async def issue_token_for_user(
        self,
        session: Optional[AsyncSession],
        user: User,
        roles: Optional[List[str]] = None,
        expires_delta: Optional[timedelta] = None,
    ) -> str:
        """
        Constructs user claims and issues a signed JWT access token.
        If roles is None and a session is provided, queries the database for user roles.

        Args:
            session: Optional SQLAlchemy AsyncSession to fetch roles.
            user: User model instance.
            roles: Optional list of role names.
            expires_delta: Optional token expiration duration.

        Returns:
            str: Signed JWT access token.
        """
        effective_roles: List[str] = []
        if roles is not None:
            effective_roles = roles
        elif session is not None:
            effective_roles = await self.get_user_roles(session, user.id)

        claims = {
            "sub": str(user.id),
            "username": user.username,
            "email": user.email,
            "roles": effective_roles,
            "is_superadmin": user.is_superadmin,
        }
        return self.create_access_token(data=claims, expires_delta=expires_delta)


auth_service: JWTAuthService = JWTAuthService()


def hash_password(password: str) -> str:
    return auth_service.hash_password(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return auth_service.verify_password(plain_password, hashed_password)


def create_access_token(
    data: Dict[str, Any], expires_delta: Optional[timedelta] = None
) -> str:
    return auth_service.create_access_token(data, expires_delta=expires_delta)


def decode_jwt_token(token: str) -> Dict[str, Any]:
    return auth_service.decode_token(token)


async def authenticate_user(
    session: AsyncSession, username_or_email: str, password: str
) -> User:
    return await auth_service.authenticate_user(session, username_or_email, password)

