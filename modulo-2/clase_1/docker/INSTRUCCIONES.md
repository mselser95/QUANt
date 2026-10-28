# Docker en vivo: `chain-api`

Un servicio chico que **envuelve web3.py en una API HTTP**: lo mismo que hicimos en `web3.ipynb`
(último bloque, balances, gas, transacciones, contratos), pero corriendo dentro de un container
y consultable con `curl`, el navegador o cualquier otro programa.

Parte 1 (secciones 0–7): **un** container. Parte 2 (sección 8): **varios** containers que trabajan
juntos con `docker compose`: una blockchain local, nuestro token, un indexer y una base de datos.

```
docker/
├── INSTRUCCIONES.md       <- este archivo
├── docker-compose.yml     <- parte 2: el stack completo
├── chain-api/
│   ├── app.py             <- la API (FastAPI + web3.py)
│   ├── requirements.txt   <- dependencias con versión fija
│   ├── Dockerfile         <- la receta de la imagen
│   └── .dockerignore      <- lo que NO se copia a la imagen
├── deployer/
│   ├── QuantToken.json    <- ABI + bytecode de contracts/QuantToken.sol (OpenZeppelin 5.4)
│   ├── deploy.py          <- deploya el token y hace 3 transfers
│   └── transfer.py        <- transfers a mano (QNT o ETH)
└── indexer/
    └── indexer.py         <- guarda cada bloque y cada Transfer ERC-20 en Postgres
```

## 0. Antes de la clase

- Instalar **Docker Desktop** (Mac / Windows) o Docker Engine (Linux): <https://docs.docker.com/get-docker/>
- Verificar: `docker version` (tiene que mostrar *Client* y *Server*)
- Bajar la imagen base para no depender del wifi: `docker pull python:3.13-slim`

## 1. Conceptos en una línea

| Concepto | Qué es |
|---|---|
| **Imagen** | la receta ya "cocinada": sistema operativo mínimo + Python + dependencias + código. No cambia. |
| **Container** | una imagen **corriendo**. Se pueden levantar muchos de la misma imagen. |
| **Dockerfile** | los pasos para construir la imagen. Cada paso es una **capa** cacheada. |
| **Puerto** (`-p 8000:8000`) | conecta un puerto de tu máquina con uno del container. |
| **Variable de entorno** (`-e`) | configuración que se pasa al correr. Así entran los secretos, **nunca** dentro de la imagen. |

## 2. Construir la imagen

```bash
cd modulo-2/clase_1/docker/chain-api
docker build -t chain-api .
```

- `-t chain-api` le pone nombre a la imagen; el `.` es la carpeta con el `Dockerfile`.
- La primera vez tarda (~20 s): baja dependencias. Ver la imagen creada: `docker images chain-api`

**Probar la caché de capas:** cambiar cualquier cosa en `app.py` y volver a correr `docker build -t chain-api .`.
El paso `RUN pip install` aparece como `CACHED`: solo se rehace desde `COPY app.py`. Por eso el
`Dockerfile` copia primero `requirements.txt` y después el código.

## 3. Correr el container

```bash
docker run --rm -d --name chain-api -p 8000:8000 chain-api
```

| Flag | Para qué |
|---|---|
| `-d` | corre en segundo plano (*detached*) |
| `--name chain-api` | nombre para referirnos al container |
| `-p 8000:8000` | puerto 8000 de tu máquina → puerto 8000 del container |
| `--rm` | borra el container cuando se detiene |

Sin configurar nada usa un nodo **público de Polygon** sin API key.

## 4. Consultarlo

Desde la terminal:

```bash
curl localhost:8000/health
curl localhost:8000/block
curl localhost:8000/block/60000000
curl localhost:8000/balance/0x000000000000000000000000000000000000dEaD
curl localhost:8000/gas
curl localhost:8000/price
curl "localhost:8000/price?amount_eth=10"
curl "localhost:8000/token/0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174?holder=0x000000000000000000000000000000000000dEaD"
curl localhost:8000/tx/<hash de una tx>
```

