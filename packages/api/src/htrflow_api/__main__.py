"""``htrflow-api``: reads its settings from the environment and serves."""

from __future__ import annotations

import uvicorn

from htrflow_api.app import create_app
from htrflow_api.config import ApiConfig
from htrflow_api.tokens import Verifier


def main() -> None:
    try:
        cfg = ApiConfig.from_env()
    except ValueError as e:
        raise SystemExit(f"htrflow-api: {e}") from None
    uvicorn.run(create_app(cfg, Verifier(cfg)), host="0.0.0.0", port=8083)  # noqa: S104


if __name__ == "__main__":
    main()
