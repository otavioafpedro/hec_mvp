from sqlalchemy.orm import Session

from app.config import settings
from app.blockchain import register_on_chain
from app.db.session import SessionLocal
from app.models.models import HECCertificate
from app.workers.common import run_forever


def _process_pending_hecs(logger, db: Session) -> int:
    pending = (
        db.query(HECCertificate)
        .filter(
            HECCertificate.registry_tx_hash.is_(None),
            HECCertificate.ipfs_json_cid.isnot(None),
            HECCertificate.status.in_(["pending", "minted", "custodied", "allocated"]),
        )
        .order_by(HECCertificate.created_at.asc())
        .limit(settings.WORKER_BATCH_SIZE)
        .all()
    )

    processed = 0
    for hec in pending:
        try:
            result = register_on_chain(
                certificate_hash_hex=hec.hash_sha256,
                ipfs_cid=hec.ipfs_json_cid,
            )
            hec.registry_tx_hash = result.tx_hash
            hec.registry_block = result.block_number
            hec.contract_address = result.contract_address
            hec.registered_at = result.registered_at
            hec.chain = result.chain
            hec.status = "registered"
            db.commit()
            processed += 1
            logger.info(
                "hec_registered hec_id=%s tx_hash=%s",
                str(hec.hec_id),
                result.tx_hash,
            )
        except Exception as exc:  # pragma: no cover - runtime defensive path
            db.rollback()
            logger.exception("hec_register_failed hec_id=%s err=%s", str(hec.hec_id), exc)

    return processed


def _step(logger):
    db = SessionLocal()
    try:
        processed = _process_pending_hecs(logger, db)
        logger.info("mint_batch_processed count=%s", processed)
    finally:
        db.close()


def run():
    run_forever("blockchain_mint_worker", _step)


if __name__ == "__main__":
    run()

