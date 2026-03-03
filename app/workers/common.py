import logging
import time

from app.config import settings

LOGGER_FORMAT = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"


def configure_logging(name: str) -> logging.Logger:
    logging.basicConfig(level=logging.INFO, format=LOGGER_FORMAT)
    return logging.getLogger(name)


def run_forever(worker_name: str, step_fn):
    logger = configure_logging(worker_name)
    logger.info("worker_started poll_seconds=%s", settings.WORKER_POLL_SECONDS)
    while True:
        start = time.time()
        try:
            step_fn(logger)
        except Exception as exc:  # pragma: no cover - runtime defensive path
            logger.exception("worker_cycle_failed err=%s", exc)
        elapsed_ms = int((time.time() - start) * 1000)
        logger.info("worker_cycle_done elapsed_ms=%s", elapsed_ms)
        time.sleep(max(settings.WORKER_POLL_SECONDS, 1))
