from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    PROJECT_NAME: str = "Enterprise Data Catalog"
    VERSION: str = "1.0.0"
    API_V1_STR: str = "/api/v1"

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
    JWT_SECRET_KEY: str = "change-this-in-production-to-a-secure-jwt-secret-key"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )


settings: Settings = Settings()
