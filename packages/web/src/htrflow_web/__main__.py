"""Console-script entrypoints: ``htrflow-web`` on :8081, ``htrflow-results`` on :8082.

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
    app = create_app(reader, cfg.static_dir, cfg.batch_version)
    uvicorn.run(app, host="0.0.0.0", port=8081)


def results_app_from_env(env=None):
    from .results import ResultsConfig, create_results_app
    from .session import SessionCodec, load_key

    cfg = ResultsConfig.from_env(env)
    codec = SessionCodec(load_key(cfg.session_key_file), cfg.session_hours)
    if not cfg.s3_verify_tls:
        import logging

        logging.getLogger("htrflow_web.results").warning(
            "S3_VERIFY_TLS=false: the certificate of %s is not checked",
            cfg.s3_endpoint or "the default S3 endpoint",
        )
    return create_results_app(cfg, codec)


def results_main() -> None:
    uvicorn.run(results_app_from_env(), host="0.0.0.0", port=8082)


if __name__ == "__main__":
    main()
