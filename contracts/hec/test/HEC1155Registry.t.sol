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

    function setUp() public {
        vm.startPrank(admin);

        registry = new HEC1155Registry(admin, treasury, "ipfs://base/");
        receipt = new HECRetirementReceipt(admin);

        registry.setReceiptContract(address(receipt));
        receipt.grantRole(receipt.MINTER_ROLE(), address(registry));

        vm.stopPrank();
    }

    // ═══════════════════════════════════════════════════════════════
    // ISSUANCE TESTS
    // ═══════════════════════════════════════════════════════════════

    function testIssueBatchToTreasury() public {
        vm.prank(admin);
        uint256 tokenId = registry.issueBatch(
            batchHash,
            "ipfs://canonical-batch-001",
            1700000000,
            1700086400,
            100000, // 100 HEC
            "METH-1.0",
            "SCHEMA-1.0"
        );

        assertEq(tokenId, 1);
        assertEq(registry.balanceOf(treasury, tokenId), 100000);
    }

    function testIssueBatchEmitsEvent() public {
        vm.prank(admin);
        vm.expectEmit(true, true, true, true);
        emit HEC1155Registry.BatchIssued(
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
        vm.startPrank(admin);
        registry.issueBatch(
            batchHash,
            "ipfs://canonical-batch-001",
            1700000000,
            1700086400,
            100000,
            "METH-1.0",
            "SCHEMA-1.0"
        );

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
        vm.stopPrank();
    }

    function testRejectInvalidPeriod() public {
        vm.prank(admin);
        vm.expectRevert(HEC1155Registry.InvalidPeriod.selector);
        registry.issueBatch(
            batchHash,
            "ipfs://canonical-batch-001",
            1700086400, // end before start
            1700000000,
            100000,
            "METH-1.0",
            "SCHEMA-1.0"
        );
    }

    function testRejectZeroAmount() public {
        vm.prank(admin);
        vm.expectRevert(HEC1155Registry.InvalidAmount.selector);
        registry.issueBatch(
            batchHash,
            "ipfs://canonical-batch-001",
            1700000000,
            1700086400,
            0,
            "METH-1.0",
            "SCHEMA-1.0"
        );
    }

    // ═══════════════════════════════════════════════════════════════
    // TRANSFER TESTS
    // ═══════════════════════════════════════════════════════════════

    function testTransferWhileActive() public {
        vm.prank(admin);
        uint256 tokenId = registry.issueBatch(
            batchHash,
            "ipfs://canonical-batch-001",
            1700000000,
            1700086400,
            100000,
            "METH-1.0",
            "SCHEMA-1.0"
        );

        vm.prank(treasury);
        registry.safeTransferFrom(treasury, alice, tokenId, 5000, "");

        assertEq(registry.balanceOf(alice, tokenId), 5000);

        vm.prank(alice);
        registry.safeTransferFrom(alice, bob, tokenId, 2000, "");

        assertEq(registry.balanceOf(alice, tokenId), 3000);
        assertEq(registry.balanceOf(bob, tokenId), 2000);
    }

    function testBlockTransferWhenSuspended() public {
        vm.prank(admin);
        uint256 tokenId = registry.issueBatch(
            batchHash,
            "ipfs://canonical-batch-001",
            1700000000,
            1700086400,
            100000,
            "METH-1.0",
            "SCHEMA-1.0"
        );

        vm.prank(treasury);
        registry.safeTransferFrom(treasury, alice, tokenId, 5000, "");

        vm.prank(admin);
        registry.setBatchStatus(tokenId, HEC1155Registry.BatchStatus.SUSPENDED, "audit pending");

        vm.prank(alice);
        vm.expectRevert();
        registry.safeTransferFrom(alice, bob, tokenId, 1000, "");
    }

    function testBlockTransferWhenRevoked() public {
        vm.prank(admin);
        uint256 tokenId = registry.issueBatch(
            batchHash,
            "ipfs://canonical-batch-001",
            1700000000,
            1700086400,
            100000,
            "METH-1.0",
            "SCHEMA-1.0"
        );

        vm.prank(treasury);
        registry.safeTransferFrom(treasury, alice, tokenId, 5000, "");

        vm.prank(admin);
        registry.setBatchStatus(tokenId, HEC1155Registry.BatchStatus.REVOKED, "fraud detected");

        vm.prank(alice);
        vm.expectRevert();
        registry.safeTransferFrom(alice, bob, tokenId, 1000, "");
    }

    // ═══════════════════════════════════════════════════════════════
    // RETIREMENT TESTS
    // ═══════════════════════════════════════════════════════════════

    function testRetireBurnsActiveBalanceAndMintsReceipt() public {
        vm.prank(admin);
        uint256 tokenId = registry.issueBatch(
            batchHash,
            "ipfs://canonical-batch-001",
            1700000000,
            1700086400,
            100000,
            "METH-1.0",
            "SCHEMA-1.0"
        );

        vm.prank(treasury);
        registry.safeTransferFrom(treasury, alice, tokenId, 5000, "");

        vm.prank(alice);
        uint256 retirementId = registry.retire(
            tokenId,
            1000, // 1 HEC
            "RET-001",
            "Alice Ltda",
            "Environmental claim"
        );

        assertEq(retirementId, 1);
        assertEq(registry.balanceOf(alice, tokenId), 4000);

        (
            uint256 receiptId,
            uint256 batchTokenId,
            uint256 amountUnits,
            ,
            ,
            ,

        ) = receipt.receipts(1);

        assertEq(receiptId, 1);
        assertEq(batchTokenId, tokenId);
        assertEq(amountUnits, 1000);
        assertEq(receipt.ownerOf(1), alice);
    }

    function testRetirementUpdatesSupply() public {
        vm.prank(admin);
        uint256 tokenId = registry.issueBatch(
            batchHash,
            "ipfs://canonical-batch-001",
            1700000000,
            1700086400,
            100000,
            "METH-1.0",
            "SCHEMA-1.0"
        );

        vm.prank(treasury);
        registry.safeTransferFrom(treasury, alice, tokenId, 5000, "");

        vm.prank(alice);
        registry.retire(
            tokenId,
            1000,
            "RET-001",
            "Alice Ltda",
            "Environmental claim"
        );

        uint256 circulating = registry.circulatingSupply(tokenId);
        assertEq(circulating, 99000); // 100000 - 1000
    }

    function testBlockRetireWhenSuspended() public {
        vm.prank(admin);
        uint256 tokenId = registry.issueBatch(
            batchHash,
            "ipfs://canonical-batch-001",
            1700000000,
            1700086400,
            100000,
            "METH-1.0",
            "SCHEMA-1.0"
        );

        vm.prank(treasury);
        registry.safeTransferFrom(treasury, alice, tokenId, 5000, "");

        vm.prank(admin);
        registry.setBatchStatus(tokenId, HEC1155Registry.BatchStatus.SUSPENDED, "audit pending");

        vm.prank(alice);
        vm.expectRevert(HEC1155Registry.BatchNotActive.selector);
        registry.retire(
            tokenId,
            1000,
            "RET-001",
            "Alice Ltda",
            "Environmental claim"
        );
    }

    function testBlockRetireMoreThanBalance() public {
        vm.prank(admin);
        uint256 tokenId = registry.issueBatch(
            batchHash,
            "ipfs://canonical-batch-001",
            1700000000,
            1700086400,
            100000,
            "METH-1.0",
            "SCHEMA-1.0"
        );

        vm.prank(treasury);
        registry.safeTransferFrom(treasury, alice, tokenId, 5000, "");

        vm.prank(alice);
        vm.expectRevert(); // ERC1155 burn will revert on insufficient balance
        registry.retire(
            tokenId,
            10000, // more than alice's 5000
            "RET-001",
            "Alice Ltda",
            "Environmental claim"
        );
    }

    // ═══════════════════════════════════════════════════════════════
    // GOVERNANCE TESTS
    // ═══════════════════════════════════════════════════════════════

    function testOnlyRegistrarCanIssue() public {
        vm.prank(alice);
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

    function testOnlyStatusManagerCanChangeStatus() public {
        vm.prank(admin);
        uint256 tokenId = registry.issueBatch(
            batchHash,
            "ipfs://canonical-batch-001",
            1700000000,
            1700086400,
            100000,
            "METH-1.0",
            "SCHEMA-1.0"
        );

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

    function testPauseBlocksTransfers() public {
        vm.prank(admin);
        uint256 tokenId = registry.issueBatch(
            batchHash,
            "ipfs://canonical-batch-001",
            1700000000,
            1700086400,
            100000,
            "METH-1.0",
            "SCHEMA-1.0"
        );

        vm.prank(treasury);
        registry.safeTransferFrom(treasury, alice, tokenId, 5000, "");

        vm.prank(admin);
        registry.pause();

        vm.prank(alice);
        vm.expectRevert();
        registry.safeTransferFrom(alice, bob, tokenId, 1000, "");
    }

    function testPauseBlocksRetirement() public {
        vm.prank(admin);
        uint256 tokenId = registry.issueBatch(
            batchHash,
            "ipfs://canonical-batch-001",
            1700000000,
            1700086400,
            100000,
            "METH-1.0",
            "SCHEMA-1.0"
        );

        vm.prank(treasury);
        registry.safeTransferFrom(treasury, alice, tokenId, 5000, "");

        vm.prank(admin);
        registry.pause();

        vm.prank(alice);
        vm.expectRevert();
        registry.retire(
            tokenId,
            1000,
            "RET-001",
            "Alice Ltda",
            "Environmental claim"
        );
    }

    // ═══════════════════════════════════════════════════════════════
    // RECEIPT TESTS
    // ═══════════════════════════════════════════════════════════════

    function testReceiptIsNonTransferable() public {
        vm.prank(admin);
        uint256 tokenId = registry.issueBatch(
            batchHash,
            "ipfs://canonical-batch-001",
            1700000000,
            1700086400,
            100000,
            "METH-1.0",
            "SCHEMA-1.0"
        );

        vm.prank(treasury);
        registry.safeTransferFrom(treasury, alice, tokenId, 5000, "");

        vm.prank(alice);
        registry.retire(
            tokenId,
            1000,
            "RET-001",
            "Alice Ltda",
            "Environmental claim"
        );

        // Try to transfer receipt from alice to bob
        vm.prank(alice);
        vm.expectRevert(HECRetirementReceipt.NonTransferable.selector);
        receipt.transferFrom(alice, bob, 1);
    }
}