Desde el navegador: **<http://localhost:8000/docs>** (documentación interactiva, botón *Try it out*).

| Endpoint | Qué devuelve | Sección de `web3.ipynb` |
|---|---|---|
| `GET /health` | RPC, chain id y último bloque | 3. Conectarse a un nodo |
| `GET /block` · `GET /block/{n}` | número, hash, timestamp, cantidad de txs, gas usado | 4. Último bloque |
| `GET /balance/{address}` | balance en wei y en POL/ETH | 5. Balances |
| `GET /gas` | gas price en wei y gwei | 6. Transacciones |
| `GET /tx/{hash}` | receipt: bloque, status, from, to, gas, logs | 6c. Receipt |
| `GET /token/{token}?holder=` | nombre, símbolo, decimales, supply y balance de un ERC-20 | 9. Tokens |
| `GET /price?amount_eth=` | cuántos USDC.e da SushiSwap y QuickSwap por N WETH (solo Polygon) | 8. Contratos |

Para comentar en vivo con `/price?amount_eth=10`: el pool de SushiSwap es mucho más chico que el de
QuickSwap, así que por 10 WETH paga bastante menos (**price impact**, lo vemos en la clase 6).

Si algo falla, la API responde un error legible en vez de romperse:
`/balance/hola` → `400 not an address` · RPC caído → `503 RPC error (...)` · tx inexistente → `404`.

## 5. Mirar qué pasa adentro

```bash
docker ps                          # containers corriendo
docker logs -f chain-api           # cada request en vivo (Ctrl+C para salir)
docker exec -it chain-api sh       # una terminal DENTRO del container
    whoami                         #   -> appuser (no corre como root)
    ls /app                        #   -> solo app.py y requirements.txt
    env | grep RPC                 #   -> la configuración que recibió
    exit
docker stats chain-api             # CPU y memoria (Ctrl+C para salir)
```

## 6. Misma imagen, otra configuración

La imagen no cambia: solo cambia la variable de entorno. Levantamos un **segundo** container
apuntando a **Ethereum mainnet**, en otro puerto:

```bash
docker run --rm -d --name eth-api -p 8001:8000 -e RPC_URL=https://ethereum-rpc.publicnode.com chain-api

curl localhost:8001/health      # chain_id 1
curl localhost:8001/token/0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2   # WETH en Ethereum
curl localhost:8001/price       # 400: los pools de /price son de Polygon
```

Con un proveedor propio (Alchemy, Infura...) la URL con la key se pasa **igual**, por `-e` o con un
archivo que no se sube a git:

```bash
echo 'RPC_URL=https://polygon-mainnet.g.alchemy.com/v2/TU_KEY' > .env    # .env está en .dockerignore
docker run --rm -d --name my-api -p 8002:8000 --env-file .env chain-api
```

Nunca `ENV RPC_URL=...con la key...` en el `Dockerfile`: queda dentro de la imagen y cualquiera que
la tenga la lee con `docker history` / `docker inspect`.

## 7. Apagar y limpiar

```bash
docker stop chain-api eth-api      # con --rm, se borran solos al detenerse
docker ps -a                       # verificar que no quedó nada
docker rmi chain-api               # (opcional) borrar la imagen
```

## 8. Parte 2: docker compose, un stack blockchain local

Un `docker-compose.yml` describe **varios** containers, cómo se conectan y en qué orden arrancan.
Se levantan y se apagan todos juntos con un solo comando.

| Servicio | Qué hace | Puerto en tu máquina |
|---|---|---|
| `anvil` | blockchain EVM **local** (Foundry): un bloque cada 2 s y 10 cuentas con 10.000 ETH de prueba | `8546` |
| `deployer` | deploya **QuantToken** (el contrato de Remix de hoy) y hace 3 transfers; después termina | - |
| `postgres` | base de datos | - (solo adentro) |
| `indexer` | escucha cada bloque nuevo por websocket y guarda bloques y eventos `Transfer` en Postgres | - |
| `chain-api` | **la misma imagen de la parte 1**, apuntando a anvil en vez de Polygon | `8001` |
| `adminer` | interfaz web para mirar la base | `8088` |

