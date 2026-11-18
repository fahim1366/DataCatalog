import pytest
from backend.src.core.config import Settings


def test_development_mode_allows_default_keys():
    """Development mode must allow default development keys without error."""
    settings = Settings(ENVIRONMENT="development")
    assert settings.JWT_SECRET_KEY == "change-this-in-production-to-a-secure-jwt-secret-key"
    assert settings.CATALOG_MASTER_KEK == "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
    assert settings.SECRET_KEY is not None
    assert settings.ENCRYPTION_KEY is not None


def test_production_mode_rejects_default_jwt_secret():
    """Production mode must enforce non-default SECRET_KEY / JWT_SECRET_KEY."""
    with pytest.raises(ValueError, match="Production security violation: In production mode, a non-default SECRET_KEY"):
        Settings(
            ENVIRONMENT="production",
            JWT_SECRET_KEY="change-this-in-production-to-a-secure-jwt-secret-key",
            CATALOG_MASTER_KEK="fedcba9876543210fedcba9876543210fedcba9876543210fedcba9876543210",
        )


def test_production_mode_rejects_empty_or_short_jwt_secret():
    """Production mode must reject empty or trivial JWT secrets."""
    with pytest.raises(ValueError, match="Production security violation: In production mode, a non-default SECRET_KEY"):
        Settings(
            ENVIRONMENT="production",
            JWT_SECRET_KEY="short",
            CATALOG_MASTER_KEK="fedcba9876543210fedcba9876543210fedcba9876543210fedcba9876543210",
        )


def test_production_mode_rejects_default_master_kek():
    """Production mode must enforce non-default ENCRYPTION_KEY / CATALOG_MASTER_KEK."""
    with pytest.raises(ValueError, match="Production security violation: In production mode, a non-default ENCRYPTION_KEY"):
        Settings(
            ENVIRONMENT="production",
            JWT_SECRET_KEY="super-secure-production-jwt-secret-key-32chars!",
            CATALOG_MASTER_KEK="0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
        )


def test_production_mode_accepts_valid_keys_via_aliases():
    """Production mode must accept custom SECRET_KEY and ENCRYPTION_KEY aliases."""
    custom_secret = "a-very-long-secure-jwt-secret-for-production-environment"
    custom_kek = "fedcba9876543210fedcba9876543210fedcba9876543210fedcba9876543210"

    settings = Settings(
        ENVIRONMENT="production",
        SECRET_KEY=custom_secret,
        ENCRYPTION_KEY=custom_kek,
    )
    assert settings.JWT_SECRET_KEY == custom_secret
    assert settings.SECRET_KEY == custom_secret
    assert settings.CATALOG_MASTER_KEK == custom_kek
    assert settings.ENCRYPTION_KEY == custom_kek
