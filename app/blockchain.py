"""
Serviço Blockchain — Registro on-chain de certificados HEC

Registra o SHA-256 hash + IPFS CID do certificado no contrato
HECRegistry.sol (Polygon PoS).

Providers (plugável — mesmo padrão satellite/ipfs):
  - MockBlockchainProvider: simula blockchain em memória (dev/test)
  - PolygonProvider: Web3.py + Polygon Amoy/mainnet (produção) [stub]

Fluxo:
  1. Backend emite HEC (JSON + PDF + SHA-256 + IPFS CIDs)
  2. register_on_chain() chama HECRegistry.register(hash, ipfsCID)
  3. Contrato valida unicidade e emite CertificateRegistered event
  4. Backend salva tx_hash + block_number em hec_certificates
  5. Status atualizado para "registered"

Critério de backing completo:
  HEC só é considerado com lastro completo se registry_tx_hash existir.

Campos persistidos:
  - registry_tx_hash: hash da transação no Polygon
  - registry_block: número do bloco
  - contract_address: endereço do contrato HECRegistry
  - registered_at: timestamp do registro on-chain
"""
import hashlib
import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, Dict


# ---------------------------------------------------------------------------
# Resultados
# ---------------------------------------------------------------------------

@dataclass
class RegistrationResult:
    """Resultado do registro on-chain."""
    tx_hash: str                # Hash da transação (0x...)
    block_number: int           # Número do bloco
    contract_address: str       # Endereço do contrato
    chain: str                  # "polygon-amoy" | "polygon-mainnet"
    certificate_hash: str       # SHA-256 registrado (hex, sem 0x)
    ipfs_cid: str               # CID registrado
    gas_used: int               # Gas consumido
    registered_at: datetime     # Timestamp do registro
    provider: str               # "mock" | "polygon"


@dataclass
class OnChainVerifyResult:
    """Resultado da verificação on-chain."""
    exists: bool                # True se hash está registrado no contrato
    certificate_hash: str       # Hash consultado
    ipfs_cid: str               # CID armazenado no contrato
    registered_at: int          # Timestamp do bloco (unix)
    block_number: int           # Bloco do registro
    contract_address: str       # Endereço do contrato
    chain: str                  # Chain consultada
    provider: str               # Provider usado


# ---------------------------------------------------------------------------
# Provider ABC
# ---------------------------------------------------------------------------

class BlockchainProvider(ABC):
    """Interface para providers blockchain."""

    @abstractmethod
    def register(self, certificate_hash_hex: str, ipfs_cid: str) -> RegistrationResult:
        """
        Registra hash + CID no contrato on-chain.

        Args:
            certificate_hash_hex: SHA-256 do certificado (64 hex chars, sem 0x)
            ipfs_cid: CID do JSON no IPFS

        Returns:
            RegistrationResult com tx_hash, block, etc.
        """
        ...

    @abstractmethod
    def verify(self, certificate_hash_hex: str) -> OnChainVerifyResult:
        """
        Verifica se hash está registrado on-chain.

        Args:
            certificate_hash_hex: SHA-256 (64 hex chars, sem 0x)

        Returns:
            OnChainVerifyResult
        """
        ...

    @property
    @abstractmethod
    def contract_address(self) -> str:
        ...

    @property
    @abstractmethod
    def chain(self) -> str:
        ...

    @property
    @abstractmethod
    def name(self) -> str:
        ...


# ---------------------------------------------------------------------------
# Mock Provider (dev/test)
# ---------------------------------------------------------------------------

class MockBlockchainProvider(BlockchainProvider):
    """
    Provider blockchain mock — simula registro on-chain em memória.

    Gera tx_hash determinísticos baseados no certificate_hash.
    Simula block numbers incrementais.
    Mantém registry em dict para verificação.
    """

    def __init__(
        self,
        contract_addr: str = "0x1234567890abcdef1234567890abcdef12345678",
        chain_name: str = "polygon-amoy",
    ):
        self._contract_addr = contract_addr
        self._chain_name = chain_name
        self._registry: Dict[str, dict] = {}
        self._block_number = 50_000_000  # Starting block (realistic Polygon range)
        self._gas_base = 65_000

    def register(self, certificate_hash_hex: str, ipfs_cid: str) -> RegistrationResult:
        # Validate inputs (mirrors Solidity requires)
        if not certificate_hash_hex or certificate_hash_hex == "0" * 64:
            raise ValueError("HECRegistry: hash cannot be zero")
        if not ipfs_cid:
            raise ValueError("HECRegistry: IPFS CID cannot be empty")
        if certificate_hash_hex in self._registry:
            raise ValueError(
                f"HECRegistry: hash already registered "
                f"(tx: {self._registry[certificate_hash_hex]['tx_hash']})"
            )

        self._block_number += 1
        now = datetime.now(timezone.utc)

        # Generate deterministic tx_hash from certificate_hash
        tx_raw = hashlib.sha256(
            f"tx:{certificate_hash_hex}:{self._block_number}".encode()
        ).hexdigest()
        tx_hash = f"0x{tx_raw}"

        record = {
            "certificate_hash": certificate_hash_hex,
            "ipfs_cid": ipfs_cid,
            "tx_hash": tx_hash,
            "block_number": self._block_number,
            "registered_at": int(now.timestamp()),
            "gas_used": self._gas_base,
        }
        self._registry[certificate_hash_hex] = record

        return RegistrationResult(
            tx_hash=tx_hash,
            block_number=self._block_number,
            contract_address=self._contract_addr,
            chain=self._chain_name,
            certificate_hash=certificate_hash_hex,
            ipfs_cid=ipfs_cid,
            gas_used=self._gas_base,
            registered_at=now,
            provider=self.name,
        )

    def verify(self, certificate_hash_hex: str) -> OnChainVerifyResult:
        record = self._registry.get(certificate_hash_hex)

        if record:
            return OnChainVerifyResult(
                exists=True,
                certificate_hash=certificate_hash_hex,
                ipfs_cid=record["ipfs_cid"],
                registered_at=record["registered_at"],
                block_number=record["block_number"],
                contract_address=self._contract_addr,
                chain=self._chain_name,
                provider=self.name,
            )
        else:
            return OnChainVerifyResult(
                exists=False,
                certificate_hash=certificate_hash_hex,
                ipfs_cid="",
                registered_at=0,
                block_number=0,
                contract_address=self._contract_addr,
                chain=self._chain_name,
                provider=self.name,
            )

    @property
    def contract_address(self) -> str:
        return self._contract_addr

    @property
    def chain(self) -> str:
        return self._chain_name

    @property
    def name(self) -> str:
        return "mock"

    @property
    def total_registered(self) -> int:
        return len(self._registry)

    def clear(self):
        self._registry.clear()


