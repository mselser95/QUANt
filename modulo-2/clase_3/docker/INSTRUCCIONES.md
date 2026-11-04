# Monitor de arbitraje en vivo (docker compose)

En la clase 5 levantamos un stack con una chain local. Hoy el stack mira la **chain real**
(Polygon mainnet, solo lectura, sin wallet ni API key) y aplica **las fórmulas del notebook**
(`clase_3.ipynb`, secciones 5b, 5d y 6) en cada bloque nuevo, de forma continua.

```
docker/
├── INSTRUCCIONES.md        <- este archivo
├── docker-compose.yml      <- el stack completo (proyecto "quant-arb")
├── .env.example            <- puertos / RPC configurables (opcional)
├── postgres/init.sql       <- las 3 tablas: blocks, pool_states, opportunities
├── collector/collector.py  <- lee los pools en cada bloque y los guarda
├── detector/detector.py    <- evalúa los ciclos de arbitraje con la forma cerrada
└── grafana/                <- datasource + dashboard ya provisionados
```

Antes de la clase: Docker Desktop andando (`docker version`) y, para no depender del wifi,
`docker pull postgres:17-alpine adminer:5 python:3.13-slim grafana/grafana-oss:11.6.0`.

| Servicio | Qué hace | Puerto en tu máquina |
|---|---|---|
| `postgres` | base de datos con el esquema de `postgres/init.sql` | - (solo adentro) |
| `collector` | en cada bloque nuevo de Polygon lee **8 pools en UNA sola llamada** (Multicall3) y guarda una fila por pool | - |
| `detector` | por cada bloque guardado evalúa 30 ciclos que empiezan y terminan en USDC.e y guarda el resultado | - |
| `grafana` | dashboard (sin login) que lee Postgres | `3030` |
| `adminer` | interfaz web para correr SQL a mano | `8089` |

¿Por qué `collector` y `detector` son dos servicios? Cada uno hace **una** cosa y se leen en
5 minutos: uno habla con la blockchain, el otro hace matemática sobre lo que ya está en la
base. Se comunican solo a través de Postgres (la tabla `pool_states` es el contrato entre ambos),
así que se puede reiniciar o cambiar uno sin tocar el otro.

## 1. Levantar todo

```bash
cd modulo-2/clase_3/docker
docker compose up -d --build       # construye las imágenes y levanta todo en segundo plano
docker compose ps                  # a los ~30 s: postgres, collector, detector y grafana "healthy"
```

El `collector` está "healthy" cuando guardó un bloque en el último minuto; el `detector`
espera a que el `collector` esté sano antes de arrancar (`depends_on: service_healthy`).

Si un puerto del host está ocupado, copiar `.env.example` a `.env`, cambiar el puerto y repetir
`docker compose up -d`. También se puede usar otro nodo con `RPC_URL=...` en el `.env`.

## 2. Abrir el dashboard

<http://localhost:3030> (abre directo el dashboard "Arbitrage monitor - Polygon", **sin login**, se refresca cada 5 s).

- **Profitable opportunities, last hour**: cuántos ciclos tuvieron ganancia neta > 0 en la última hora (gris = 0, verde > 0).
- **Latest block / Best gap now / Gas cost**: último bloque guardado, mejor brecha del bloque en bps (rojo < 0, verde > 0) y costo en USD del gas de un arbitraje de 2 patas.
- **WETH / WPOL price per venue**: precio medio en cada DEX. La distancia entre líneas **es** el arbitraje.
- **Gap per cycle**: la brecha `γⁿ·Π(r_out/r_in) − 1` en bps de los 6 mejores ciclos. Por encima de 0 hay oportunidad bruta.
- **Top cycles of the latest block**: dx\*, ganancia bruta, flash loan, gas y neta de los mejores ciclos.
- **Gas price / POL price**: lo que usamos para convertir el gas a USDC.

Con los precios actuales **casi siempre** verás brechas negativas: los dos fees (0.3 % + 0.3 %) cuestan ~60 bps y
el mercado de Polygon está muy arbitrado. Eso es una conclusión, no un bug. Las oportunidades aparecen en
saltos de precio (mirar el panel después de un movimiento fuerte de ETH).

Para correr SQL a mano: <http://localhost:8089> (Sistema `PostgreSQL`, servidor `postgres`, usuario `quant`, contraseña `quant`, base `arb`).

## 3. Consultas SQL

Desde la terminal (o pegando el SQL en Adminer):

```bash
cd modulo-2/clase_3/docker
# cuántas filas hay (crecen con cada bloque: ~1 bloque cada 2 s, 8 pools por bloque, 30 ciclos por bloque)
docker compose exec -T postgres psql -U quant arb -c "SELECT (SELECT count(*) FROM blocks) AS blocks, (SELECT count(*) FROM pool_states) AS pool_states, (SELECT count(*) FROM opportunities) AS opportunities"
```

```bash
# último precio de cada pool (USD del token que no es USDC.e)
docker compose exec -T postgres psql -U quant arb -c "SELECT dex, pair, round(price_usd, 4) AS price_usd, kind, tick FROM pool_states WHERE block_number = (SELECT max(block_number) FROM pool_states) ORDER BY pair, dex"
```

