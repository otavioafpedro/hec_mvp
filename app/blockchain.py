"""
Blockchain helpers for certificate registration and custody inventory flows.

The original MVP only anchored individual HEC certificate hashes on-chain.
This module now also exposes lot-level inventory issuance and retirement hooks
used by the custody ledger.
"""
import hashlib
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, Optional


UNIT_SCALE = 1000


@dataclass
class RegistrationResult:
    tx_hash: str
    block_number: int
    contract_address: str
    chain: str
    certificate_hash: str
    ipfs_cid: str
    gas_used: int
    registered_at: datetime
    provider: str


@dataclass
class OnChainVerifyResult:
    exists: bool
    certificate_hash: str
    ipfs_cid: str
    registered_at: int
    block_number: int
    contract_address: str
    chain: str
    provider: str


@dataclass
class InventoryBatchIssuanceResult:
    tx_hash: str
    block_number: int
    contract_address: str
    chain: str
    batch_hash: str
    batch_token_id: int
    total_units: int
    manifest_cid: str
    period_start: int
    period_end: int
    methodology_version: str
    schema_version: str
    issued_at: datetime
    provider: str


@dataclass
class RetirementExecutionResult:
    tx_hash: str
    block_number: int
    contract_address: str
    chain: str
    batch_token_id: int
    retirement_id: int
    receipt_token_id: int
    amount_units: int
    claimant_wallet: Optional[str]
    retirement_reference: str
    beneficiary_ref_hash: Optional[str]
    purpose: str
    retired_at: datetime
    provider: str


class BlockchainProvider(ABC):
    @abstractmethod
    def register(self, certificate_hash_hex: str, ipfs_cid: str) -> RegistrationResult:
        ...

    @abstractmethod
    def verify(self, certificate_hash_hex: str) -> OnChainVerifyResult:
        ...

    @abstractmethod
    def issue_inventory_batch(
        self,
        batch_hash_hex: str,
        manifest_cid: str,
        period_start: int,
        period_end: int,
        total_units: int,
        methodology_version: str,
        schema_version: str,
    ) -> InventoryBatchIssuanceResult:
        ...

    @abstractmethod
    def retire_inventory_batch(
        self,
        batch_token_id: int,
        amount_units: int,
        claimant_wallet: Optional[str],
        retirement_reference: str,
        beneficiary_ref_hash: Optional[str],
        purpose: str,
    ) -> RetirementExecutionResult:
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


