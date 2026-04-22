# MedVault — host port map (single Docker host)

Run `./scripts/init-federated-network.sh` before any stack that `include`s [docker-compose.base.yml](docker-compose.base.yml) (AMC, CHU, CHAR, UZG, gcp-center).

| Hospital / stack | mongos (host → container 27017) | Mongo Express (host → container 8081) | Notes |
|--------------------|---------------------------------|----------------------------------------|--------|
| **UKL** (full cluster demo) | **27017** | **8081** | Primary demo; configsvr/shard host ports see [hospital-UKL/docker-compose.yml](hospital-UKL/docker-compose.yml) |
| **AMC** | 27117 | 8082 | Env: [hospital-AMC/.env](hospital-AMC/.env) |
| **CHU** | 27217 | 8083 | [hospital-CHU/.env](hospital-CHU/.env) |
| **CHAR** | 27317 | 8084 | [hospital-CHAR/.env](hospital-CHAR/.env) |
| **UZG** | 27417 | 8085 | [hospital-UZG/.env](hospital-UZG/.env) |
| **gcp-center** | 8000 (API) | 8086 (gold UI) | [gcp-center/docker-compose.yml](gcp-center/docker-compose.yml) |
| **mock agents** | — | 9001–9005 | [agents/docker-compose.yml](agents/docker-compose.yml) |


Environment variables: `MONGOS_HOST_PORT`, `UI_PORT` (simplified hospitals); override in each `hospital-*/.env`.

## Suggested startup order (multi-stack demo)

1. From repo root: `./scripts/init-federated-network.sh`
2. `docker compose -f gcp-center/docker-compose.yml up -d`
3. `docker compose -f agents/docker-compose.yml up -d` (for consent webhook demo)
4. `docker compose -f hospital-UKL/docker-compose.yml up -d` (optional; uses 27017 / 8081)
5. Each simplified hospital (AMC, CHU, CHAR, UZG): `docker compose -f hospital-<CODE>/docker-compose.yml up -d`, then run `./hospital-<CODE>/init-shards.sh` once after first bring-up.

**UKL** does not `include` the base file, so it does not require the federated network for a standalone demo.
