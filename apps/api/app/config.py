from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


def _resolve_roots() -> tuple[Path, Path]:
    """返回 (api_dir, repo_root)。Docker 中仅有 /app，需容错。"""
    cfg = Path(__file__).resolve()
    api_dir = cfg.parents[1]  # .../app/config.py -> .../ (api package parent)
    # 本地: .../apps/api/app/config.py -> parents[1]=apps/api, parents[3]=repo
    # Docker: /app/app/config.py -> parents[1]=/app
    repo_root = api_dir
    if len(cfg.parents) > 3:
        candidate = cfg.parents[3]
        if (candidate / "apps").exists() or (candidate / "docker-compose.yml").exists():
            repo_root = candidate
    return api_dir, repo_root


_API_DIR, _ROOT = _resolve_roots()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(
            str(_API_DIR / ".env"),
            str(_ROOT / ".env"),
            ".env",
        ),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    fuyao_api_key: str = ""
    fuyao_base_url: str = "https://fuyao.aicubes.cn"

    llm_api_key: str = ""
    llm_base_url: str = "https://api.deepseek.com"
    llm_model: str = "deepseek-flash"

    # iFinD MCP（解释增强，不参与筛选）
    ifind_mcp_token: str = ""

    api_host: str = "0.0.0.0"
    api_port: int = 8000
    cors_origins: str = "http://localhost:5173,http://localhost:3000"
    sqlite_path: str = "./data/strategies.db"
    use_mock_data: bool = False

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def sqlite_file(self) -> Path:
        path = Path(self.sqlite_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        return path


@lru_cache
def get_settings() -> Settings:
    return Settings()
