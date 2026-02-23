from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    PROJECT_NAME: str = "Validation Engine — Solar One HUB"
    VERSION: str = "0.1.0"

    # PostgreSQL + TimescaleDB
    POSTGRES_USER: str = "solarone"
    POSTGRES_PASSWORD: str = "solarone_secret"
    POSTGRES_DB: str = "validation_engine"
    POSTGRES_HOST: str = "db"
    POSTGRES_PORT: int = 5432

    @property
    def DATABASE_URL(self) -> str:
        return (
            f"postgresql://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )

    class Config:
        env_file = ".env"


settings = Settings()
