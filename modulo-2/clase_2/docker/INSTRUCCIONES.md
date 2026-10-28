# docker compose: un stack blockchain local

En la clase 4 dockerizamos **un** servicio (`chain-api`, ver `modulo-2/clase_1/docker/INSTRUCCIONES.md`).
Hoy levantamos **varios** containers que trabajan juntos: una blockchain local, nuestro token
(`QuantToken`, el contrato de Remix), un indexer que guarda los eventos en Postgres y la misma
`chain-api` apuntando a la chain local.

```
docker/
├── INSTRUCCIONES.md       <- este archivo
├── docker-compose.yml     <- el stack completo
├── deployer/
│   ├── QuantToken.json    <- ABI + bytecode de modulo-2/clase_1/contracts/QuantToken.sol (OpenZeppelin 5.4)
│   ├── deploy.py          <- deploya el token y hace 3 transfers
│   └── transfer.py        <- transfers a mano (QNT o ETH)
└── indexer/
    └── indexer.py         <- guarda cada bloque y cada Transfer ERC-20 en Postgres
```

Antes de la clase: Docker Desktop andando (`docker version`) y, para no depender del wifi,
`docker pull ghcr.io/foundry-rs/foundry:v1.5.1 postgres:17-alpine adminer:5 python:3.13-slim`
(~2 GB en total).

Un `docker-compose.yml` describe **varios** containers, cómo se conectan y en qué orden arrancan.
Se levantan y se apagan todos juntos con un solo comando.

| Servicio | Qué hace | Puerto en tu máquina |
|---|---|---|
| `anvil` | blockchain EVM **local** (Foundry): un bloque cada 2 s y 10 cuentas con 10.000 ETH de prueba | `8546` |
| `deployer` | deploya **QuantToken** (el contrato de Remix de hoy) y hace 3 transfers; después termina | - |
| `postgres` | base de datos | - (solo adentro) |
| `indexer` | escucha cada bloque nuevo por websocket y guarda bloques y eventos `Transfer` en Postgres | - |
| `chain-api` | **la imagen de la clase 4** (`modulo-2/clase_1/docker/chain-api`), apuntando a anvil en vez de Polygon | `8001` |
| `adminer` | interfaz web para mirar la base | `8088` |

¿Por qué una chain local? Mandar transacciones sin wallet de testnet, sin faucet y sin esperar:
anvil arranca con cuentas con fondos y **desbloqueadas** (el nodo firma por ellas), así que no
hace falta ninguna private key. Cada vez que se reinicia, la chain vuelve a cero.

## 1. Levantar todo

```bash
cd modulo-2/clase_2/docker
docker compose up -d --build       # construye las imágenes y levanta todo en segundo plano
docker compose ps -a               # anvil y postgres "healthy"; a los ~10 s el deployer pasa a "Exited (0)"
```

La primera vez tarda más: baja las imágenes base y construye tres (depende de la conexión). Si `docker compose logs deployer`
todavía no muestra las 3 transfers, el deployer sigue trabajando: esperar unos segundos.

El orden lo define `depends_on` + `healthcheck`: el deployer, el indexer y la chain-api esperan a
que anvil responda; el indexer espera también a Postgres.

## 2. Ver qué pasó

```bash
docker compose logs deployer       # dirección del token, las 3 transfers y los balances
docker compose logs -f indexer     # cada bloque y cada Transfer en vivo (Ctrl+C para salir)
```

El token queda siempre en `0x5FbDB2315678afecb367f032d93F642f64180aa3`: la dirección de un contrato
depende solo de quién lo deploya y de su nonce, y en una anvil nueva son siempre los mismos.

## 3. Consultar la chain local con la chain-api

```bash
curl localhost:8001/health          # chain_id 31337 = anvil
curl localhost:8001/block
curl "localhost:8001/token/0x5FbDB2315678afecb367f032d93F642f64180aa3?holder=0x70997970C51812dc3A010C7d01b50e0d17dc79C8"
curl localhost:8001/balance/0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266
curl localhost:8001/tx/<hash de una transfer, de los logs del deployer>
```

