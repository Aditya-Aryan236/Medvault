## Medvault

### Docs map (start here)

- **Ports / host URLs**: [`PORTS.md`](PORTS.md)
- **End-to-end demo runbook (ingestion → central aggregates → consent withdrawal → sharding/replica/crash)**: [`demo/README.md`](demo/README.md)
- **Central stack details (gcp-center only)**: [`gcp-center/README.md`](gcp-center/README.md)
- **Per-hospital stacks**: `hospital-*/docker-compose.yml` (+ each `hospital-*/.env`)

### Environment variables (important)
This repo uses **two different env mechanisms** in Docker Compose:

- **Compose-time interpolation**: values like `${MONGO_VERSION}` in `docker-compose.yml` are resolved by Docker Compose from **your shell** and from one or more **`--env-file`** files.
- **Container runtime env**: values in `env_file:` are injected **into the container**, but **do not** affect `${...}` interpolation.

To make `${...}` variables in all stacks resolve correctly, always run Compose with **both**:

- the repo-wide `.shared.env`
- the stack-specific `.env` (e.g. `hospital-CHU/.env`)

### Run a hospital stack (recommended)
From the repo root:

```bash
docker compose \
  --env-file ./.shared.env \
  --env-file hospital-CHU/.env \
  -f hospital-CHU/docker-compose.yml \
  up -d --build
```

Repeat for any hospital by swapping the folder name:

```bash
docker compose --env-file ./.shared.env --env-file hospital-AMC/.env  -f hospital-AMC/docker-compose.yml  up -d --build
docker compose --env-file ./.shared.env --env-file hospital-CHAR/.env -f hospital-CHAR/docker-compose.yml up -d --build
docker compose --env-file ./.shared.env --env-file hospital-CHU/.env  -f hospital-CHU/docker-compose.yml  up -d --build
docker compose --env-file ./.shared.env --env-file hospital-UKL/.env  -f hospital-UKL/docker-compose.yml  up -d --build
docker compose --env-file ./.shared.env --env-file hospital-UZG/.env  -f hospital-UZG/docker-compose.yml  up -d --build
```

### Run the central (GCP) stack
From the repo root:

```bash
docker compose \
  --env-file ./.shared.env \
  --env-file gcp-center/.env \
  -f gcp-center/docker-compose.yml \
  up -d --build
```