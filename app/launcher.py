import os
import subprocess

import uvicorn

from app.config import settings


def _run_migrations_if_enabled():
    if not settings.RUN_DB_MIGRATIONS_ON_BOOT:
        return
    subprocess.run(["alembic", "upgrade", "head"], check=True)


def _run_api():
    _run_migrations_if_enabled()
    uvicorn.run(
        "app.main_api:app",
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8000")),
    )


def _run_consumer():
    _run_migrations_if_enabled()
    uvicorn.run(
        "app.main_consumer:app",
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8000")),
    )


def main():
    layer = settings.SERVICE_LAYER.lower().strip()

    if layer == "api":
        _run_api()
        return

    if layer == "consumer":
        _run_consumer()
        return

    if layer == "ds_cross_validation":
        from app.workers.ds_cross_validation_worker import run

        run()
        return

    if layer == "blockchain_mint":
        from app.workers.blockchain_mint_worker import run

        run()
        return

    if layer == "blockchain_burn":
        from app.workers.blockchain_burn_worker import run

        run()
        return

    raise SystemExit(
        "SERVICE_LAYER invalido. Use: "
        "api | consumer | ds_cross_validation | blockchain_mint | blockchain_burn"
    )


if __name__ == "__main__":
    main()