¿Por qué una chain local? Mandar transacciones sin wallet de testnet, sin faucet y sin esperar:
anvil arranca con cuentas con fondos y **desbloqueadas** (el nodo firma por ellas), así que no
hace falta ninguna private key. Cada vez que se reinicia, la chain vuelve a cero.

### 8.1 Levantar todo

```bash
cd modulo-2/clase_1/docker
docker compose up -d --build       # construye las imágenes y levanta todo en segundo plano
docker compose ps -a               # estado: anvil y postgres "healthy", deployer "Exited (0)"
```

El orden lo define `depends_on` + `healthcheck`: el deployer, el indexer y la chain-api esperan a
que anvil responda; el indexer espera también a Postgres.

### 8.2 Ver qué pasó

```bash
docker compose logs deployer       # dirección del token, las 3 transfers y los balances
docker compose logs -f indexer     # cada bloque y cada Transfer en vivo (Ctrl+C para salir)
```

El token queda siempre en `0x5FbDB2315678afecb367f032d93F642f64180aa3`: la dirección de un contrato
depende solo de quién lo deploya y de su nonce, y en una anvil nueva son siempre los mismos.

### 8.3 Consultar la chain local con la chain-api

```bash
curl localhost:8001/health          # chain_id 31337 = anvil
curl localhost:8001/block
curl "localhost:8001/token/0x5FbDB2315678afecb367f032d93F642f64180aa3?holder=0x70997970C51812dc3A010C7d01b50e0d17dc79C8"
curl localhost:8001/balance/0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266
curl localhost:8001/tx/<hash de una transfer, de los logs del deployer>
```

Adentro de la red de compose la API llega a la chain como `http://anvil:8545`: **los servicios se
encuentran por nombre**. Desde tu máquina, anvil está en `localhost:8546`.

### 8.4 Mandar transacciones

```bash
docker compose run --rm deployer python transfer.py --to 2 --amount 3              # 3 QNT: cuenta 0 -> 2
docker compose run --rm deployer python transfer.py --from 1 --to 4 --amount 2.5   # cuenta 1 -> 4
docker compose run --rm deployer python transfer.py --to 5 --amount 1.5 --eth      # ETH nativo
```

Las cuentas son índices (0–9) de la lista de anvil, o una dirección `0x...`. Cada transfer aparece
en segundos en los logs del indexer.

### 8.5 Consultar la base

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

### 8.6 Romper cosas a propósito

```bash
docker compose restart anvil       # la chain vuelve a cero
docker compose logs indexer        # "stored block N is not on this chain ... re-indexing from 0"
docker compose up deployer         # volver a deployar el token (misma dirección)
```

El indexer detecta que el último bloque que guardó ya no existe en la chain, igual que ante un
**reorg**, y re-indexa. Si se reinicia Postgres (`docker compose restart postgres`), el indexer se
reconecta solo y completa los bloques que se perdió.

### 8.7 Apagar

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
| `port is already allocated` | el puerto está en uso: usar otro, `-p 8010:8000` |
| `Conflict. The container name "/chain-api" is already in use` | `docker rm -f chain-api` y volver a correr |
| `/tx/...` da `503 ... 403 Forbidden` | el nodo público no sirve ese receipt (hash inexistente o muy viejo): probar con una tx reciente de <https://polygonscan.com> |
| `503 RPC error` en todo | sin internet o el RPC público caído: probar `-e RPC_URL=https://polygon.drpc.org` |
| compose: `ports are not available` | otro programa usa ese puerto: definirlo en `.env` (sección 8.7) |
| `transfer.py`: `QuantToken is not deployed yet` | anvil se reinició: `docker compose up deployer` |
| la base muestra datos de una chain anterior | `docker compose down -v` y volver a levantar |

---

**Por qué Python 3.13 y no 3.15 como las notebooks:** `ckzg`, una dependencia de web3.py, todavía no
publica wheels de Linux para 3.15; compilarla necesitaría un compilador de C dentro de la imagen.
