# Arquitetura do Sistema HEC Registry

## Visão Geral

O sistema HEC Registry é composto por dois contratos inteligentes que trabalham em conjunto para gerenciar o ciclo de vida completo de certificados de energia renovável (HECs).

## Componentes Principais

### 1. HEC1155Registry (Contrato Principal)

**Responsabilidades:**
* Emissão de lotes de HECs
* Gerenciamento de transferências entre holders
* Aposentadoria parcial de HECs
* Controle de status de lotes (ACTIVE, SUSPENDED, REVOKED)
* Pausa de emergência

**Padrão:** ERC-1155 (Multi-Token Standard)

**Por que ERC-1155?**
O ERC-1155 foi escolhido porque cada lote de HEC é único e precisa ser rastreado individualmente. Diferentemente do ERC-20 (que trata todos os tokens como fungíveis), o ERC-1155 permite que cada token ID represente um lote distinto com seus próprios metadados, status e histórico de aposentadoria.

**Estrutura de Dados Chave:**

```solidity
struct HECBatch {
    uint256 tokenId;                  // ID único do lote
    bytes32 batchHash;                // Hash SHA-256 do certificado
    string canonicalCID;              // CID IPFS dos metadados
    uint64 issuedAt;                  // Timestamp de emissão
    uint64 periodStart;               // Início do período de geração
    uint64 periodEnd;                 // Fim do período de geração
    uint256 totalIssuedUnits;         // Total de unidades emitidas
    uint256 totalRetiredUnits;        // Total de unidades aposentadas
    address issuer;                   // Endereço do emissor
    BatchStatus status;               // Estado do lote
    uint64 statusChangedAt;           // Timestamp da última mudança de status
    string methodologyVersion;        // Versão da metodologia
    string schemaVersion;             // Versão do schema
    string statusReason;              // Motivo da mudança de status
}
```

**Unidade Mínima:** 1 HEC = 1000 unidades (permitindo fracionamento de 0.001 HEC)

### 2. HECRetirementReceipt (Contrato de Recibos)

**Responsabilidades:**
* Emissão de recibos de aposentadoria (NFTs Soulbound)
* Armazenamento de dados de aposentadoria
* Garantir intransferibilidade dos recibos

**Padrão:** ERC-721URIStorage (NFT com URI de metadados)

**Por que Soulbound?**
Um recibo de aposentadoria é prova permanente de que um certificado foi consumido. Ele não deve ser transferível porque:
1. Representa um benefício ambiental já reivindicado
2. Sua transferência poderia criar confusão sobre quem realmente beneficiou-se da energia limpa
3. Serve como comprovante legal/regulatório para a empresa que aposentou

**Estrutura de Dados Chave:**

```solidity
struct ReceiptData {
    uint256 receiptId;                // ID único do recibo
    uint256 batchTokenId;             // ID do lote aposentado
    uint256 amountUnits;              // Quantidade de unidades aposentadas
    uint64 retiredAt;                 // Timestamp da aposentadoria
    string retirementReference;       // Referência externa (ex: ID de projeto)
    string beneficiaryName;           // Nome da empresa que aposentou
    string purpose;                   // Propósito da aposentadoria
}
```

## Fluxo de Dados

### Fluxo 1: Emissão (Minting)

```
Backend valida telemetria
    ↓
Backend gera certificado (JSON + hash)
    ↓
Backend faz upload para IPFS
    ↓
Backend chama registry.issueBatch()
    ↓
Contrato minta tokens para Treasury
    ↓
Evento BatchIssued emitido
```

### Fluxo 2: Negociação (Trading)

```
Treasury tem 100k unidades do lote
    ↓
Treasury transfere 5k para Alice
    ↓
Alice transfere 2k para Bob
    ↓
Saldo final: Treasury 95k, Alice 3k, Bob 2k
    ↓
(Só funciona se lote está ACTIVE)
```

### Fluxo 3: Aposentadoria (Retirement)

```
Alice tem 3k unidades do lote
    ↓
Alice chama retire(3k, "RET-001", "Alice Inc", "Carbon offset")
    ↓
Contrato queima 3k tokens de Alice
    ↓
totalRetiredUnits aumenta em 3k
    ↓
Contrato chama receiptContract.mintReceipt()
    ↓
Recibo Soulbound é emitido para Alice
    ↓
Evento Retired emitido
    ↓
Alice agora tem: 0 tokens ativos + 1 recibo intransferível
```

