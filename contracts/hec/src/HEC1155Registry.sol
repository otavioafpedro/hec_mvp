// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import {AccessControl} from "@openzeppelin/contracts/access/AccessControl.sol";
import {Pausable} from "@openzeppelin/contracts/utils/Pausable.sol";
import {ERC1155} from "@openzeppelin/contracts/token/ERC1155/ERC1155.sol";

interface IHECRetirementReceipt {
    function mintReceipt(
        address to,
        uint256 batchTokenId,
        uint256 amountUnits,
        string calldata retirementReference,
        string calldata beneficiaryRefHash,
        string calldata purpose
    ) external returns (uint256);
}

contract HEC1155Registry is ERC1155, AccessControl, Pausable {
    bytes32 public constant REGISTRAR_ROLE = keccak256("REGISTRAR_ROLE");
    bytes32 public constant PAUSER_ROLE = keccak256("PAUSER_ROLE");
    bytes32 public constant STATUS_MANAGER_ROLE = keccak256("STATUS_MANAGER_ROLE");
    bytes32 public constant RETIREMENT_ROLE = keccak256("RETIREMENT_ROLE");

    uint256 public constant UNIT_SCALE = 1000;
    uint256 private _nextBatchId = 1;

    enum BatchStatus {
        NONE,
        ACTIVE,
        SUSPENDED,
        REVOKED
    }

    struct HECBatch {
        uint256 tokenId;
        bytes32 batchHash;
        string canonicalCID;
        uint64 issuedAt;
        uint64 periodStart;
        uint64 periodEnd;
        uint256 totalIssuedUnits;
        uint256 totalRetiredUnits;
        address issuer;
        address custodyWallet;
        BatchStatus status;
        uint64 statusChangedAt;
        string methodologyVersion;
        string schemaVersion;
        string statusReason;
    }

    struct RetirementRecord {
        uint256 retirementId;
        uint256 batchTokenId;
        address protocolOperator;
        address claimantWallet;
        uint256 amountUnits;
        uint64 retiredAt;
        string retirementReference;
        string beneficiaryRefHash;
        string purpose;
        uint256 receiptTokenId;
    }

    error ZeroAddress();
    error ZeroHash();
    error EmptyString();
    error InvalidAmount();
    error InvalidPeriod();
    error BatchAlreadyRegistered(bytes32 batchHash);
    error BatchNotFound();
    error BatchNotActive();
    error NonTransferableInventory();

    event BatchIssued(
        uint256 indexed tokenId,
        bytes32 indexed batchHash,
        address indexed treasury,
        uint256 totalIssuedUnits,
        string canonicalCID,
        string methodologyVersion,
        string schemaVersion
    );

    event BatchStatusChanged(
        uint256 indexed tokenId,
        BatchStatus previousStatus,
        BatchStatus newStatus,
        string reason,
        address changedBy
    );

    event Retired(
        uint256 indexed retirementId,
        uint256 indexed batchTokenId,
        address indexed claimantWallet,
        uint256 amountUnits,
        string retirementReference,
        uint256 receiptTokenId
    );

    mapping(bytes32 => uint256) public batchIdByHash;
    mapping(uint256 => HECBatch) public batches;
    mapping(uint256 => RetirementRecord) public retirements;

    uint256 public nextRetirementId = 1;
    IHECRetirementReceipt public receiptContract;
    address public treasuryAddress;

    constructor(address admin, address treasury, string memory baseURI) ERC1155(baseURI) {
        if (admin == address(0) || treasury == address(0)) revert ZeroAddress();

        _grantRole(DEFAULT_ADMIN_ROLE, admin);
        _grantRole(REGISTRAR_ROLE, admin);
        _grantRole(PAUSER_ROLE, admin);
        _grantRole(STATUS_MANAGER_ROLE, admin);
        _grantRole(RETIREMENT_ROLE, admin);

        treasuryAddress = treasury;
    }

    function setTreasury(address newTreasury) external onlyRole(DEFAULT_ADMIN_ROLE) {
        if (newTreasury == address(0)) revert ZeroAddress();
        treasuryAddress = newTreasury;
    }

    function setReceiptContract(address contractAddress) external onlyRole(DEFAULT_ADMIN_ROLE) {
        if (contractAddress == address(0)) revert ZeroAddress();
        receiptContract = IHECRetirementReceipt(contractAddress);
    }

    function issueBatch(
        bytes32 batchHash,
        string calldata canonicalCID,
        uint64 periodStart,
        uint64 periodEnd,
        uint256 totalIssuedUnits,
        string calldata methodologyVersion,
        string calldata schemaVersion
    ) external onlyRole(REGISTRAR_ROLE) whenNotPaused returns (uint256 tokenId) {
        _validateIssue(batchHash, canonicalCID, periodStart, periodEnd, totalIssuedUnits, methodologyVersion, schemaVersion);

        tokenId = _nextBatchId++;
        batchIdByHash[batchHash] = tokenId;

        HECBatch storage batch = batches[tokenId];
        batch.tokenId = tokenId;
        batch.batchHash = batchHash;
        batch.canonicalCID = canonicalCID;
        batch.issuedAt = uint64(block.timestamp);
        batch.periodStart = periodStart;
        batch.periodEnd = periodEnd;
        batch.totalIssuedUnits = totalIssuedUnits;
        batch.totalRetiredUnits = 0;
        batch.issuer = msg.sender;
        batch.custodyWallet = treasuryAddress;
        batch.status = BatchStatus.ACTIVE;
        batch.statusChangedAt = uint64(block.timestamp);
        batch.methodologyVersion = methodologyVersion;
        batch.schemaVersion = schemaVersion;
        batch.statusReason = "INITIAL_ISSUANCE";

        _mint(batch.custodyWallet, tokenId, totalIssuedUnits, "");

        _emitBatchIssued(tokenId);
    }

    function retire(
        uint256 batchTokenId,
        uint256 amountUnits,
        address claimantWallet,
        string calldata retirementReference,
        string calldata beneficiaryRefHash,
        string calldata purpose
    ) external onlyRole(RETIREMENT_ROLE) whenNotPaused returns (uint256 retirementId) {
        HECBatch storage batch = batches[batchTokenId];
        if (batch.tokenId == 0) revert BatchNotFound();
        if (batch.status != BatchStatus.ACTIVE) revert BatchNotActive();
        if (amountUnits == 0) revert InvalidAmount();
        if (claimantWallet == address(0)) revert ZeroAddress();

        _burn(batch.custodyWallet, batchTokenId, amountUnits);
        batch.totalRetiredUnits += amountUnits;

        uint256 receiptTokenId = 0;
        if (address(receiptContract) != address(0)) {
            receiptTokenId = receiptContract.mintReceipt(
                claimantWallet,
                batchTokenId,
                amountUnits,
                retirementReference,
                beneficiaryRefHash,
                purpose
            );
        }

        retirementId = nextRetirementId++;
        RetirementRecord storage record = retirements[retirementId];
        record.retirementId = retirementId;
        record.batchTokenId = batchTokenId;
        record.protocolOperator = msg.sender;
        record.claimantWallet = claimantWallet;
        record.amountUnits = amountUnits;
        record.retiredAt = uint64(block.timestamp);
        record.retirementReference = retirementReference;
        record.beneficiaryRefHash = beneficiaryRefHash;
        record.purpose = purpose;
        record.receiptTokenId = receiptTokenId;

        _emitRetired(retirementId);
    }

    function setBatchStatus(
        uint256 batchTokenId,
        BatchStatus newStatus,
        string calldata reason
    ) external onlyRole(STATUS_MANAGER_ROLE) whenNotPaused {
        HECBatch storage batch = batches[batchTokenId];
        if (batch.tokenId == 0) revert BatchNotFound();
        if (bytes(reason).length == 0) revert EmptyString();
        require(newStatus != BatchStatus.NONE, "invalid status");

        BatchStatus previous = batch.status;
        batch.status = newStatus;
        batch.statusChangedAt = uint64(block.timestamp);
        batch.statusReason = reason;

        emit BatchStatusChanged(batchTokenId, previous, newStatus, reason, msg.sender);
    }

    function circulatingSupply(uint256 batchTokenId) external view returns (uint256) {
        HECBatch storage batch = batches[batchTokenId];
        if (batch.tokenId == 0) revert BatchNotFound();
        return batch.totalIssuedUnits - batch.totalRetiredUnits;
    }

    function pause() external onlyRole(PAUSER_ROLE) {
        _pause();
    }

    function unpause() external onlyRole(PAUSER_ROLE) {
        _unpause();
    }

    function _emitBatchIssued(uint256 tokenId) private {
        HECBatch storage batch = batches[tokenId];
        emit BatchIssued(
            tokenId,
            batch.batchHash,
            batch.custodyWallet,
            batch.totalIssuedUnits,
            batch.canonicalCID,
            batch.methodologyVersion,
            batch.schemaVersion
        );
    }

    function _emitRetired(uint256 retirementId) private {
        RetirementRecord storage record = retirements[retirementId];
        emit Retired(
            retirementId,
            record.batchTokenId,
            record.claimantWallet,
            record.amountUnits,
            record.retirementReference,
            record.receiptTokenId
        );
    }

    function _validateIssue(
        bytes32 batchHash,
        string calldata canonicalCID,
        uint64 periodStart,
        uint64 periodEnd,
        uint256 totalIssuedUnits,
        string calldata methodologyVersion,
        string calldata schemaVersion
    ) private view {
        if (batchHash == bytes32(0)) revert ZeroHash();
        if (bytes(canonicalCID).length == 0) revert EmptyString();
        if (bytes(methodologyVersion).length == 0) revert EmptyString();
        if (bytes(schemaVersion).length == 0) revert EmptyString();
        if (totalIssuedUnits == 0) revert InvalidAmount();
        if (periodEnd < periodStart) revert InvalidPeriod();
        if (batchIdByHash[batchHash] != 0) revert BatchAlreadyRegistered(batchHash);
    }

    function _update(
        address from,
        address to,
        uint256[] memory ids,
        uint256[] memory values
    ) internal override {
        if (paused()) revert EnforcedPause();
        if (from != address(0) && to != address(0)) {
            revert NonTransferableInventory();
        }
        super._update(from, to, ids, values);
    }

    function supportsInterface(bytes4 interfaceId)
        public
        view
        override(ERC1155, AccessControl)
        returns (bool)
    {
        return super.supportsInterface(interfaceId);
    }
}
