from typing import Optional
from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    PROJECT_NAME: str = "Enterprise Data Catalog"
    VERSION: str = "1.0.0"
    API_V1_STR: str = "/api/v1"
    ENVIRONMENT: str = "development"

    # PostgreSQL Database URL
    DATABASE_URL: str = "postgresql+asyncpg://postgres:12345678@127.0.0.1:5432/enterprise_datacatalog"
    DATABASE_SYNC_URL: str = "postgresql+psycopg2://postgres:12345678@127.0.0.1:5432/enterprise_datacatalog"

    # Enterprise Connection Pool Settings (Optimized for 160k tables / 800 DBs concurrent access)
    DB_POOL_SIZE: int = 20
    DB_MAX_OVERFLOW: int = 40
    DB_POOL_TIMEOUT: int = 30
    DB_POOL_RECYCLE: int = 1800
    DB_POOL_PRE_PING: bool = True
    DB_ECHO: bool = False

    # Security & Cryptography
    CATALOG_MASTER_KEK: str = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
    ENCRYPTION_KEY: Optional[str] = None
    JWT_SECRET_KEY: str = "change-this-in-production-to-a-secure-jwt-secret-key"
    SECRET_KEY: Optional[str] = None
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60

    # Redis & Celery Distributed Broker Configuration
    REDIS_URL: str = "redis://redis:6379/0"
    CELERY_BROKER_URL: str = "redis://redis:6379/0"
    CELERY_RESULT_BACKEND: str = "redis://redis:6379/0"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    @model_validator(mode="after")
    def validate_production_security(self) -> "Settings":
        if self.SECRET_KEY:
            self.JWT_SECRET_KEY = self.SECRET_KEY
        else:
            self.SECRET_KEY = self.JWT_SECRET_KEY

        if self.ENCRYPTION_KEY:
            self.CATALOG_MASTER_KEK = self.ENCRYPTION_KEY
        else:
            self.ENCRYPTION_KEY = self.CATALOG_MASTER_KEK

        env = (self.ENVIRONMENT or "").strip().lower()
        if env in ("production", "prod"):
            insecure_secrets = {
                "change-this-in-production-to-a-secure-jwt-secret-key",
                "secret",
                "changeme",
                "default",
                "",
            }
            if not self.JWT_SECRET_KEY or self.JWT_SECRET_KEY in insecure_secrets or len(self.JWT_SECRET_KEY) < 16:
                raise ValueError(
                    "Production security violation: In production mode, a non-default SECRET_KEY / JWT_SECRET_KEY of at least 16 characters must be configured."
                )

            insecure_keks = {
                "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
                "",
            }
            if not self.CATALOG_MASTER_KEK or self.CATALOG_MASTER_KEK in insecure_keks:
                raise ValueError(
                    "Production security violation: In production mode, a non-default ENCRYPTION_KEY / CATALOG_MASTER_KEK (64 hex characters) must be configured."
                )

        return self


settings: Settings = Settings()
