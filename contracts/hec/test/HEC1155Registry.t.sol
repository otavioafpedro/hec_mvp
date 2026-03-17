// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "forge-std/Test.sol";
import "../src/HEC1155Registry.sol";
import "../src/HECRetirementReceipt.sol";

contract HEC1155RegistryTest is Test {
    HEC1155Registry registry;
    HECRetirementReceipt receipt;

    address admin = address(0xA11CE);
    address treasury = address(0xBEEF);
    address alice = address(0x1111);
    address bob = address(0x2222);

    bytes32 batchHash = keccak256("batch-001");

    event BatchIssued(
        uint256 indexed tokenId,
        bytes32 indexed batchHash,
        address indexed treasury,
        uint256 totalIssuedUnits,
        string canonicalCID,
        string methodologyVersion,
        string schemaVersion
    );

    function setUp() public {
        vm.startPrank(admin);
        registry = new HEC1155Registry(admin, treasury, "ipfs://base/");
        receipt = new HECRetirementReceipt(admin);
        registry.setReceiptContract(address(receipt));
        receipt.grantRole(receipt.MINTER_ROLE(), address(registry));
        vm.stopPrank();
    }

    function _issueDefaultBatch() internal returns (uint256 tokenId) {
        vm.prank(admin);
        tokenId = registry.issueBatch(
            batchHash,
            "ipfs://canonical-batch-001",
            1700000000,
            1700086400,
            100000,
            "METH-1.0",
            "SCHEMA-1.0"
        );
    }

    function testIssueBatchToTreasury() public {
        uint256 tokenId = _issueDefaultBatch();
        assertEq(tokenId, 1);
        assertEq(registry.balanceOf(treasury, tokenId), 100000);
    }

    function testIssueBatchEmitsEvent() public {
        vm.prank(admin);
        vm.expectEmit(true, true, true, true);
        emit BatchIssued(
            1,
            batchHash,
            treasury,
            100000,
            "ipfs://canonical-batch-001",
            "METH-1.0",
            "SCHEMA-1.0"
        );
        registry.issueBatch(
            batchHash,
            "ipfs://canonical-batch-001",
            1700000000,
            1700086400,
            100000,
            "METH-1.0",
            "SCHEMA-1.0"
        );
    }

    function testRejectDuplicateBatchHash() public {
        _issueDefaultBatch();
        vm.prank(admin);
        vm.expectRevert(abi.encodeWithSelector(HEC1155Registry.BatchAlreadyRegistered.selector, batchHash));
        registry.issueBatch(
            batchHash,
            "ipfs://canonical-batch-002",
            1700000000,
            1700086400,
            50000,
            "METH-1.0",
            "SCHEMA-1.0"
        );
    }

    function testInventoryIsNonTransferable() public {
        uint256 tokenId = _issueDefaultBatch();
        vm.prank(treasury);
        vm.expectRevert(HEC1155Registry.NonTransferableInventory.selector);
        registry.safeTransferFrom(treasury, alice, tokenId, 1000, "");
    }

    function testRetireBurnsTreasuryInventoryAndMintsReceipt() public {
        uint256 tokenId = _issueDefaultBatch();

        vm.prank(admin);
        uint256 retirementId = registry.retire(
            tokenId,
            1000,
            alice,
            "RET-001",
            "beneficiary-hash",
            "Environmental claim"
        );

        assertEq(retirementId, 1);
        assertEq(registry.balanceOf(treasury, tokenId), 99000);
        assertEq(receipt.ownerOf(1), alice);
        assertTrue(receipt.locked(1));
    }

    function testRetirementUpdatesSupply() public {
        uint256 tokenId = _issueDefaultBatch();

        vm.prank(admin);
        registry.retire(
            tokenId,
            1000,
            alice,
            "RET-001",
            "beneficiary-hash",
            "Environmental claim"
        );

        uint256 circulating = registry.circulatingSupply(tokenId);
        assertEq(circulating, 99000);
    }

    function testOnlyRetirementRoleCanRetire() public {
        uint256 tokenId = _issueDefaultBatch();

        vm.prank(alice);
        vm.expectRevert();
        registry.retire(
            tokenId,
            1000,
            alice,
            "RET-001",
            "beneficiary-hash",
            "Environmental claim"
        );
    }

    function testBlockRetireWhenSuspended() public {
        uint256 tokenId = _issueDefaultBatch();

        vm.prank(admin);
        registry.setBatchStatus(tokenId, HEC1155Registry.BatchStatus.SUSPENDED, "audit pending");

        vm.prank(admin);
        vm.expectRevert(HEC1155Registry.BatchNotActive.selector);
        registry.retire(
            tokenId,
            1000,
            alice,
            "RET-001",
            "beneficiary-hash",
            "Environmental claim"
        );
    }

    function testOnlyStatusManagerCanChangeStatus() public {
        uint256 tokenId = _issueDefaultBatch();
        vm.prank(alice);
        vm.expectRevert();
        registry.setBatchStatus(tokenId, HEC1155Registry.BatchStatus.SUSPENDED, "unauthorized");
    }

    function testPauseBlocksIssuance() public {
        vm.prank(admin);
        registry.pause();

        vm.prank(admin);
        vm.expectRevert();
        registry.issueBatch(
            batchHash,
            "ipfs://canonical-batch-001",
            1700000000,
            1700086400,
            100000,
            "METH-1.0",
            "SCHEMA-1.0"
        );
    }

    function testPauseBlocksRetirement() public {
        uint256 tokenId = _issueDefaultBatch();

        vm.prank(admin);
        registry.pause();

        vm.prank(admin);
        vm.expectRevert();
        registry.retire(
            tokenId,
            1000,
            alice,
            "RET-001",
            "beneficiary-hash",
            "Environmental claim"
        );
    }

    function testReceiptIsNonTransferable() public {
        uint256 tokenId = _issueDefaultBatch();

        vm.prank(admin);
        registry.retire(
            tokenId,
            1000,
            alice,
            "RET-001",
            "beneficiary-hash",
            "Environmental claim"
        );

        vm.prank(alice);
        vm.expectRevert(HECRetirementReceipt.NonTransferable.selector);
        receipt.transferFrom(alice, bob, 1);
    }
}
