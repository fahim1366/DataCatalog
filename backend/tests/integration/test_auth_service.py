from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
import pytest
import jwt
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.core.config import settings
from backend.src.core.database import async_session_factory
from backend.src.models.identity import Role, User, UserRole, UserStatus
from backend.src.services.auth.jwt_service import (
    AuthError,
    InactiveUserError,
    InvalidCredentialsError,
    InvalidTokenError,
    JWTAuthService,
    TokenExpiredError,
    auth_service,
)


@pytest.mark.asyncio
async def test_argon2id_password_hashing():
    """
    Assert that password hashing produces valid Argon2id hashes ($argon2id$),
    verifies correctly with matching passwords, and rejects invalid passwords.
    """
    service = JWTAuthService()
    raw_password = "SuperSecureEnterprisePassword#2026!"

    # 1. Hashing creates valid Argon2id string
    hashed = service.hash_password(raw_password)
    assert isinstance(hashed, str)
    assert hashed.startswith("$argon2id$")

    # 2. Verify with matching password returns True
    assert service.verify_password(raw_password, hashed) is True

    # 3. Verify with wrong password returns False
    assert service.verify_password("WrongPassword123!", hashed) is False

    # 4. Verify with malformed hash returns False
    assert service.verify_password(raw_password, "invalid_hash_string") is False


@pytest.mark.asyncio
async def test_jwt_issue_and_validation():
    """
    Assert that JWT issue creates a token with valid payload claims (sub, username, email, roles, exp, iat, jti)
    and validates successfully.
    """
    service = JWTAuthService()
    user_id = uuid.uuid4()
    claims = {
        "sub": str(user_id),
        "username": "alice_data_engineer",
        "email": "alice@example.com",
        "roles": ["DATA_ENGINEER", "CATALOG_VIEWER"],
        "is_superadmin": False,
    }

    # Issue token
    token = service.create_access_token(
        data=claims,
        expires_delta=timedelta(minutes=30),
    )
    assert isinstance(token, str)
    assert len(token.split(".")) == 3

    # Decode and validate token
    decoded = service.decode_token(token)
    assert decoded["sub"] == str(user_id)
    assert decoded["username"] == "alice_data_engineer"
    assert decoded["email"] == "alice@example.com"
    assert decoded["roles"] == ["DATA_ENGINEER", "CATALOG_VIEWER"]
    assert decoded["is_superadmin"] is False
    assert "exp" in decoded
    assert "iat" in decoded
    assert "jti" in decoded


@pytest.mark.asyncio
async def test_jwt_expiration_handling():
    """
    Assert that expired JWTs raise TokenExpiredError.
    """
    service = JWTAuthService()
    claims = {
        "sub": str(uuid.uuid4()),
        "username": "expired_user",
    }

    # Create token already expired
    token = service.create_access_token(
        data=claims,
        expires_delta=timedelta(seconds=-10),
    )

    with pytest.raises(TokenExpiredError):
        service.decode_token(token)


@pytest.mark.asyncio
async def test_jwt_invalid_signature_and_tampering():
    """
    Assert that tokens signed with wrong secret key or tampered payloads raise InvalidTokenError.
    """
    service = JWTAuthService()
    claims = {"sub": str(uuid.uuid4()), "username": "tampered_user"}

    # Token signed with forged/different secret key
    forged_token = jwt.encode(
        claims,
        "an-attackers-unauthorized-secret-key-32b",
        algorithm="HS256",
    )

    with pytest.raises(InvalidTokenError):
        service.decode_token(forged_token)

    # Tampered token payload
    valid_token = service.create_access_token(data=claims)
    parts = valid_token.split(".")
    tampered_token = f"{parts[0]}.eyJob2dnZWQiOiJ0cnVlIn0.{parts[2]}"

    with pytest.raises(InvalidTokenError):
        service.decode_token(tampered_token)

    # Completely garbage token string
    with pytest.raises(InvalidTokenError):
        service.decode_token("not.a.valid.jwt.payload")


