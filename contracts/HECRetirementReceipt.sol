// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import {AccessControl} from "@openzeppelin/contracts/access/AccessControl.sol";
import {ERC721URIStorage} from "@openzeppelin/contracts/token/ERC721/extensions/ERC721URIStorage.sol";
import {ERC721} from "@openzeppelin/contracts/token/ERC721/ERC721.sol";

/**
 * @title HECRetirementReceipt
 * @notice Recibo soulbound (intransferível) emitido quando HECs são aposentados.
 * @dev Serve como prova permanente de claim/consumo ambiental.
 */
contract HECRetirementReceipt is ERC721URIStorage, AccessControl {
    bytes32 public constant MINTER_ROLE = keccak256("MINTER_ROLE");
    uint256 private _nextId = 1;

    error NonTransferable();

    struct ReceiptData {
        uint256 receiptId;
        uint256 batchTokenId;
        uint256 amountUnits;
        uint64 retiredAt;
        string retirementReference;
        string beneficiaryName;
        string purpose;
    }

    mapping(uint256 => ReceiptData) public receipts;

    constructor(address admin) ERC721("HEC Retirement Receipt", "HEC-R") {
        _grantRole(DEFAULT_ADMIN_ROLE, admin);
        _grantRole(MINTER_ROLE, admin);
    }

    function mintReceipt(
        address to,
        uint256 batchTokenId,
        uint256 amountUnits,
        string calldata retirementReference,
        string calldata beneficiaryName,
        string calldata purpose
    ) external onlyRole(MINTER_ROLE) returns (uint256 receiptId) {
        receiptId = _nextId++;
        _safeMint(to, receiptId);

        receipts[receiptId] = ReceiptData({
            receiptId: receiptId,
            batchTokenId: batchTokenId,
            amountUnits: amountUnits,
            retiredAt: uint64(block.timestamp),
            retirementReference: retirementReference,
            beneficiaryName: beneficiaryName,
            purpose: purpose
        });
    }

    function _update(
        address to,
        uint256 tokenId,
        address auth
    ) internal override returns (address) {
        address from = _ownerOf(tokenId);
        // Permitir minting (from == 0), mas bloquear transferências (from != 0)
        if (from != address(0) && to != address(0)) revert NonTransferable();
        return super._update(to, tokenId, auth);
    }

    function supportsInterface(bytes4 interfaceId)
        public
        view
        override(AccessControl, ERC721URIStorage)
        returns (bool)
    {
        return super.supportsInterface(interfaceId);
    }
}
