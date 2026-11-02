# TOE Kotak Module is its own tree

Canonical remote, when published: https://github.com/IX7TechnologyResearch/TOE_Kotak_Module

This tree is the Kotak Neo Trade Order Executor (Redis intent consumer, Neo UAT
orders, order-feed fills, execution publisher, port 8005).

## Layout

- Own `Dockerfile` (build from this repo root only)
- Zero source coupling to QuantEngine, RMS, or `toe_dhan`
- Runtime contracts below
- Desk orchestration is docker compose in the QuantEngine repo via `TOE_KOTAK_MODULE_DIR`

## Runtime contract

- Intent stream: `stream:order_intents_kotak_sandbox`
- Execution stream: `stream:executions` with `broker_id: kotak_sandbox`
- Health: `GET /toe/live`, `GET /toe/ready`
- Infra: `REDIS_URL`, `MONGO_URI`
- Broker: Neo `environment=uat` via `NEO_*` / `TOE_KOTAK_*`
- Desk build: `TOE_KOTAK_MODULE_DIR` pointing at this clone (same pattern as `TOE_DHAN_MODULE_DIR=/opt/TOE_Dhan_Module`)
- Port: `8005` (`TOE_KOTAK_PORT`)

The service refuses `NEO_ENVIRONMENT` other than `uat`.
