from sqlalchemy.orm import Session

from app.config import settings
from app.blockchain import register_on_chain
from app.db.session import SessionLocal
from app.models.models import BurnCertificate
from app.workers.common import run_forever


def _process_pending_burns(logger, db: Session) -> int:
    pending = (
        db.query(BurnCertificate)
        .filter(
            BurnCertificate.registry_tx_hash.is_(None),
            BurnCertificate.ipfs_json_cid.isnot(None),
            BurnCertificate.hash_sha256.isnot(None),
        )
        .order_by(BurnCertificate.created_at.asc())
        .limit(settings.WORKER_BATCH_SIZE)
        .all()
    )

    processed = 0
    for burn in pending:
        try:
            result = register_on_chain(
                certificate_hash_hex=burn.hash_sha256,
                ipfs_cid=burn.ipfs_json_cid,
            )
            burn.registry_tx_hash = result.tx_hash
            burn.registry_block = result.block_number
            burn.contract_address = result.contract_address
            burn.chain = result.chain
            db.commit()
            processed += 1
            logger.info(
                "burn_registered burn_id=%s tx_hash=%s",
                str(burn.burn_id),
                result.tx_hash,
            )
        except Exception as exc:  # pragma: no cover - runtime defensive path
            db.rollback()
            logger.exception("burn_register_failed burn_id=%s err=%s", str(burn.burn_id), exc)

    return processed


def _step(logger):
    db = SessionLocal()
    try:
        processed = _process_pending_burns(logger, db)
        logger.info("burn_batch_processed count=%s", processed)
    finally:
        db.close()


def run():
    run_forever("blockchain_burn_worker", _step)


if __name__ == "__main__":
    run()
