# Registro HEC (Hydroelectric Energy Certificate)

Olá! Bem-vindo ao repositório oficial do sistema de registro de HECs (Hydroelectric Energy Certificates). Este projeto implementa um modelo robusto de tokenização para certificados de energia renovável, garantindo transparência, rastreabilidade e segurança em todo o ciclo de vida do ativo.

## Visão Geral do Sistema

O sistema foi desenhado para resolver um desafio comum em mercados ambientais: como permitir a negociação livre de um certificado ambiental enquanto ele está ativo, mas garantir que ele não possa mais circular depois de ser "consumido" ou "aposentado" por uma empresa para abater suas emissões.

Para resolver isso, adotamos uma arquitetura de dois contratos:

1. **HEC1155Registry**: Gerencia os HECs ativos. Usa o padrão ERC-1155 para representar lotes de energia. Cada lote (uma safra de uma usina específica, por exemplo) é um token ID diferente. Esses tokens são fracionáveis (1 HEC = 1000 unidades) e livremente transferíveis.
2. **HECRetirementReceipt**: Gerencia os recibos de aposentadoria. É um NFT Soulbound (ERC-721 intransferível). Quando alguém decide consumir (aposentar) seus HECs, os tokens ativos são queimados no ERC-1155 e um recibo permanente e intransferível é emitido neste contrato.

## Como Funciona na Prática

### 1. Emissão (Minting)
O administrador (com o papel `REGISTRAR_ROLE`) emite um novo lote de HECs. Cada lote recebe um ID único e é associado a um hash de lote, um CID do IPFS (contendo os metadados completos em JSON), versões de metodologia e schema, e o período de geração da energia. Os tokens recém-criados vão direto para a carteira da tesouraria (Treasury).

### 2. Negociação e Transferência
A tesouraria pode vender frações desse lote para empresas. As empresas podem, por sua vez, revender essas frações para terceiros. Tudo isso acontece através de transferências padrão do ERC-1155, e só é permitido enquanto o status do lote for `ACTIVE`.

### 3. Aposentadoria (Retirement)
Quando uma empresa decide usar os HECs para comprovar o uso de energia limpa, ela chama a função `retire` no contrato ERC-1155. O que acontece nos bastidores:
* A quantidade especificada de tokens ativos é **queimada** da carteira da empresa.
* O contrato atualiza a contabilidade interna (aumenta o `totalRetiredUnits`).
* O contrato chama o `HECRetirementReceipt` para emitir um recibo Soulbound para a empresa.
* Esse recibo contém todos os detalhes da aposentadoria (quem aposentou, a quantidade, o propósito, o nome do beneficiário, etc.).

Como os tokens ativos foram queimados e o recibo é intransferível, é **matematicamente impossível** que um HEC aposentado volte a circular no mercado.

## Estados do Lote e Segurança

Para garantir a integridade do mercado, cada lote possui um status que afeta sua transferibilidade:
* **ACTIVE**: Transferências e aposentadorias são permitidas.
* **SUSPENDED**: Transferências e aposentadorias são bloqueadas temporariamente (ex: para auditoria).
* **REVOKED**: Transferências e aposentadorias são bloqueadas permanentemente (ex: se for descoberta fraude na medição da usina).

Além disso, o contrato principal possui um mecanismo de **Pausa de Emergência**, que permite ao administrador paralisar todas as operações globais do contrato caso seja descoberta alguma vulnerabilidade crítica.

## Estrutura do Projeto (Foundry)

Este projeto foi construído usando o [Foundry](https://book.getfoundry.sh/), um framework de desenvolvimento de smart contracts super rápido escrito em Rust.

```text
hec-registry/
├── src/
│   ├── HEC1155Registry.sol        # Contrato principal (ERC-1155)
│   └── HECRetirementReceipt.sol   # Contrato de recibos (ERC-721 Soulbound)
├── test/
│   └── HEC1155Registry.t.sol      # Suite de testes unitários e de integração
├── foundry.toml                   # Configuração do Foundry
└── README.md                      # Este arquivo
```

## Como Rodar Localmente

### Pré-requisitos
Você precisa ter o Foundry instalado. Se não tiver, rode:
```bash
curl -L https://foundry.paradigm.xyz | bash
foundryup
```

### Instalação e Testes
1. Clone o repositório e entre na pasta:
   ```bash
   cd hec-registry
   ```
2. Instale as dependências do OpenZeppelin (necessário para os contratos base):
   ```bash
   forge install OpenZeppelin/openzeppelin-contracts --no-commit
   ```
3. Compile os contratos:
   ```bash
   forge build
   ```
4. Rode a suite de testes:
   ```bash
   forge test -vv
   ```

## Governança e Papéis (Roles)

O sistema utiliza controle de acesso baseado em papéis (RBAC) para garantir que apenas entidades autorizadas possam realizar ações sensíveis:

* `DEFAULT_ADMIN_ROLE`: Papel supremo. Pode conceder/revogar outros papéis e alterar o endereço da tesouraria ou do contrato de recibos. **Deve ser controlado por uma carteira Multisig (ex: Gnosis Safe).**
* `REGISTRAR_ROLE`: Pode emitir novos lotes de HECs. Geralmente atribuído ao backend automatizado do sistema de registro.
* `STATUS_MANAGER_ROLE`: Pode alterar o status de um lote (Suspender, Revogar, Ativar).
* `PAUSER_ROLE`: Pode pausar ou despausar o contrato inteiro em emergências.
* `MINTER_ROLE` (no contrato de recibos): Apenas o contrato `HEC1155Registry` deve ter esse papel, garantindo que recibos só sejam emitidos como resultado de uma aposentadoria válida.

## Padrão de Metadados (JSON Canônico)

Para garantir a transparência e o versionamento, cada lote deve ser acompanhado de um arquivo JSON salvo no IPFS. O CID desse arquivo é registrado on-chain na emissão do lote. Exemplo de estrutura esperada:

```json
{
  "documentType": "HEC_BATCH",
  "batchHash": "0x...",
  "schemaVersion": "HEC-SCHEMA-1.0.0",
  "methodologyVersion": "HEC-METHODOLOGY-1.0.0",
  "issuer": "Solar One Account",
  "plantId": "PLANT-0001",
  "periodStart": "2026-03-01T00:00:00Z",
  "periodEnd": "2026-03-31T23:59:59Z",
  "totalIssuedUnits": 250000,
  "unitScale": 1000,
  "energyMWh": 250.0,
  "evidence": {
    "telemetryRoot": "0x...",
    "weatherRoot": "0x...",
    "auditReportCID": "ipfs://..."
  }
}
```

Qualquer alteração na estrutura desse JSON deve resultar no incremento da `schemaVersion`. Qualquer alteração na forma como a energia é calculada ou validada deve resultar no incremento da `methodologyVersion`. O contrato registra ambas as versões no momento da emissão do lote.

## Considerações Finais

A linguagem jurídica e operacional em torno da aposentadoria é crucial. Neste sistema, "Retirement" (Aposentadoria) é o ato irrevogável de destinação ambiental do atributo representado pelo HEC. Não significa que o registro foi destruído, mas sim que sua negociabilidade econômica foi encerrada definitivamente, transformando-se em um comprovante permanente de benefício ambiental.
