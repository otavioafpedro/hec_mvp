"""
ServiÃ§o IPFS â€” Upload e verificaÃ§Ã£o de certificados HEC

Sobe JSON canÃ´nico + PDF do certificado para IPFS e retorna CIDs.
Na verificaÃ§Ã£o, baixa do IPFS, recalcula SHA-256 e compara 100%.

Providers (plugÃ¡vel â€” mesmo padrÃ£o do satellite_fetcher):
  - MockIPFSProvider: armazena em memÃ³ria para dev/test (padrÃ£o)
  - PinataProvider: API Pinata (produÃ§Ã£o) [stub]
  - LocalIPFSProvider: nÃ³ IPFS local via HTTP API [stub]

Fluxo de upload:
  1. Serializa JSON canÃ´nico (sort_keys, compact separators)
  2. Upload JSON â†’ CID_json
  3. Upload PDF â†’ CID_pdf
  4. Retorna IPFSUploadResult com ambos CIDs

Fluxo de verificaÃ§Ã£o:
  1. Baixa JSON do IPFS via CID
  2. Recalcula SHA-256 do conteÃºdo baixado
  3. Compara com hash armazenado
  4. Resultado: match 100% ou TAMPERED

Garantia de integridade:
  - JSON Ã© serializado de forma determinÃ­stica (sort_keys=True)
  - SHA-256 recalculado byte-a-byte
  - Qualquer 1 bit alterado â†’ hash diverge â†’ TAMPERED
"""
import hashlib
import json
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, Dict


# ---------------------------------------------------------------------------
# Resultados
# ---------------------------------------------------------------------------

@dataclass
class IPFSUploadResult:
    """Resultado do upload para IPFS."""
    json_cid: str           # CID do JSON canÃ´nico
    pdf_cid: str            # CID do PDF
    json_size_bytes: int    # Tamanho do JSON
    pdf_size_bytes: int     # Tamanho do PDF
    provider: str           # "mock" | "pinata" | "local"
    pinned: bool            # True se conteÃºdo foi pinado


@dataclass
class IPFSVerifyResult:
    """Resultado da verificacao de integridade via IPFS."""
    verified: bool              # True se hash bate 100%
    hec_id: str                 # ID do certificado verificado
    stored_hash: str            # Hash armazenado no DB
    recalculated_hash: str      # Hash recalculado do IPFS
    match: bool                 # stored_hash == recalculated_hash
    json_cid: Optional[str]     # CID do JSON usado na verificacao
    pdf_cid: Optional[str]      # CID do PDF
    json_size_bytes: int        # Tamanho do JSON baixado
    ipfs_provider: str          # Provider usado
    verified_at: str            # Timestamp da verificacao
    certificate_json: Optional[dict] = None  # JSON recuperado do IPFS
    reason: str = ""            # Motivo se falhou


@dataclass
class IPFSJsonUploadResult:
    """Resultado do upload de um documento JSON sem PDF associado."""
    json_cid: str
    json_size_bytes: int
    provider: str
    pinned: bool


# ---------------------------------------------------------------------------
# Provider ABC
# ---------------------------------------------------------------------------

class IPFSProvider(ABC):
    """Interface para providers IPFS."""

    @abstractmethod
    def upload(self, data: bytes, filename: str) -> str:
        """Upload bytes para IPFS, retorna CID."""
        ...

    @abstractmethod
    def download(self, cid: str) -> Optional[bytes]:
        """Download bytes do IPFS por CID. None se nÃ£o encontrado."""
        ...

    @abstractmethod
    def pin(self, cid: str) -> bool:
        """Pin CID no IPFS. Retorna True se sucesso."""
        ...

    @property
    @abstractmethod
    def name(self) -> str:
        """Nome do provider."""
        ...


# ---------------------------------------------------------------------------
# Mock Provider (dev/test)
# ---------------------------------------------------------------------------

class MockIPFSProvider(IPFSProvider):
    """
    Provider IPFS mock â€” armazena em memÃ³ria.

    Gera CIDs determinÃ­sticos baseados em SHA-256 do conteÃºdo.
    Formato: Qm + hex[:44] (simulando CIDv0 IPFS).
    """

    def __init__(self):
        self._store: Dict[str, bytes] = {}
        self._pinned: set = set()

    def upload(self, data: bytes, filename: str) -> str:
        content_hash = hashlib.sha256(data).hexdigest()
        cid = f"Qm{content_hash[:44]}"
        self._store[cid] = data
        return cid

    def download(self, cid: str) -> Optional[bytes]:
        return self._store.get(cid)

    def pin(self, cid: str) -> bool:
        if cid in self._store:
            self._pinned.add(cid)
            return True
        return False

    @property
    def name(self) -> str:
        return "mock"

    def clear(self):
        """Limpa o store (para testes)."""
        self._store.clear()
        self._pinned.clear()

    @property
    def store_size(self) -> int:
        return len(self._store)


