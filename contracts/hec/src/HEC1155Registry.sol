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
        string calldata beneficiaryName,
        string calldata purpose
    ) external returns (uint256);
}

/**
 * @title HEC1155Registry
 * @notice Registro on-chain de lotes de certificados HEC (Hydroelectric Energy Certificate)
 * @dev Representa HECs ativos, fracionáveis e transferíveis. 
 *      Permite aposentadoria parcial, que queima os tokens ativos e emite um recibo soulbound.
 */
contract HEC1155Registry is ERC1155, AccessControl, Pausable {
    bytes32 public constant REGISTRAR_ROLE = keccak256("REGISTRAR_ROLE");
    bytes32 public constant PAUSER_ROLE = keccak256("PAUSER_ROLE");
    bytes32 public constant STATUS_MANAGER_ROLE = keccak256("STATUS_MANAGER_ROLE");
    bytes32 public constant RETIREMENT_ROLE = keccak256("RETIREMENT_ROLE");

    uint256 public constant UNIT_SCALE = 1000; // 1 HEC = 1000 units => 0.001 HEC min
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
        BatchStatus status;
        uint64 statusChangedAt;
        string methodologyVersion;
        string schemaVersion;
        string statusReason;
    }

    struct RetirementRecord {
        uint256 retirementId;
        uint256 batchTokenId;
        address retiredBy;
        uint256 amountUnits;
        uint64 retiredAt;
        string retirementReference;
        string beneficiaryName;
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
    error NonTransferableStatus(BatchStatus status);

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
        address indexed retiredBy,
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
        if (batchHash == bytes32(0)) revert ZeroHash();
        if (bytes(canonicalCID).length == 0) revert EmptyString();
        if (bytes(methodologyVersion).length == 0) revert EmptyString();
        if (bytes(schemaVersion).length == 0) revert EmptyString();
        if (totalIssuedUnits == 0) revert InvalidAmount();
        if (periodEnd < periodStart) revert InvalidPeriod();
        if (batchIdByHash[batchHash] != 0) revert BatchAlreadyRegistered(batchHash);

        tokenId = _nextBatchId++;
        batchIdByHash[batchHash] = tokenId;

        batches[tokenId] = HECBatch({
            tokenId: tokenId,
            batchHash: batchHash,
            canonicalCID: canonicalCID,
            issuedAt: uint64(block.timestamp),
            periodStart: periodStart,
            periodEnd: periodEnd,
            totalIssuedUnits: totalIssuedUnits,
            totalRetiredUnits: 0,
            issuer: msg.sender,
            status: BatchStatus.ACTIVE,
            statusChangedAt: uint64(block.timestamp),
            methodologyVersion: methodologyVersion,
            schemaVersion: schemaVersion,
            statusReason: "INITIAL_ISSUANCE"
        });

        _mint(treasuryAddress, tokenId, totalIssuedUnits, "");

        emit BatchIssued(
            tokenId,
            batchHash,
            treasuryAddress,
            totalIssuedUnits,
            canonicalCID,
            methodologyVersion,
            schemaVersion
        );
    }

    function retire(
        uint256 batchTokenId,
        uint256 amountUnits,
        string calldata retirementReference,
        string calldata beneficiaryName,
        string calldata purpose
    ) external whenNotPaused returns (uint256 retirementId) {
        HECBatch storage batch = batches[batchTokenId];
        if (batch.tokenId == 0) revert BatchNotFound();
        if (batch.status != BatchStatus.ACTIVE) revert BatchNotActive();
        if (amountUnits == 0) revert InvalidAmount();

        _burn(msg.sender, batchTokenId, amountUnits);
        batch.totalRetiredUnits += amountUnits;

        uint256 receiptTokenId = 0;
        if (address(receiptContract) != address(0)) {
            receiptTokenId = receiptContract.mintReceipt(
                msg.sender,
                batchTokenId,
                amountUnits,
                retirementReference,
                beneficiaryName,
                purpose
            );
        }

        retirementId = nextRetirementId++;
        retirements[retirementId] = RetirementRecord({
            retirementId: retirementId,
            batchTokenId: batchTokenId,
            retiredBy: msg.sender,
            amountUnits: amountUnits,
            retiredAt: uint64(block.timestamp),
            retirementReference: retirementReference,
            beneficiaryName: beneficiaryName,
            purpose: purpose,
            receiptTokenId: receiptTokenId
        });

        emit Retired(
            retirementId,
            batchTokenId,
            msg.sender,
            amountUnits,
            retirementReference,
            receiptTokenId
        );
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

    function _update(
        address from,
        address to,
        uint256[] memory ids,
        uint256[] memory values
    ) internal override {
        if (paused()) revert EnforcedPause();

        if (from != address(0) && to != address(0)) {
            for (uint256 i = 0; i < ids.length; i++) {
                HECBatch storage batch = batches[ids[i]];
                if (batch.status != BatchStatus.ACTIVE) {
                    revert NonTransferableStatus(batch.status);
                }
            }
        }

        super._update(from, to, ids, values);
    }
    
    // The following functions are overrides required by Solidity.
    function supportsInterface(bytes4 interfaceId)
        public
        view
        override(ERC1155, AccessControl)
        returns (bool)
    {
        return super.supportsInterface(interfaceId);
    }
}
