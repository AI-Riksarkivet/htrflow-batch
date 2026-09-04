"""``htrflow-web`` console-script entrypoint: uvicorn serving on :8081.

``HTRFLOW_WEB_SITE_ONLY`` serves the built site without a cluster (the local
compose stack, `.docker/docker-compose.yml`): the API routes stay registered
and answer 503 instead of the process failing at startup on a missing
kubeconfig.
"""

from __future__ import annotations

import uvicorn

from .app import NoCluster, create_app
from .kube import Config, Reader


def main() -> None:
    cfg = Config.from_env()
    reader = NoCluster() if cfg.site_only else Reader(cfg)
    uvicorn.run(create_app(reader, cfg.static_dir), host="0.0.0.0", port=8081)


if __name__ == "__main__":
    main()
