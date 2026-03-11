// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "forge-std/Script.sol";
import "../src/HEC1155Registry.sol";
import "../src/HECRetirementReceipt.sol";

contract Deploy is Script {
    function run() public {
        uint256 deployerPrivateKey = vm.envUint("DEPLOYER_PRIVATE_KEY");
        address admin = vm.addr(deployerPrivateKey);
        address treasury = vm.envAddress("TREASURY_ADDRESS");

        vm.startBroadcast(deployerPrivateKey);

        // Deploy HEC1155Registry
        HEC1155Registry registry = new HEC1155Registry(
            admin,
            treasury,
            "ipfs://QmYourBaseURI/" // Update with your IPFS base URI
        );

        // Deploy HECRetirementReceipt
        HECRetirementReceipt receipt = new HECRetirementReceipt(admin);

        // Link the contracts
        registry.setReceiptContract(address(receipt));

        // Grant MINTER_ROLE to registry in receipt contract
        receipt.grantRole(receipt.MINTER_ROLE(), address(registry));

        vm.stopBroadcast();

        // Log addresses
        console.log("HEC1155Registry deployed at:", address(registry));
        console.log("HECRetirementReceipt deployed at:", address(receipt));
    }
}
