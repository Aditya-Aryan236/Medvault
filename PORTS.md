# MedVault — host port map (single Docker host)

Run `./scripts/init-federated-network.sh` before any stack that attaches to the external federated bridge network (`net-federated-bridge`). In this repo that is: **gcp-center**, and all **hospital-*** stacks (because they all include [docker-compose.base.yml](docker-compose.base.yml) and/or run an agent on the bridge).

| Hospital / stack | mongos (host → container 27017) | Mongo Express UI (host → container 8081) | Agent API (host → container 9000) | Notes |
|--------------------|---------------------------------|------------------------------------------|----------------------------------|--------|
| **UKL** (full cluster demo) | **27017** | **8081** | **9005** | Full sharded cluster. Also publishes configsvr ports **27019**, **28019**, **29019**. See [hospital-UKL/docker-compose.yml](hospital-UKL/docker-compose.yml). |
| **AMC** | **27117** | 8082 *(disabled by default)* | **9001** | Ports come from [hospital-AMC/.env](hospital-AMC/.env). |
| **CHU** | **27217** | 8083 *(disabled by default)* | **9002** | Ports come from [hospital-CHU/.env](hospital-CHU/.env). |
| **CHAR** | **27317** | 8084 *(disabled by default)* | **9003** | Ports come from [hospital-CHAR/.env](hospital-CHAR/.env). |
| **UZG** | **27417** | 8085 *(disabled by default)* | **9004** | Ports come from [hospital-UZG/.env](hospital-UZG/.env). |
| **gcp-center** | — | **8086** (gold UI) | — | Also publishes **8000** (central API). Port comes from `GOLD_UI_PORT` in `gcp-center/.env`. See [gcp-center/docker-compose.yml](gcp-center/docker-compose.yml). |
| **hospital agents** | — | — | **9001–9005** | Agent APIs run inside each `hospital-*/docker-compose.yml` stack (e.g. UKL binds 9005). |


Environment variables:

- **Hospitals (AMC/CHU/CHAR/UZG)**: `MONGOS_HOST_PORT`, `UI_PORT` (Mongo Express is currently commented out), `BASICAUTH_USERNAME`, `BASICAUTH_PASSWORD` in each `hospital-*/.env`.
- **gcp-center**: `GOLD_UI_PORT`, `MONGO_VERSION`, `MONGO_EXPRESS_VERSION`, `BASICAUTH_USERNAME`, `BASICAUTH_PASSWORD` in `gcp-center/.env`.

Important: variables like `${MONGO_VERSION}` used in `docker-compose.yml` are resolved by **Compose interpolation**, so run stacks with both env files loaded:
`--env-file ./.shared.env --env-file <stack>/.env` (see below).

## Suggested startup order (multi-stack demo)

1. From repo root: `./scripts/init-federated-network.sh`
2. `docker compose --env-file ./.shared.env --env-file gcp-center/.env -f gcp-center/docker-compose.yml up -d --build`
3. `docker compose --env-file ./.shared.env --env-file hospital-UKL/.env -f hospital-UKL/docker-compose.yml up -d --build` (optional; uses 27017 / 8081 / 9005)
5. Each simplified hospital (AMC, CHU, CHAR, UZG):
   - Bring up: `docker compose --env-file ./.shared.env --env-file hospital-<CODE>/.env -f hospital-<CODE>/docker-compose.yml up -d --build`
   - Shard init runs automatically via the `init-shards-cluster` one-shot container. If you need to re-run it: `docker compose -f hospital-<CODE>/docker-compose.yml up init-shards-cluster`

**UKL** includes the base file and also runs an agent on `net-federated-bridge`, so for the full multi-stack demo it expects the federated network to exist. If you want UKL standalone (no central / no agents), comment out the `agent-ukl` service and remove `net-federated-bridge` usage in the UKL compose.