class TamperedMockIPFSProvider(MockIPFSProvider):
    """
    Provider mock que simula adulteraÃ§Ã£o â€” altera 1 byte no download.

    Usado exclusivamente para testar detecÃ§Ã£o de tampering.
    """

    def download(self, cid: str) -> Optional[bytes]:
        data = self._store.get(cid)
        if data and len(data) > 10:
            # Flip 1 bit no byte 10 â€” simula adulteraÃ§Ã£o
            tampered = bytearray(data)
            tampered[10] = (tampered[10] + 1) % 256
            return bytes(tampered)
        return data


class MissingMockIPFSProvider(MockIPFSProvider):
    """Provider mock que simula CID nÃ£o encontrado no IPFS."""

    def download(self, cid: str) -> Optional[bytes]:
        return None  # Sempre retorna None


# ---------------------------------------------------------------------------
# Pinata Provider (production stub)
# ---------------------------------------------------------------------------

class PinataProvider(IPFSProvider):
    """
    Provider Pinata â€” stub para produÃ§Ã£o.

    Em produÃ§Ã£o real, usa pinata.cloud API:
      POST https://api.pinata.cloud/pinning/pinFileToIPFS
      Authorization: Bearer JWT_TOKEN
    """

    def __init__(self, api_key: str = "", secret: str = ""):
        self._api_key = api_key
        self._secret = secret

    def upload(self, data: bytes, filename: str) -> str:
        # TODO: implementar chamada real Ã  API Pinata
        raise NotImplementedError("PinataProvider nÃ£o implementado â€” use MockIPFSProvider")

    def download(self, cid: str) -> Optional[bytes]:
        # TODO: GET https://gateway.pinata.cloud/ipfs/{cid}
        raise NotImplementedError("PinataProvider download nÃ£o implementado")

    def pin(self, cid: str) -> bool:
        raise NotImplementedError("PinataProvider pin nÃ£o implementado")

    @property
    def name(self) -> str:
        return "pinata"


# ---------------------------------------------------------------------------
# Local IPFS Node Provider (production stub)
# ---------------------------------------------------------------------------

class LocalIPFSProvider(IPFSProvider):
    """
    Provider para nÃ³ IPFS local.

    Em produÃ§Ã£o real, usa HTTP API do nÃ³ IPFS:
      POST http://localhost:5001/api/v0/add
      POST http://localhost:5001/api/v0/cat?arg={cid}
    """

    def __init__(self, api_url: str = "http://localhost:5001"):
        self._api_url = api_url

    def upload(self, data: bytes, filename: str) -> str:
        raise NotImplementedError("LocalIPFSProvider nÃ£o implementado")

    def download(self, cid: str) -> Optional[bytes]:
        raise NotImplementedError("LocalIPFSProvider download nÃ£o implementado")

    def pin(self, cid: str) -> bool:
        raise NotImplementedError("LocalIPFSProvider pin nÃ£o implementado")

    @property
    def name(self) -> str:
        return "local"


# ---------------------------------------------------------------------------
# Singleton provider (injetÃ¡vel para testes)
# ---------------------------------------------------------------------------

_ipfs_provider: IPFSProvider = MockIPFSProvider()


def get_ipfs_provider() -> IPFSProvider:
    return _ipfs_provider


def set_ipfs_provider(provider: IPFSProvider) -> None:
    global _ipfs_provider
    _ipfs_provider = provider


def reset_ipfs_provider() -> None:
    global _ipfs_provider
    _ipfs_provider = MockIPFSProvider()


# ---------------------------------------------------------------------------
# Upload JSON + PDF to IPFS
# ---------------------------------------------------------------------------