```bash
# mejores ciclos del último bloque
docker compose exec -T postgres psql -U quant arb -c "SELECT cycle, round(gap_bps::numeric, 1) AS gap_bps, round(dx_star_usdc::numeric) AS dx_star, round(net_usdc::numeric, 2) AS net_usdc, profitable FROM opportunities WHERE block_number = (SELECT max(block_number) FROM opportunities) ORDER BY gap_bps DESC LIMIT 8"
```

```bash
# las oportunidades rentables (net > 0) de la última hora; vacío si el mercado estuvo tranquilo
docker compose exec -T postgres psql -U quant arb -c "SELECT block_number, cycle, round(gap_bps::numeric, 1) AS gap_bps, round(dx_star_usdc::numeric) AS dx_star, round(gross_usdc::numeric, 2) AS gross, round(net_usdc::numeric, 2) AS net FROM opportunities WHERE profitable AND block_time > now() - interval '1 hour' ORDER BY net_usdc DESC LIMIT 10"
```

```bash
# la mejor brecha que vimos por ciclo desde que arrancó (aunque sea negativa)
docker compose exec -T postgres psql -U quant arb -c "SELECT cycle, round(max(gap_bps)::numeric, 1) AS best_gap_bps FROM opportunities GROUP BY cycle ORDER BY best_gap_bps DESC LIMIT 5"
```

## 4. Cómo se relacionan los números con el notebook

| Notebook | Dónde está en el stack |
|---|---|
| `getAmountOut` con 997/1000 (`univ2.py`) | `swap_out()` en `detector.py`, con `gamma = 1 - fee_ppm/1e6` por pata |
| `virtual_pool` (sección 5d, plegar el ciclo en un pool equivalente `E_in`, `E_out`) | `fold()` en `detector.py` |
| `dx* = (√(γ·E_in·E_out) − E_in)/γ` | `optimal_input()` -> columna `dx_star_usdc` |
| hay oportunidad ⇔ `γⁿ·Π(r_out/r_in) > 1` | `marginal_product()` -> columnas `marginal_product` y `gap_bps = (mp − 1)·10⁴` |
| `cycle_out(dx*) − dx*` | columna `gross_usdc` |
| sección 6: flash loan 0.05 % y gas ~350k × precio del gas × precio del POL | columnas `flash_fee_usdc`, `gas_usdc` -> `net_usdc = gross − flash − gas` |

Detalles que conviene saber:

- **Reservas**: `pool_states.reserve0/reserve1` están en unidades crudas (USDC.e tiene 6 decimales, WETH y WPOL 18); el detector las divide por `10^decimales`.
- **Pools V3**: guardamos la liquidez `L` y `sqrtPriceX96` (`slot0`), y las **reservas virtuales** `x = L/√P`, `y = L·√P`. Con ellas un pool V3 se ve como un pool V2 y entra en la misma fórmula, con `γ = 1 − 500/10⁶` (0.05 %) o `1 − 3000/10⁶` (0.3 %).
- **Aproximación "en rango"** (importante): para las patas V3, `dx*` solo es válido mientras el trade **no saque el precio del tick range actual**. Si `dx*` es grande, el trade real cruzaría ticks y la liquidez cambiaría. Tomar `dx*` de una pata V3 como una cota superior, no como un tamaño ejecutable.
- **Ciclos**: 2 patas entre DEXs para WETH/USDC.e (Sushi, Quick, V3 0.05 %, V3 0.3 %: 12 ciclos) y para WPOL/USDC.e (Sushi, Quick: 2 ciclos), y los triangulares USDC.e→WETH→WPOL→USDC.e y el inverso con cada pata en Sushi o Quick (16 ciclos). Total: 30.
- **Gas**: 350 000 unidades para 2 patas, +100 000 por pata extra (supuesto del curso), por el precio del gas del bloque y el precio del POL (pool QuickSwap WPOL/USDC.e).
- Si no hay oportunidad (`marginal_product ≤ 1`) se guardan `dx_star`, `gross`, `flash_fee` y `net` en 0 y `profitable = false`.
- Esto es **solo cotización**: no se manda ninguna transacción. No incluye MEV, latencia ni slippage entre bloques.

## 5. Apagar y limpiar

```bash
docker compose logs -f collector detector   # (Ctrl+C para salir) qué está haciendo cada servicio
docker compose down                         # apaga; los datos quedan en el volumen pgdata
docker compose down -v                      # apaga Y borra la base (empezar de cero)
```

## 6. Problemas comunes

- **`port is already allocated`**: cambiar `GRAFANA_PORT` o `ADMINER_PORT` en `.env` (ver `.env.example`) y repetir `docker compose up -d`.
- **El `collector` queda "unhealthy" o `connection problem` en los logs**: el nodo público (`publicnode`) está lento o limitó las llamadas. El collector reintenta solo cada 5 s. Probar otro endpoint: `RPC_URL=https://polygon-rpc.com` en `.env` y `docker compose up -d`.
- **Dashboard vacío al principio**: esperar ~30 s; los paneles muestran los últimos 15 minutos y recién empiezan a llenarse. Si sigue vacío: `docker compose ps` y ver que `collector` y `detector` estén `healthy`.
- **`docker compose ps` muestra `detector` en "Created" por mucho tiempo**: espera al `collector`; mirar `docker compose logs collector`.
- **Cambié el esquema (`init.sql`) y no pasa nada**: `init.sql` corre solo cuando el volumen es nuevo; hacer `docker compose down -v` y levantar de nuevo.
