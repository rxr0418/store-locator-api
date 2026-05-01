import os
from dotenv import load_dotenv

load_dotenv()


def _fix_database_url(url: str) -> str:
    """Railway/Render/Heroku give 'postgres://' but SQLAlchemy needs 'postgresql://'."""
    if url and url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql://", 1)
    return url


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-key-change-in-prod")
    SQLALCHEMY_DATABASE_URI = _fix_database_url(
        os.environ.get("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/store_locator")
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {
        "pool_pre_ping": True,
        "pool_recycle": 300,
    }

    # JWT
    JWT_SECRET_KEY = os.environ.get("JWT_SECRET_KEY", "jwt-secret-change-in-prod")
    JWT_ACCESS_TOKEN_EXPIRES = int(os.environ.get("JWT_ACCESS_TOKEN_EXPIRES", 900))   # 15 min
    JWT_REFRESH_TOKEN_EXPIRES = int(os.environ.get("JWT_REFRESH_TOKEN_EXPIRES", 604800))  # 7 days

    # Caching
    RATELIMIT_STORAGE_URL = os.environ.get("RATE_LIMIT_STORAGE_URL", "memory://")
    RATELIMIT_DEFAULT = "100 per hour"

    # Geocoding
    GEOCODING_USER_AGENT = os.environ.get("GEOCODING_USER_AGENT", "store-locator-app")

    # Search defaults
    DEFAULT_SEARCH_RADIUS_MILES = 10
    MAX_SEARCH_RADIUS_MILES = 100
    MAX_SEARCH_RESULTS = 50


class DevelopmentConfig(Config):
    FLASK_DEBUG = True


class ProductionConfig(Config):
    FLASK_DEBUG = False
    # Stricter pool settings for production
    SQLALCHEMY_ENGINE_OPTIONS = {
        "pool_pre_ping": True,
        "pool_recycle": 300,
        "pool_size": 5,
        "max_overflow": 10,
    }


class TestingConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = _fix_database_url(
        os.environ.get(
            "TEST_DATABASE_URL",
            "postgresql://postgres:postgres@localhost:5432/store_locator_test",
        )
    )
    JWT_ACCESS_TOKEN_EXPIRES = 300
    RATELIMIT_ENABLED = False


config_map = {
    "development": DevelopmentConfig,
    "production": ProductionConfig,
    "testing": TestingConfig,
}


def get_config():
    env = os.environ.get("FLASK_ENV", "development")
    return config_map.get(env, DevelopmentConfig)
