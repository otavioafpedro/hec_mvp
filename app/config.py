from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    PROJECT_NAME: str = "Validation Engine - Solar One HUB"
    VERSION: str = "0.1.0"
    SERVICE_LAYER: str = "api"
    RUN_DB_MIGRATIONS_ON_BOOT: bool = False
    WORKER_POLL_SECONDS: int = 20
    WORKER_BATCH_SIZE: int = 50
    DS_LOOKBACK_HOURS: int = 24
    DS_ENABLE_EXTERNAL_FETCH: bool = True
    DEFAULT_GRID_ZONE: str = "BR-CS"

    # External integrations (ported from SOA gateway)
    SOLCAST_API_KEY: str = ""
    SOLCAST_BASE_URL: str = "https://api.solcast.com.au"

    COPERNICUS_CLIENT_ID: str = ""
    COPERNICUS_CLIENT_SECRET: str = ""
    NASA_POWER_BASE_URL: str = "https://power.larc.nasa.gov/api"

    INMET_BASE_URL: str = "https://apitempo.inmet.gov.br"

    OPENWEATHER_API_KEY: str = ""
    OPENWEATHER_BASE_URL: str = "https://api.openweathermap.org/data/3.0"

    ELECTRICITY_MAPS_KEY: str = ""
    ELECTRICITY_MAPS_URL: str = "https://api.electricitymap.org/v3"

    # Social OAuth (Google / LinkedIn)
    OAUTH_STATE_SECRET: str = "hec-oauth-state-secret-change-in-production"
    OAUTH_STATE_TTL_SECONDS: int = 600
    OAUTH_DEFAULT_FRONTEND_REDIRECT_URI: str = "http://localhost:3000/auth/social/callback"
    OAUTH_ALLOWED_REDIRECT_HOSTS: str = ""
    GOOGLE_CLIENT_ID: str = ""
    GOOGLE_CLIENT_SECRET: str = ""
    GOOGLE_REDIRECT_URI: str = ""
    LINKEDIN_CLIENT_ID: str = ""
    LINKEDIN_CLIENT_SECRET: str = ""
    LINKEDIN_REDIRECT_URI: str = ""

    # Legacy PostgreSQL (validation-engine schema)
    POSTGRES_USER: str = "solarone"
    POSTGRES_PASSWORD: str = "solarone_secret"
    POSTGRES_DB: str = "validation_engine"
    POSTGRES_HOST: str = "db"
    POSTGRES_PORT: int = 5432
    POSTGRES_DSN: str | None = None

    # SOA ingest feature flag (MariaDB + Timeseries split)
    SOA_ENABLE_INGEST: bool = False

    # MariaDB transactional schema (sql_hec_soa/mysql_schema.sql)
    SOA_MYSQL_USER: str = "solarone"
    SOA_MYSQL_PASSWORD: str = "solarone_secret"
    SOA_MYSQL_DB: str = "soa_sos"
    SOA_MYSQL_HOST: str = "mariadb"
    SOA_MYSQL_PORT: int = 3306
    SOA_MYSQL_DSN: str | None = None

    # PostgreSQL timeseries schema (sql_hec_soa/postgres_timeseries.sql)
    SOA_TIMESERIES_USER: str | None = None
    SOA_TIMESERIES_PASSWORD: str | None = None
    SOA_TIMESERIES_DB: str | None = None
    SOA_TIMESERIES_HOST: str | None = None
    SOA_TIMESERIES_PORT: int | None = None
    SOA_TIMESERIES_DSN: str | None = None

    @property
    def DATABASE_URL(self) -> str:
        if self.POSTGRES_DSN:
            return self.POSTGRES_DSN
        return (
            f"postgresql://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )

    @property
    def SOA_MYSQL_URL(self) -> str:
        if self.SOA_MYSQL_DSN:
            return self.SOA_MYSQL_DSN
        return (
            f"mysql+pymysql://{self.SOA_MYSQL_USER}:{self.SOA_MYSQL_PASSWORD}"
            f"@{self.SOA_MYSQL_HOST}:{self.SOA_MYSQL_PORT}/{self.SOA_MYSQL_DB}"
        )

    @property
    def SOA_TIMESERIES_URL(self) -> str:
        if self.SOA_TIMESERIES_DSN:
            return self.SOA_TIMESERIES_DSN

        user = self.SOA_TIMESERIES_USER or self.POSTGRES_USER
        password = self.SOA_TIMESERIES_PASSWORD or self.POSTGRES_PASSWORD
        db_name = self.SOA_TIMESERIES_DB or self.POSTGRES_DB
        host = self.SOA_TIMESERIES_HOST or self.POSTGRES_HOST
        port = self.SOA_TIMESERIES_PORT or self.POSTGRES_PORT
        return f"postgresql://{user}:{password}@{host}:{port}/{db_name}"

    class Config:
        env_file = ".env"


settings = Settings()
