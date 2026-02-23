"""
Serviço de Lotes HEC — Agrupamento de certificados para comercialização

Regras de negócio:
  1. Só HECs com backing completo podem entrar em lotes:
     - validation.status == "approved"
     - ipfs_json_cid IS NOT NULL
     - registry_tx_hash IS NOT NULL
  2. Cada HEC só pode pertencer a um lote (lot_id unique per HEC)
  3. Lote calcula total_quantity, available_quantity, total_energy_kwh
  4. Status do HEC muda para "listed" ao entrar em lote

Fluxo:
  1. POST /lots/create com lista de hec_ids + nome
  2. Valida cada HEC: backing completo? Já em outro lote?
  3. Cria hec_lot com totais
  4. Atribui lot_id a cada HEC
  5. Retorna lote com detalhes
"""
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import List, Optional

from sqlalchemy.orm import Session

from app.models.models import HECCertificate, HECLot, Validation


# ---------------------------------------------------------------------------
# Resultado
# ---------------------------------------------------------------------------

@dataclass
class LotCreationResult:
    """Resultado da criação de um lote."""
    lot_id: uuid.UUID
    name: str
    description: Optional[str]
    total_quantity: int
    available_quantity: int
    total_energy_kwh: float
    certificate_count: int
    status: str
    hec_ids: List[str]
    created_at: datetime


# ---------------------------------------------------------------------------
# Validação de backing completo
# ---------------------------------------------------------------------------

def validate_hec_backing(hec: HECCertificate) -> Optional[str]:
    """
    Valida se um HEC tem backing completo para entrar em lote.

    Retorna None se OK, ou string com motivo da rejeição.

    Critérios (TODOS obrigatórios):
      1. validation.status == "approved" (via HEC status == "registered")
      2. ipfs_json_cid IS NOT NULL
      3. registry_tx_hash IS NOT NULL
    """
    # Critério 1: Status registered (implica validation approved)
    if hec.status not in ("registered", "minted"):
        return (
            f"HEC {hec.hec_id} — status={hec.status}, "
            f"requerido: registered (backing completo)"
        )

    # Critério 2: IPFS CID
    if not hec.ipfs_json_cid:
        return (
            f"HEC {hec.hec_id} — ipfs_json_cid ausente, "
            f"upload IPFS necessário"
        )

    # Critério 3: On-chain registry
    if not hec.registry_tx_hash:
        return (
            f"HEC {hec.hec_id} — registry_tx_hash ausente, "
            f"registro on-chain necessário"
        )

    return None  # All checks passed


def validate_hec_not_in_lot(hec: HECCertificate) -> Optional[str]:
    """Valida se HEC não está já em outro lote."""
    if hec.lot_id is not None:
        return (
            f"HEC {hec.hec_id} já pertence ao lote {hec.lot_id}"
        )
    return None


# ---------------------------------------------------------------------------
# Create lot
# ---------------------------------------------------------------------------

def create_lot(
    db: Session,
    hec_ids: List[uuid.UUID],
    name: str,
    description: Optional[str] = None,
    price_per_kwh: Optional[float] = None,
) -> LotCreationResult:
    """
    Cria um lote de HECs backed.

    Pipeline:
      1. Busca todos os HECs
      2. Valida backing completo de cada um
      3. Valida que nenhum está em outro lote
      4. Cria HECLot com totais calculados
      5. Atribui lot_id a cada HEC + status=listed
      6. Retorna resultado

    Args:
        db: Sessão do banco
        hec_ids: Lista de IDs dos HECs a incluir no lote
        name: Nome do lote
        description: Descrição opcional
        price_per_kwh: Preço por kWh (opcional)

    Returns:
        LotCreationResult

    Raises:
        ValueError: Se lista vazia, HEC não encontrado, backing incompleto,
                    ou HEC já em outro lote
    """
    if not hec_ids:
        raise ValueError("Lista de HEC IDs não pode ser vazia")

    # Remove duplicatas mantendo ordem
    seen = set()
    unique_ids = []
    for hid in hec_ids:
        if hid not in seen:
            seen.add(hid)
            unique_ids.append(hid)

    # 1. Buscar todos os HECs
    hecs = []
    for hid in unique_ids:
        hec = db.query(HECCertificate).filter(
            HECCertificate.hec_id == hid
        ).first()

        if not hec:
            raise ValueError(f"HEC {hid} não encontrado")
        hecs.append(hec)

    # 2. Validar backing completo
    errors = []
    for hec in hecs:
        err = validate_hec_backing(hec)
        if err:
            errors.append(err)

    if errors:
        raise ValueError(
            f"Backing incompleto para {len(errors)} HEC(s):\n"
            + "\n".join(f"  • {e}" for e in errors)
        )

    # 3. Validar não está em outro lote
    for hec in hecs:
        err = validate_hec_not_in_lot(hec)
        if err:
            raise ValueError(err)

    # 4. Calcular totais
    total_energy = sum(float(hec.energy_kwh) for hec in hecs)
    qty = len(hecs)

    # 5. Criar lote
    lot_id = uuid.uuid4()
    lot = HECLot(
        lot_id=lot_id,
        name=name,
        description=description,
        total_energy_kwh=Decimal(str(total_energy)),
        total_quantity=qty,
        available_quantity=qty,
        certificate_count=qty,
        price_per_kwh=Decimal(str(price_per_kwh)) if price_per_kwh else None,
        status="open",
    )
    db.add(lot)

    # 6. Atribuir lot_id a cada HEC
    for hec in hecs:
        hec.lot_id = lot_id
        hec.status = "listed"

    # Don't commit — caller controls transaction

    return LotCreationResult(
        lot_id=lot_id,
        name=name,
        description=description,
        total_quantity=qty,
        available_quantity=qty,
        total_energy_kwh=total_energy,
        certificate_count=qty,
        status="open",
        hec_ids=[str(hec.hec_id) for hec in hecs],
        created_at=datetime.now(timezone.utc),
    )