# ---------------------------------------------------------------------------
# Polygon Provider (production stub)
# ---------------------------------------------------------------------------

class PolygonProvider(BlockchainProvider):
    """
    Provider Polygon — stub para produção.

    Em produção real usa Web3.py:
      - RPC: https://rpc-amoy.polygon.technology (testnet)
      - RPC: https://polygon-rpc.com (mainnet)
      - Signer: private key do deployer
      - ABI: HECRegistry compiled ABI

    Deploy:
      1. Compile HECRegistry.sol com solc ou hardhat
      2. Deploy via hardhat/foundry para Polygon Amoy
      3. Configure contract_address e private_key
      4. Backend chama register() via Web3.py
    """

    def __init__(
        self,
        rpc_url: str = "https://rpc-amoy.polygon.technology",
        contract_address: str = "",
        private_key: str = "",
        chain_name: str = "polygon-amoy",
    ):
        self._rpc_url = rpc_url
        self._contract_addr = contract_address
        self._private_key = private_key
        self._chain_name = chain_name

    def register(self, certificate_hash_hex: str, ipfs_cid: str) -> RegistrationResult:
        """
        Production: Web3.py call to HECRegistry.register()

        Steps:
          1. Connect to Polygon RPC
          2. Load contract ABI + address
          3. Build transaction: register(bytes32(hash), ipfsCID)
          4. Sign with deployer private key
          5. Send transaction
          6. Wait for receipt
          7. Return tx_hash + block_number
        """
        # TODO: implement with Web3.py
        # from web3 import Web3
        # w3 = Web3(Web3.HTTPProvider(self._rpc_url))
        # contract = w3.eth.contract(address=self._contract_addr, abi=ABI)
        # hash_bytes = bytes.fromhex(certificate_hash_hex)
        # tx = contract.functions.register(hash_bytes, ipfs_cid).build_transaction({...})
        # signed = w3.eth.account.sign_transaction(tx, self._private_key)
        # tx_hash = w3.eth.send_raw_transaction(signed.rawTransaction)
        # receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
        raise NotImplementedError(
            "PolygonProvider não implementado — use MockBlockchainProvider para dev/test. "
            "Para produção, configure RPC_URL, CONTRACT_ADDRESS e PRIVATE_KEY."
        )

    def verify(self, certificate_hash_hex: str) -> OnChainVerifyResult:
        """
        Production: Web3.py call to HECRegistry.verify()
        """
        raise NotImplementedError("PolygonProvider verify não implementado")

    @property
    def contract_address(self) -> str:
        return self._contract_addr

    @property
    def chain(self) -> str:
        return self._chain_name

    @property
    def name(self) -> str:
        return "polygon"


# ---------------------------------------------------------------------------
# Singleton provider (injetável para testes)
# ---------------------------------------------------------------------------

_blockchain_provider: BlockchainProvider = MockBlockchainProvider()


def get_blockchain_provider() -> BlockchainProvider:
    return _blockchain_provider


def set_blockchain_provider(provider: BlockchainProvider) -> None:
    global _blockchain_provider
    _blockchain_provider = provider


def reset_blockchain_provider() -> None:
    global _blockchain_provider
    _blockchain_provider = MockBlockchainProvider()


# ---------------------------------------------------------------------------
# Register on-chain
# ---------------------------------------------------------------------------

def register_on_chain(
    certificate_hash_hex: str,
    ipfs_cid: str,
    provider: Optional[BlockchainProvider] = None,
) -> RegistrationResult:
    """
    Registra o certificado HEC no contrato on-chain.

    Args:
        certificate_hash_hex: SHA-256 do certificado (64 hex chars)
        ipfs_cid: CID do JSON no IPFS
        provider: Provider blockchain (default: singleton)

    Returns:
        RegistrationResult com tx_hash, block_number, etc.
    """
    prov = provider or get_blockchain_provider()
    return prov.register(certificate_hash_hex, ipfs_cid)


def verify_on_chain(
    certificate_hash_hex: str,
    provider: Optional[BlockchainProvider] = None,
) -> OnChainVerifyResult:
    """
    Verifica se o certificado está registrado on-chain.

    Args:
        certificate_hash_hex: SHA-256 do certificado (64 hex chars)
        provider: Provider blockchain (default: singleton)

    Returns:
        OnChainVerifyResult
    """
    prov = provider or get_blockchain_provider()
    return prov.verify(certificate_hash_hex)
