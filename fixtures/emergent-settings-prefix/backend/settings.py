from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    api_prefix: str = "/api"


settings = Settings()
