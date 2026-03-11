// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/**
 * @title HECRegistry
 * @notice Registro on-chain de certificados HEC (Hydroelectric Energy Certificate)
 * @dev Cada certificado é registrado pelo seu SHA-256 hash + IPFS CID.
 *      O contrato garante:
 *        - Cada hash só pode ser registrado UMA vez (imutável)
 *        - Apenas o owner pode registrar (controle de emissão)
 *        - Verificação pública de qualquer certificado (transparência)
 *
 * Deploy target: Polygon PoS (Amoy testnet → mainnet)
 * Gas estimado: ~65k per register()
 *
 * Fluxo:
 *   1. Backend valida telemetria (5 camadas Fortaleza Lógica)
 *   2. Backend gera certificado HEC (JSON + PDF + SHA-256)
 *   3. Backend upload IPFS (JSON + PDF → CIDs)
 *   4. Backend chama register(hash, ipfsCID) neste contrato
 *   5. Contrato emite Certificate Registered event
 *   6. Qualquer pessoa pode chamar verify(hash) para checar
 */
contract HECRegistry {

    // ═══════════════════════════════════════════════════════════════
    // STATE
    // ═══════════════════════════════════════════════════════════════

    struct Certificate {
        bytes32 certificateHash;    // SHA-256 do JSON canônico
        string  ipfsCID;            // CID do JSON no IPFS
        address registeredBy;       // Endereço que registrou
        uint256 registeredAt;       // Block timestamp do registro
        uint256 blockNumber;        // Block number do registro
        bool    exists;             // Flag de existência
    }

    /// @notice Owner do contrato (pode registrar certificados)
    address public owner;

    /// @notice Mapping hash → Certificate
    mapping(bytes32 => Certificate) public certificates;

    /// @notice Contador total de certificados registrados
    uint256 public totalCertificates;

    /// @notice Array de todos os hashes (para enumeração)
    bytes32[] public allHashes;

    // ═══════════════════════════════════════════════════════════════
    // EVENTS
    // ═══════════════════════════════════════════════════════════════

    /// @notice Emitido quando um certificado é registrado
    event CertificateRegistered(
        bytes32 indexed certificateHash,
        string  ipfsCID,
        address indexed registeredBy,
        uint256 registeredAt,
        uint256 blockNumber
    );

    /// @notice Emitido quando ownership é transferido
    event OwnershipTransferred(
        address indexed previousOwner,
        address indexed newOwner
    );

    // ═══════════════════════════════════════════════════════════════
    // MODIFIERS
    // ═══════════════════════════════════════════════════════════════

    modifier onlyOwner() {
        require(msg.sender == owner, "HECRegistry: caller is not the owner");
        _;
    }

    // ═══════════════════════════════════════════════════════════════
    // CONSTRUCTOR
    // ═══════════════════════════════════════════════════════════════

    constructor() {
        owner = msg.sender;
        emit OwnershipTransferred(address(0), msg.sender);
    }

    // ═══════════════════════════════════════════════════════════════
    // WRITE FUNCTIONS
    // ═══════════════════════════════════════════════════════════════

    /**
     * @notice Registra um certificado HEC on-chain
     * @param certificateHash SHA-256 hash do certificado (bytes32)
     * @param ipfsCID CID do JSON canônico no IPFS
     * @dev Cada hash só pode ser registrado uma vez (imutável)
     *      Somente o owner pode chamar esta função
     */
    function register(
        bytes32 certificateHash,
        string calldata ipfsCID
    ) external onlyOwner {
        require(certificateHash != bytes32(0), "HECRegistry: hash cannot be zero");
        require(bytes(ipfsCID).length > 0, "HECRegistry: IPFS CID cannot be empty");
        require(!certificates[certificateHash].exists, "HECRegistry: hash already registered");

        certificates[certificateHash] = Certificate({
            certificateHash: certificateHash,
            ipfsCID: ipfsCID,
            registeredBy: msg.sender,
            registeredAt: block.timestamp,
            blockNumber: block.number,
            exists: true
        });

        allHashes.push(certificateHash);
        totalCertificates++;

        emit CertificateRegistered(
            certificateHash,
            ipfsCID,
            msg.sender,
            block.timestamp,
            block.number
        );
    }

    // ═══════════════════════════════════════════════════════════════
    // READ FUNCTIONS (public verification)
    // ═══════════════════════════════════════════════════════════════

    /**
     * @notice Verifica se um certificado está registrado
     * @param certificateHash SHA-256 hash do certificado
     * @return exists True se registrado
     * @return ipfsCID CID do IPFS (vazio se não registrado)
     * @return registeredAt Timestamp do registro (0 se não registrado)
     * @return blockNumber Bloco do registro (0 se não registrado)
     */
    function verify(bytes32 certificateHash)
        external
        view
        returns (
            bool exists,
            string memory ipfsCID,
            uint256 registeredAt,
            uint256 blockNumber
        )
    {
        Certificate storage cert = certificates[certificateHash];
        return (
            cert.exists,
            cert.ipfsCID,
            cert.registeredAt,
            cert.blockNumber
        );
    }

    /**
     * @notice Retorna o hash no índice dado
     * @param index Índice no array allHashes
     * @return certificateHash Hash no índice
     */
    function getHashAtIndex(uint256 index) external view returns (bytes32) {
        require(index < allHashes.length, "HECRegistry: index out of bounds");
        return allHashes[index];
    }

    // ═══════════════════════════════════════════════════════════════
    // ADMIN
    // ═══════════════════════════════════════════════════════════════

    /**
     * @notice Transfere ownership do contrato
     * @param newOwner Novo owner
     */
    function transferOwnership(address newOwner) external onlyOwner {
        require(newOwner != address(0), "HECRegistry: new owner is zero address");
        emit OwnershipTransferred(owner, newOwner);
        owner = newOwner;
    }
}