@pytest.mark.asyncio
async def test_user_authentication_success_against_postgres():
    """
    Assert that authenticate_user authenticates by either username or email
    against real PostgreSQL database with Argon2id password verification.
    """
    service = JWTAuthService()
    user_id = uuid.uuid4()
    username = f"auth_user_{uuid.uuid4().hex[:8]}"
    email = f"{username}@enterprise.org"
    password = "CorrectHorseBatteryStaple2026!"
    hashed_password = service.hash_password(password)

    role_id = uuid.uuid4()
    role_name = f"ROLE_{uuid.uuid4().hex[:6].upper()}"

    async with async_session_factory() as session:
        # Create role
        role = Role(
            id=role_id,
            name=role_name,
            description="Test role for auth",
            is_system_role=False,
        )
        session.add(role)

        # Create user
        user = User(
            id=user_id,
            username=username,
            email=email,
            hashed_password=hashed_password,
            full_name="Enterprise Auth User",
            status=UserStatus.ACTIVE,
            is_superadmin=False,
        )
        session.add(user)
        await session.flush()

        # Assign role
        user_role = UserRole(user_id=user.id, role_id=role.id)
        session.add(user_role)
        await session.commit()

    # 1. Authenticate with username
    async with async_session_factory() as session:
        auth_user = await service.authenticate_user(session, username, password)
        assert auth_user is not None
        assert auth_user.id == user_id
        assert auth_user.username == username

    # 2. Authenticate with email
    async with async_session_factory() as session:
        auth_user = await service.authenticate_user(session, email, password)
        assert auth_user is not None
        assert auth_user.id == user_id

    # 3. Issue token for user with fetched roles
    async with async_session_factory() as session:
        token = await service.issue_token_for_user(session, auth_user)
        decoded = service.decode_token(token)
        assert decoded["sub"] == str(user_id)
        assert decoded["username"] == username
        assert decoded["email"] == email
        assert role_name in decoded["roles"]
        assert decoded["is_superadmin"] is False


@pytest.mark.asyncio
async def test_user_authentication_invalid_credentials_and_inactive_status():
    """
    Assert that authenticate_user raises InvalidCredentialsError for wrong password
    or non-existent user, and InactiveUserError for SUSPENDED or DEACTIVATED accounts.
    """
    service = JWTAuthService()
    username = f"inactive_user_{uuid.uuid4().hex[:8]}"
    email = f"{username}@enterprise.org"
    password = "ValidPassword2026!"
    hashed_password = service.hash_password(password)

    async with async_session_factory() as session:
        # Non-existent user
        with pytest.raises(InvalidCredentialsError):
            await service.authenticate_user(session, "non_existent_user_9999", password)

        # Create active user, test wrong password
        user = User(
            id=uuid.uuid4(),
            username=username,
            email=email,
            hashed_password=hashed_password,
            full_name="Inactive Tester",
            status=UserStatus.SUSPENDED,
            is_superadmin=False,
        )
        session.add(user)
        await session.commit()

    # Suspended user raises InactiveUserError
    async with async_session_factory() as session:
        with pytest.raises(InactiveUserError):
            await service.authenticate_user(session, username, password)

    # Deactivated user raises InactiveUserError
    async with async_session_factory() as session:
        user_to_update = await session.get(User, user.id)
        user_to_update.status = UserStatus.DEACTIVATED
        await session.commit()

    async with async_session_factory() as session:
        with pytest.raises(InactiveUserError):
            await service.authenticate_user(session, username, password)

    # Active user with wrong password raises InvalidCredentialsError
    async with async_session_factory() as session:
        user_to_update = await session.get(User, user.id)
        user_to_update.status = UserStatus.ACTIVE
        await session.commit()

    async with async_session_factory() as session:
        with pytest.raises(InvalidCredentialsError):
            await service.authenticate_user(session, username, "WrongPassword!")