class MockBlockchainProvider(BlockchainProvider):
    def __init__(
        self,
        contract_addr: str = "0x1234567890abcdef1234567890abcdef12345678",
        chain_name: str = "polygon-amoy",
    ):
        self._contract_addr = contract_addr
        self._chain_name = chain_name
        self._certificate_registry: Dict[str, dict] = {}
        self._inventory_by_hash: Dict[str, int] = {}
        self._inventory_batches: Dict[int, dict] = {}
        self._retirements: Dict[int, dict] = {}
        self._block_number = 50000000
        self._gas_base = 65000
        self._next_batch_id = 1
        self._next_retirement_id = 1
        self._next_receipt_id = 1

    def _next_tx_hash(self, namespace: str, seed: str) -> str:
        tx_raw = hashlib.sha256(
            f"{namespace}:{seed}:{self._block_number}".encode("utf-8")
        ).hexdigest()
        return f"0x{tx_raw}"

    def register(self, certificate_hash_hex: str, ipfs_cid: str) -> RegistrationResult:
        if not certificate_hash_hex or certificate_hash_hex == "0" * 64:
            raise ValueError("HECRegistry: hash cannot be zero")
        if not ipfs_cid:
            raise ValueError("HECRegistry: IPFS CID cannot be empty")
        if certificate_hash_hex in self._certificate_registry:
            raise ValueError(
                f"HECRegistry: hash already registered "
                f"(tx: {self._certificate_registry[certificate_hash_hex]['tx_hash']})"
            )

        self._block_number += 1
        now = datetime.now(timezone.utc)
        tx_hash = self._next_tx_hash("register", certificate_hash_hex)
        record = {
            "certificate_hash": certificate_hash_hex,
            "ipfs_cid": ipfs_cid,
            "tx_hash": tx_hash,
            "block_number": self._block_number,
            "registered_at": int(now.timestamp()),
            "gas_used": self._gas_base,
        }
        self._certificate_registry[certificate_hash_hex] = record

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
        record = self._certificate_registry.get(certificate_hash_hex)
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

    def issue_inventory_batch(
        self,
        batch_hash_hex: str,
        manifest_cid: str,
        period_start: int,
        period_end: int,
        total_units: int,
        methodology_version: str,
        schema_version: str,
    ) -> InventoryBatchIssuanceResult:
        if not batch_hash_hex or batch_hash_hex == "0" * 64:
            raise ValueError("HECInventory: batch hash cannot be zero")
        if batch_hash_hex in self._inventory_by_hash:
            raise ValueError("HECInventory: batch hash already issued")
        if not manifest_cid:
            raise ValueError("HECInventory: manifest CID cannot be empty")
        if total_units <= 0:
            raise ValueError("HECInventory: total units must be > 0")
        if period_end < period_start:
            raise ValueError("HECInventory: invalid reporting period")

        self._block_number += 1
        now = datetime.now(timezone.utc)
        batch_token_id = self._next_batch_id
        self._next_batch_id += 1
        tx_hash = self._next_tx_hash("issue_batch", batch_hash_hex)

        self._inventory_by_hash[batch_hash_hex] = batch_token_id
        self._inventory_batches[batch_token_id] = {
            "batch_hash": batch_hash_hex,
            "manifest_cid": manifest_cid,
            "period_start": period_start,
            "period_end": period_end,
            "total_units": total_units,
            "retired_units": 0,
            "methodology_version": methodology_version,
            "schema_version": schema_version,
            "tx_hash": tx_hash,
            "block_number": self._block_number,
            "issued_at": int(now.timestamp()),
            "status": "issued",
        }

        return InventoryBatchIssuanceResult(
            tx_hash=tx_hash,
            block_number=self._block_number,
            contract_address=self._contract_addr,
            chain=self._chain_name,
            batch_hash=batch_hash_hex,
            batch_token_id=batch_token_id,
            total_units=total_units,
            manifest_cid=manifest_cid,
            period_start=period_start,
            period_end=period_end,
            methodology_version=methodology_version,
            schema_version=schema_version,
            issued_at=now,
            provider=self.name,
        )

    def retire_inventory_batch(
        self,
        batch_token_id: int,
        amount_units: int,
        claimant_wallet: Optional[str],
        retirement_reference: str,
        beneficiary_ref_hash: Optional[str],
        purpose: str,
    ) -> RetirementExecutionResult:
        batch = self._inventory_batches.get(batch_token_id)
        if not batch:
            raise ValueError("HECInventory: batch not found")
        if amount_units <= 0:
            raise ValueError("HECInventory: retirement amount must be > 0")
        if batch["retired_units"] + amount_units > batch["total_units"]:
            raise ValueError("HECInventory: insufficient unretired units")

        self._block_number += 1
        now = datetime.now(timezone.utc)
        retirement_id = self._next_retirement_id
        receipt_token_id = self._next_receipt_id
        self._next_retirement_id += 1
        self._next_receipt_id += 1
        tx_hash = self._next_tx_hash(
            "retire_batch",
            f"{batch_token_id}:{amount_units}:{retirement_reference}",
        )

        batch["retired_units"] += amount_units
        self._retirements[retirement_id] = {
            "batch_token_id": batch_token_id,
            "amount_units": amount_units,
            "claimant_wallet": claimant_wallet,
            "retirement_reference": retirement_reference,
            "beneficiary_ref_hash": beneficiary_ref_hash,
            "purpose": purpose,
            "receipt_token_id": receipt_token_id,
            "tx_hash": tx_hash,
            "block_number": self._block_number,
            "retired_at": int(now.timestamp()),
        }

        return RetirementExecutionResult(
            tx_hash=tx_hash,
            block_number=self._block_number,
            contract_address=self._contract_addr,
            chain=self._chain_name,
            batch_token_id=batch_token_id,
            retirement_id=retirement_id,
            receipt_token_id=receipt_token_id,
            amount_units=amount_units,
            claimant_wallet=claimant_wallet,
            retirement_reference=retirement_reference,
            beneficiary_ref_hash=beneficiary_ref_hash,
            purpose=purpose,
            retired_at=now,
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
        return len(self._certificate_registry)

    def clear(self) -> None:
        self._certificate_registry.clear()
        self._inventory_by_hash.clear()
        self._inventory_batches.clear()
        self._retirements.clear()
        self._next_batch_id = 1
        self._next_retirement_id = 1
        self._next_receipt_id = 1


class PolygonProvider(BlockchainProvider):
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
        raise NotImplementedError(
            "PolygonProvider.register is not implemented. Use MockBlockchainProvider for dev/test."
        )

    def verify(self, certificate_hash_hex: str) -> OnChainVerifyResult:
        raise NotImplementedError("PolygonProvider.verify is not implemented.")

    def issue_inventory_batch(
        self,
        batch_hash_hex: str,
        manifest_cid: str,
        period_start: int,
        period_end: int,
        total_units: int,
        methodology_version: str,
        schema_version: str,
    ) -> InventoryBatchIssuanceResult:
        raise NotImplementedError("PolygonProvider.issue_inventory_batch is not implemented.")

    def retire_inventory_batch(
        self,
        batch_token_id: int,
        amount_units: int,
        claimant_wallet: Optional[str],
        retirement_reference: str,
        beneficiary_ref_hash: Optional[str],
        purpose: str,
    ) -> RetirementExecutionResult:
        raise NotImplementedError("PolygonProvider.retire_inventory_batch is not implemented.")

    @property
    def contract_address(self) -> str:
        return self._contract_addr

    @property
    def chain(self) -> str:
        return self._chain_name

    @property
    def name(self) -> str:
        return "polygon"


_blockchain_provider: BlockchainProvider = MockBlockchainProvider()


def get_blockchain_provider() -> BlockchainProvider:
    return _blockchain_provider


def set_blockchain_provider(provider: BlockchainProvider) -> None:
    global _blockchain_provider
    _blockchain_provider = provider


def reset_blockchain_provider() -> None:
    global _blockchain_provider
    _blockchain_provider = MockBlockchainProvider()


def register_on_chain(
    certificate_hash_hex: str,
    ipfs_cid: str,
    provider: Optional[BlockchainProvider] = None,
) -> RegistrationResult:
    prov = provider or get_blockchain_provider()
    return prov.register(certificate_hash_hex, ipfs_cid)


def verify_on_chain(
    certificate_hash_hex: str,
    provider: Optional[BlockchainProvider] = None,
) -> OnChainVerifyResult:
    prov = provider or get_blockchain_provider()
    return prov.verify(certificate_hash_hex)


def issue_inventory_batch_on_chain(
    batch_hash_hex: str,
    manifest_cid: str,
    period_start: int,
    period_end: int,
    total_units: int,
    methodology_version: str,
    schema_version: str,
    provider: Optional[BlockchainProvider] = None,
) -> InventoryBatchIssuanceResult:
    prov = provider or get_blockchain_provider()
    return prov.issue_inventory_batch(
        batch_hash_hex=batch_hash_hex,
        manifest_cid=manifest_cid,
        period_start=period_start,
        period_end=period_end,
        total_units=total_units,
        methodology_version=methodology_version,
        schema_version=schema_version,
    )


def retire_inventory_batch_on_chain(
    batch_token_id: int,
    amount_units: int,
    claimant_wallet: Optional[str],
    retirement_reference: str,
    beneficiary_ref_hash: Optional[str],
    purpose: str,
    provider: Optional[BlockchainProvider] = None,
) -> RetirementExecutionResult:
    prov = provider or get_blockchain_provider()
    return prov.retire_inventory_batch(
        batch_token_id=batch_token_id,
        amount_units=amount_units,
        claimant_wallet=claimant_wallet,
        retirement_reference=retirement_reference,
        beneficiary_ref_hash=beneficiary_ref_hash,
        purpose=purpose,
    )