## Segurança e Proteções

### 1. Controle de Transferência por Status

```solidity
// Dentro de _update (chamado antes de toda transferência)
if (batch.status != BatchStatus.ACTIVE) {
    revert NonTransferableStatus(batch.status);
}
```

**Efeito:** Tokens só podem ser transferidos se o lote estiver ACTIVE. Qualquer mudança para SUSPENDED ou REVOKED bloqueia todas as transferências.

### 2. Queima Irreversível na Aposentadoria

```solidity
_burn(msg.sender, batchTokenId, amountUnits);
```

**Efeito:** Uma vez aposentado, o token é destruído do ledger. Não pode ser recuperado ou re-emitido.

### 3. Recibos Intransferíveis

```solidity
function _update(address to, uint256 tokenId, address auth) internal override returns (address) {
    address from = _ownerOf(tokenId);
    if (from != address(0) && to != address(0)) revert NonTransferable();
    return super._update(to, tokenId, auth);
}
```

**Efeito:** Permite minting (from == 0), mas bloqueia transferências (from != 0 && to != 0).

### 4. Pausa de Emergência

```solidity
function _update(...) internal override {
    if (paused()) revert EnforcedPause();
    // ...
}
```

**Efeito:** Se o contrato for pausado, nenhuma operação de transferência, emissão ou aposentadoria é permitida.

### 5. Controle de Acesso Granular

| Função | Papel Requerido | Descrição |
|--------|-----------------|-----------|
| `issueBatch` | REGISTRAR_ROLE | Apenas backend autorizado |
| `retire` | Nenhum | Qualquer holder pode aposentar |
| `setBatchStatus` | STATUS_MANAGER_ROLE | Apenas compliance/governança |
| `pause` / `unpause` | PAUSER_ROLE | Apenas em emergências |
| `setReceiptContract` | DEFAULT_ADMIN_ROLE | Apenas admin supremo |

## Versionamento e Compatibilidade

### Schema Version
Incrementar quando a estrutura do JSON canônico muda (ex: adição de novo campo obrigatório).

### Methodology Version
Incrementar quando a forma de calcular a energia muda (ex: novo algoritmo de validação).

**Exemplo:**
* Lote 1: SCHEMA-1.0, METH-1.0
* Lote 2: SCHEMA-1.0, METH-1.1 (mesmo schema, metodologia melhorada)
* Lote 3: SCHEMA-2.0, METH-2.0 (novo schema, nova metodologia)

## Integração com IPFS

Cada lote referencia um arquivo JSON no IPFS através de seu CID. Este arquivo contém:
* Metadados completos do lote
* Evidências de telemetria
* Referências a auditorias
* Hashes de validação

O contrato armazena apenas o CID, não o conteúdo completo. Isso economiza gas e garante que os dados não possam ser alterados (IPFS é imutável).

## Governança Recomendada

Para produção, recomenda-se:

| Papel | Controlado por | Tipo |
|-------|----------------|------|
| DEFAULT_ADMIN_ROLE | Gnosis Safe 3/5 | Multisig |
| REGISTRAR_ROLE | Backend Signer | Hot Wallet |
| STATUS_MANAGER_ROLE | Gnosis Safe 2/3 | Submultisig |
| PAUSER_ROLE | Gnosis Safe 2/3 | Submultisig |
| MINTER_ROLE (Receipt) | HEC1155Registry | Contrato |

## Considerações de Gas

* **Emissão:** ~85k gas
* **Transferência:** ~25k gas (ERC-1155 padrão)
* **Aposentadoria:** ~95k gas (inclui queima + emissão de recibo)
* **Mudança de Status:** ~35k gas

## Limitações e Trade-offs

1. **Sem Upgradability:** O contrato não é upgradable. Mudanças futuras requerem redeployment.
2. **Sem Fractional Ownership Dinâmico:** A fração mínima é fixa em 0.001 HEC.
3. **Sem Mercado Integrado:** Transferências são P2P. Não há DEX integrada.
4. **Sem Staking:** Não há incentivos de yield para holders.

Esses trade-offs foram feitos intencionalmente para manter a simplicidade, segurança e conformidade regulatória.
