from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    neo4j_uri: str
    neo4j_username: str
    neo4j_password: str
    gemini_api_key: str

    class Config:
        env_file = ".env"

settings = Settings()