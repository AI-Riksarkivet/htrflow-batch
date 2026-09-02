"""``htrflow-web`` console-script entrypoint: uvicorn serving on :8081."""

from __future__ import annotations

import uvicorn

from .app import create_app
from .kube import Config, Reader


def main() -> None:
    reader = Reader(Config.from_env())
    uvicorn.run(create_app(reader), host="0.0.0.0", port=8081)


if __name__ == "__main__":
    main()
