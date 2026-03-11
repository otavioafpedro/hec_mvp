// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "@openzeppelin/contracts/security/ReentrancyGuard.sol";
import "@openzeppelin/contracts/security/Pausable.sol";

/**
 * @title HECRegistry
 * @notice Registro on-chain de certificados HEC (Hydroelectric Energy Certificate)
 * @dev Versão melhorada com:
 *      - Proteção contra transferência de HECs aposentados
 *      - Funções de transferência e aposentadoria de certificados
 *      - Two-step ownership transfer
 *      - Pausabilidade para emergências
 *      - ReentrancyGuard para segurança futura
 *
 * Deploy target: Polygon PoS (Amoy testnet → mainnet)
 * Gas estimado: ~85k per register(), ~95k per transferCertificate()
 */
contract HECRegistry is ReentrancyGuard, Pausable {

    // ═══════════════════════════════════════════════════════════════
    // STATE
    // ═══════════════════════════════════════════════════════════════

    struct Certificate {
        bytes32 certificateHash;    // SHA-256 do JSON canônico
        string  ipfsCID;            // CID do JSON no IPFS
        address registeredBy;       // Endereço que registrou
        address currentHolder;      // Endereço atual do certificado
        uint256 registeredAt;       // Block timestamp do registro
        uint256 blockNumber;        // Block number do registro
        bool    exists;             // Flag de existência
        bool    isRetired;          // Flag de aposentadoria
        uint256 retiredAt;          // Timestamp da aposentadoria (0 se não aposentado)
        string  retirementReason;   // Motivo da aposentadoria
    }

    /// @notice Owner do contrato (pode registrar certificados)
    address public owner;

    /// @notice Pending owner para two-step transfer
    address public pendingOwner;

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
        address indexed currentHolder,
        uint256 registeredAt,
        uint256 blockNumber
    );

    /// @notice Emitido quando um certificado é transferido
    event CertificateTransferred(
        bytes32 indexed certificateHash,
        address indexed from,
        address indexed to,
        uint256 timestamp
    );

    /// @notice Emitido quando um certificado é aposentado
    event CertificateRetired(
        bytes32 indexed certificateHash,
        address indexed retiredBy,
        string reason,
        uint256 timestamp
    );

    /// @notice Emitido quando ownership é transferido (iniciado)
    event OwnershipTransferInitiated(
        address indexed currentOwner,
        address indexed pendingOwner
    );

    /// @notice Emitido quando ownership é aceito
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

    modifier onlyOwnerOrHolder(bytes32 certificateHash) {
        Certificate storage cert = certificates[certificateHash];
        require(
            msg.sender == owner || msg.sender == cert.currentHolder,
            "HECRegistry: caller is not owner or certificate holder"
        );
        _;
    }

    modifier certificateExists(bytes32 certificateHash) {
        require(
            certificates[certificateHash].exists,
            "HECRegistry: certificate does not exist"
        );
        _;
    }

    modifier certificateNotRetired(bytes32 certificateHash) {
        require(
            !certificates[certificateHash].isRetired,
            "HECRegistry: certificate is retired and cannot be transferred"
        );
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
     *      O certificado começa com o owner como holder
     */
    function register(
        bytes32 certificateHash,
        string calldata ipfsCID
    ) external onlyOwner whenNotPaused {
        require(certificateHash != bytes32(0), "HECRegistry: hash cannot be zero");
        require(bytes(ipfsCID).length > 0, "HECRegistry: IPFS CID cannot be empty");
        require(!certificates[certificateHash].exists, "HECRegistry: hash already registered");
        
        // Validar formato básico do CID (CIDv0 começa com 'Q', CIDv1 com 'bafy')
        bytes memory cidBytes = bytes(ipfsCID);
        require(
            cidBytes[0] == 'Q' || cidBytes[0] == 'b',
            "HECRegistry: invalid IPFS CID format"
        );

        certificates[certificateHash] = Certificate({
            certificateHash: certificateHash,
            ipfsCID: ipfsCID,
            registeredBy: msg.sender,
            currentHolder: msg.sender,
            registeredAt: block.timestamp,
            blockNumber: block.number,
            exists: true,
            isRetired: false,
            retiredAt: 0,
            retirementReason: ""
        });

        allHashes.push(certificateHash);
        totalCertificates++;

        emit CertificateRegistered(
            certificateHash,
            ipfsCID,
            msg.sender,
            msg.sender,
            block.timestamp,
            block.number
        );
    }

    /**
     * @notice Transfere a propriedade de um certificado para outro endereço
     * @param certificateHash Hash do certificado a transferir
     * @param newHolder Novo detentor do certificado
     * @dev Apenas o owner ou o holder atual podem transferir
     *      Certificados aposentados NÃO podem ser transferidos
     *      Emite evento CertificateTransferred
     */
    function transferCertificate(
        bytes32 certificateHash,
        address newHolder
    ) 
        external 
        onlyOwnerOrHolder(certificateHash)
        certificateExists(certificateHash)
        certificateNotRetired(certificateHash)
        nonReentrant
        whenNotPaused
    {
        require(newHolder != address(0), "HECRegistry: new holder cannot be zero address");
        
        Certificate storage cert = certificates[certificateHash];
        address previousHolder = cert.currentHolder;
        
        require(newHolder != previousHolder, "HECRegistry: new holder is the same as current holder");

        cert.currentHolder = newHolder;

        emit CertificateTransferred(
            certificateHash,
            previousHolder,
            newHolder,
            block.timestamp
        );
    }

    /**
     * @notice Aposenta um certificado (marca como inativo)
     * @param certificateHash Hash do certificado a aposentar
     * @param reason Motivo da aposentadoria
     * @dev Apenas o owner ou o holder atual podem aposentar
     *      Certificados aposentados não podem ser transferidos
     *      Esta operação é irreversível
     */
    function retireCertificate(
        bytes32 certificateHash,
        string calldata reason
    ) 
        external 
        onlyOwnerOrHolder(certificateHash)
        certificateExists(certificateHash)
        nonReentrant
        whenNotPaused
    {
        Certificate storage cert = certificates[certificateHash];
        
        require(!cert.isRetired, "HECRegistry: certificate is already retired");
        require(bytes(reason).length > 0, "HECRegistry: retirement reason cannot be empty");

        cert.isRetired = true;
        cert.retiredAt = block.timestamp;
        cert.retirementReason = reason;

        emit CertificateRetired(
            certificateHash,
            msg.sender,
            reason,
            block.timestamp
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
     * @return currentHolder Detentor atual do certificado
     * @return isRetired True se o certificado está aposentado
     * @return retiredAt Timestamp da aposentadoria (0 se não aposentado)
     */
    function verify(bytes32 certificateHash)
        external
        view
        returns (
            bool exists,
            string memory ipfsCID,
            uint256 registeredAt,
            uint256 blockNumber,
            address currentHolder,
            bool isRetired,
            uint256 retiredAt
        )
    {
        Certificate storage cert = certificates[certificateHash];
        return (
            cert.exists,
            cert.ipfsCID,
            cert.registeredAt,
            cert.blockNumber,
            cert.currentHolder,
            cert.isRetired,
            cert.retiredAt
        );
    }

    /**
     * @notice Retorna informações completas de um certificado
     * @param certificateHash Hash do certificado
     * @return cert Estrutura completa do certificado
     */
    function getCertificate(bytes32 certificateHash)
        external
        view
        certificateExists(certificateHash)
        returns (Certificate memory)
    {
        return certificates[certificateHash];
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

    /**
     * @notice Retorna o número total de certificados
     * @return Total de certificados registrados
     */
    function getTotalCertificates() external view returns (uint256) {
        return totalCertificates;
    }

    /**
     * @notice Retorna certificados de forma paginada
     * @param offset Índice inicial
     * @param limit Quantidade de certificados a retornar
     * @return Array de hashes dos certificados
     */
    function getCertificatesPaginated(uint256 offset, uint256 limit)
        external
        view
        returns (bytes32[] memory)
    {
        require(offset + limit <= allHashes.length, "HECRegistry: invalid range");
        require(limit > 0, "HECRegistry: limit must be greater than 0");
        
        bytes32[] memory result = new bytes32[](limit);
        for (uint256 i = 0; i < limit; i++) {
            result[i] = allHashes[offset + i];
        }
        return result;
    }

    // ═══════════════════════════════════════════════════════════════
    // ADMIN FUNCTIONS
    // ═══════════════════════════════════════════════════════════════

    /**
     * @notice Inicia transferência de ownership (two-step process)
     * @param newOwner Novo owner
     * @dev Requer confirmação do novo owner via acceptOwnership()
     */
    function transferOwnership(address newOwner) external onlyOwner {
        require(newOwner != address(0), "HECRegistry: new owner is zero address");
        require(newOwner != owner, "HECRegistry: new owner is the same as current owner");
        
        pendingOwner = newOwner;
        emit OwnershipTransferInitiated(owner, newOwner);
    }

    /**
     * @notice Aceita transferência de ownership (completa two-step process)
     * @dev Deve ser chamado pelo pendingOwner
     */
    function acceptOwnership() external {
        require(msg.sender == pendingOwner, "HECRegistry: caller is not the pending owner");
        
        address previousOwner = owner;
        owner = msg.sender;
        pendingOwner = address(0);
        
        emit OwnershipTransferred(previousOwner, msg.sender);
    }

    /**
     * @notice Pausa o contrato (apenas owner)
     * @dev Impede novos registros e transferências
     */
    function pause() external onlyOwner {
        _pause();
    }

    /**
     * @notice Despausa o contrato (apenas owner)
     */
    function unpause() external onlyOwner {
        _unpause();
    }

    /**
     * @notice Verifica se o contrato está pausado
     * @return True se pausado
     */
    function isPaused() external view returns (bool) {
        return paused();
    }
}