def upload_certificate_to_ipfs(
    certificate_json: dict,
    pdf_bytes: bytes,
    hec_id: str,
    provider: Optional[IPFSProvider] = None,
) -> IPFSUploadResult:
    """
    Upload JSON canÃ´nico + PDF do certificado para IPFS.

    Args:
        certificate_json: JSON do certificado HEC
        pdf_bytes: PDF binÃ¡rio do certificado
        hec_id: ID do certificado (para filenames)
        provider: Provider IPFS (default: singleton)

    Returns:
        IPFSUploadResult com CIDs do JSON e PDF
    """
    prov = provider or get_ipfs_provider()

    # Serializar JSON de forma determinÃ­stica (mesmo que compute_certificate_hash)
    json_bytes = json.dumps(
        certificate_json,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")

    # Upload JSON
    json_cid = prov.upload(json_bytes, f"hec-{hec_id}.json")
    prov.pin(json_cid)

    # Upload PDF
    pdf_cid = prov.upload(pdf_bytes, f"hec-{hec_id}.pdf")
    prov.pin(pdf_cid)

    return IPFSUploadResult(
        json_cid=json_cid,
        pdf_cid=pdf_cid,
        json_size_bytes=len(json_bytes),
        pdf_size_bytes=len(pdf_bytes),
        provider=prov.name,
        pinned=True,
    )


def upload_json_document_to_ipfs(
    payload: dict,
    document_id: str,
    filename_prefix: str = "document",
    provider: Optional[IPFSProvider] = None,
) -> IPFSJsonUploadResult:
    """
    Upload a canonical JSON-only document to IPFS.

    Used for lot manifests and other custody ledger artifacts that do not
    require a paired PDF representation.
    """
    prov = provider or get_ipfs_provider()
    json_bytes = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")

    json_cid = prov.upload(json_bytes, f"{filename_prefix}-{document_id}.json")
    pinned = prov.pin(json_cid)

    return IPFSJsonUploadResult(
        json_cid=json_cid,
        json_size_bytes=len(json_bytes),
        provider=prov.name,
        pinned=pinned,
    )
# ---------------------------------------------------------------------------
# Download and verify from IPFS
# ---------------------------------------------------------------------------

def verify_certificate_from_ipfs(
    hec_id: str,
    stored_hash: str,
    json_cid: str,
    pdf_cid: Optional[str] = None,
    provider: Optional[IPFSProvider] = None,
) -> IPFSVerifyResult:
    """
    Verifica integridade do certificado HEC via IPFS.

    Pipeline:
      1. Baixa JSON canÃ´nico do IPFS via CID
      2. Recalcula SHA-256 dos bytes baixados
      3. Compara com hash armazenado no DB
      4. Match 100% = VERIFIED, qualquer divergÃªncia = TAMPERED

    Args:
        hec_id: ID do certificado
        stored_hash: SHA-256 armazenado no DB
        json_cid: CID do JSON no IPFS
        pdf_cid: CID do PDF (opcional, para referÃªncia)
        provider: Provider IPFS (default: singleton)

    Returns:
        IPFSVerifyResult com resultado da verificaÃ§Ã£o
    """
    prov = provider or get_ipfs_provider()
    now = datetime.now(timezone.utc).isoformat() + "Z"

    # 1. Download JSON from IPFS
    json_bytes = prov.download(json_cid)

    if json_bytes is None:
        return IPFSVerifyResult(
            verified=False,
            hec_id=hec_id,
            stored_hash=stored_hash,
            recalculated_hash="",
            match=False,
            json_cid=json_cid,
            pdf_cid=pdf_cid,
            json_size_bytes=0,
            ipfs_provider=prov.name,
            verified_at=now,
            reason=f"JSON nÃ£o encontrado no IPFS (CID: {json_cid})",
        )

    # 2. Recalculate SHA-256 from downloaded bytes
    recalculated_hash = hashlib.sha256(json_bytes).hexdigest()

    # 3. Compare with stored hash
    match = recalculated_hash == stored_hash

    # 4. Parse JSON for response
    cert_json = None
    try:
        cert_json = json.loads(json_bytes.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        pass

    reason = ""
    if match:
        reason = "VERIFIED â€” Hash IPFS bate 100% com hash armazenado"
    else:
        reason = (
            f"TAMPERED â€” Hash diverge! "
            f"Armazenado: {stored_hash[:16]}..., "
            f"IPFS: {recalculated_hash[:16]}..."
        )

    return IPFSVerifyResult(
        verified=match,
        hec_id=hec_id,
        stored_hash=stored_hash,
        recalculated_hash=recalculated_hash,
        match=match,
        json_cid=json_cid,
        pdf_cid=pdf_cid,
        json_size_bytes=len(json_bytes),
        ipfs_provider=prov.name,
        verified_at=now,
        certificate_json=cert_json,
        reason=reason,
    )

