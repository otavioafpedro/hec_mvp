# SOA/SOS — Justificativa do Split MySQL × PostgreSQL

## Por que MySQL (MariaDB) para dados transacionais?

As 25 tabelas em MySQL cobrem **entidades de negócio com relações rígidas** (FKs, constraints, ACID):
organizações, usuários, RBAC, sites, dispositivos, contratos, assinaturas, carteiras, token ERC-1155,
lotes/certificados HEC com intervalo de energia, ownership, transfers, claims/retirement/burn,
marketplace (listings/orders/trades), pagamentos/invoices, árvores Merkle, feeds oracle, pipeline DS,
PoCE sessions, iCleanSolar, integrações, jobs, audit log, pricing Ouro Verde, notificações, fatores
de emissão e curtailment. São dados com cardinalidade moderada (milhares–milhões de linhas), alta
taxa de leitura por chave primária/FK, e necessidade de JOINs complexos entre entidades — cenário
ideal para InnoDB com índices B-Tree e buffer pool agressivo.

## Por que PostgreSQL + TimescaleDB para séries temporais?

As 12 tabelas + 2 continuous aggregates em PostgreSQL armazenam **dados de altíssima cardinalidade e
ingestão contínua** que crescem ilimitadamente: telemetria de inversores (1440 pts/dia/device), leituras
de medidores, energia por intervalo horário, features ML por intervalo, scores QSV, sinais de anomalia
(LSTM autoencoder), observações meteorológicas (INMET/SONDA), observações satelitais (Copernicus), SDK
burn segundo a segundo (potencialmente milhões/dia), fatores de emissão horários, validação de vizinhos
(S4) e saída teórica pvlib. TimescaleDB oferece hypertables com chunk automático, índices BRIN para
varredura temporal eficiente, continuous aggregates nativos, compression policies que reduzem storage
em 90%+ para dados históricos, e retention policies para controle de volume. Particionamento por RANGE
(mensal) seria o fallback caso TimescaleDB não esteja disponível.

## Resumo da separação

| Critério             | MySQL (transacional)              | PostgreSQL (time-series)             |
|----------------------|-----------------------------------|--------------------------------------|
| Tabelas              | 25 tabelas + 3 de junção          | 12 hypertables + 2 mat. views        |
| Cardinalidade        | Milhares–baixos milhões           | Bilhões (projeção 2 anos)            |
| Padrão de acesso     | OLTP, JOINs, FK constraints       | Append-mostly, range scans, rollups  |
| Índices              | B-Tree (PK, FK, filtros)          | BRIN (temporal) + B-Tree (device/site)|
| Compressão           | InnoDB page compression (opc.)    | TimescaleDB chunk compression nativa |
| Retenção             | Permanente (dados legais)         | Policies: 1-5 anos por tabela        |
