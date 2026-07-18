"""Configuration management for the Cloud Agent Platform."""
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Optional
from functools import lru_cache


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""
    
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )
    
    # Application
    app_name: str = "Cloud Agent Platform"
    app_version: str = "0.1.0"
    debug: bool = False
    
    # API
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    api_workers: int = 4
    
    # Security
    secret_key: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"
    jwt_expiration_minutes: int = 60
    
    # Redis
    redis_url: str = "redis://localhost:6379/0"
    redis_max_connections: int = 100
    
    # Sandbox
    sandbox_type: str = "docker"
    docker_base_image: str = "ghcr.io/openhands/agent-server:latest-python"
    sandbox_timeout_seconds: int = 3600
    sandbox_max_concurrent: int = 10
    
    # Kubernetes (optional)
    k8s_namespace: str = "agent-platform"
    k8s_api_server: Optional[str] = None
    k8s_cert_file: Optional[str] = None
    k8s_token_file: Optional[str] = None
    
    # LLM Configuration
    llm_provider: str = "openai"
    llm_model: str = "gpt-4o"
    llm_api_key: Optional[str] = None
    llm_base_url: Optional[str] = None
    llm_max_tokens: int = 4096
    llm_temperature: float = 0.7
    llm_max_retries: int = 3
    
    # Storage
    storage_type: str = "sqlite"
    database_url: str = "sqlite:///./agent_platform.db"
    
    # Rate Limiting
    rate_limit_requests: int = 100
    rate_limit_period_seconds: int = 60
    
    # Observability
    otlp_endpoint: Optional[str] = None
    log_level: str = "INFO"
    

@lru_cache()
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()
