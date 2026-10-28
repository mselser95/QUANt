# Docker en vivo: `chain-api`

Un servicio chico que **envuelve web3.py en una API HTTP**: lo mismo que hicimos en `web3.ipynb`
(último bloque, balances, gas, transacciones, contratos), pero corriendo dentro de un container
y consultable con `curl`, el navegador o cualquier otro programa.

```
docker/
├── INSTRUCCIONES.md      <- este archivo
└── chain-api/
    ├── app.py            <- la API (FastAPI + web3.py)
    ├── requirements.txt  <- dependencias con versión fija
    ├── Dockerfile        <- la receta de la imagen
    └── .dockerignore     <- lo que NO se copia a la imagen
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

## Problemas comunes

| Síntoma | Causa / solución |
|---|---|
| `Cannot connect to the Docker daemon` | Docker Desktop no está abierto |
| `port is already allocated` | el puerto está en uso: usar otro, `-p 8010:8000` |
| `Conflict. The container name "/chain-api" is already in use` | `docker rm -f chain-api` y volver a correr |
| `/tx/...` da `503 ... 403 Forbidden` | el nodo público no sirve ese receipt (hash inexistente o muy viejo): probar con una tx reciente de <https://polygonscan.com> |
| `503 RPC error` en todo | sin internet o el RPC público caído: probar `-e RPC_URL=https://polygon.drpc.org` |

---

**Por qué Python 3.13 y no 3.15 como las notebooks:** `ckzg`, una dependencia de web3.py, todavía no
publica wheels de Linux para 3.15; compilarla necesitaría un compilador de C dentro de la imagen.