Adentro de la red de compose la API llega a la chain como `http://anvil:8545`: **los servicios se
encuentran por nombre**. Desde tu máquina, anvil está en `localhost:8546`.

## 4. Mandar transacciones

```bash
docker compose run --rm deployer python transfer.py --to 2 --amount 3              # 3 QNT: cuenta 0 -> 2
docker compose run --rm deployer python transfer.py --from 1 --to 4 --amount 2.5   # cuenta 1 -> 4
docker compose run --rm deployer python transfer.py --to 5 --amount 1.5 --eth      # ETH nativo
```

Las cuentas son índices (0–9) de la lista de anvil, o una dirección `0x...`. Cada transfer aparece
en segundos en los logs del indexer.

## 5. Consultar la base

Por SQL:

```bash
docker compose exec postgres psql -U quant -d chain
```

```sql
SELECT count(*), max(number) FROM blocks;                         -- cuántos bloques indexó

SELECT block_number, from_address, to_address,
       round(value / 1e18, 4) AS qnt
FROM transfers ORDER BY block_number;                             -- todos los Transfer

SELECT address, round(balance / 1e18, 4) AS qnt
FROM balances ORDER BY balance DESC;                              -- saldos reconstruidos desde los eventos
\q
```

Los saldos de la vista `balances` salen **solo de los eventos**, y coinciden con `balanceOf` del
contrato (comparar con `/token/...?holder=` de la chain-api). Así funcionan los exploradores tipo
Etherscan: indexan eventos en una base.

Por la web: **<http://localhost:8088>**. Sistema *PostgreSQL*, servidor `postgres`, usuario `quant`,
contraseña `quant`, base `chain`.

## 6. Romper cosas a propósito

```bash
docker compose restart anvil       # la chain vuelve a cero
docker compose logs indexer        # "stored block N is not on this chain (...): starting over"
docker compose up deployer         # volver a deployar el token (misma dirección)
```

El indexer detecta que el último bloque que guardó ya no existe en la chain, igual que ante un
**reorg**, y re-indexa. Si se reinicia Postgres (`docker compose restart postgres`), el indexer se
reconecta solo y completa los bloques que se perdió.

## 7. Apagar

```bash
docker compose down                # apaga y borra los containers; la base (volumen pgdata) queda
docker compose down -v             # también borra los volúmenes: empezar de cero
```

Si un puerto está ocupado en tu máquina, cambiarlo sin tocar el compose, con un archivo `.env` al
lado de `docker-compose.yml`: `ANVIL_PORT=18545`, `CHAIN_API_PORT=18001` o `ADMINER_PORT=18088`.

> Las cuentas de anvil son públicas (las mismas en todas las máquinas del mundo): **nunca** mandar
> fondos reales a esas direcciones ni usar sus claves fuera de una chain local.

## Problemas comunes

| Síntoma | Causa / solución |
|---|---|
| `Cannot connect to the Docker daemon` | Docker Desktop no está abierto |
| `ports are not available` | otro programa usa ese puerto: definirlo en `.env` (sección 7) |
| `transfer.py`: `QuantToken is not deployed yet` | anvil se reinició: `docker compose up deployer` |
| la base muestra datos de una chain anterior | `docker compose down -v` y volver a levantar |

## Para la tarea

La tarea de hoy pide reaccionar a cada **swap** de un par. El indexer ya tiene la estructura:
suscripción a bloques nuevos, `get_logs` por topic y escritura en una base. Cambiar el topic de
`Transfer` por el de `Swap(address,uint256,uint256,uint256,uint256,address)` (ver
`modulo-2/clase_3/clase_3_3.py`) y apuntar el indexer a Polygon es el primer paso. Para probar el
indexer actual contra Polygon real, con el stack levantado (`START_BLOCK=latest`: Polygon tiene ~100 millones
de bloques):

```bash
docker compose stop indexer        # el indexer local y este escribirían en la misma base y se borrarían
docker compose run --rm -e WS_URL=wss://polygon-bor-rpc.publicnode.com -e START_BLOCK=latest indexer
# ~300-400 Transfer por bloque. Ctrl+C para cortar; después, para volver a la chain local:
docker compose start indexer       # detecta que la base es de otra chain y empieza de nuevo
```
