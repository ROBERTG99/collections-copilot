# 0009. ElasticMQ, MinIO and WireMock versus LocalStack

- Status: accepted
- Date: 2026-10-16

## Context

Tests, the demo and `make verify` must run offline with no keys (spec §6), and `make up` must start the full stack on macOS or Linux. The application talks to three external services that need local doubles: SQS for the work queues, S3 for uploads, and Twilio's REST API for SMS (spec §7.4). Each one sits behind an interface whose real adapter takes an endpoint URL: `SqsQueueClient` (`src/collections_copilot/integrations/queue/sqs.py:45-58`), `S3ObjectStore` (`src/collections_copilot/integrations/storage/s3.py`) and `TwilioSmsSender` with `CC_TWILIO_BASE_URL` (`src/collections_copilot/integrations/sms/twilio.py`). Images reach the cloud container only through a registry mirror; direct Docker Hub pulls are rate-limited and quay.io is blocked (spec Appendix A). Other AWS dependencies already have doubles: dev-oidc for Cognito, and moto for KMS in unit tests (`tests/unit/integrations/test_kms_tokens.py`).

## Decision

- One single-purpose double per service in `compose.yaml`, each with its own healthcheck:
  - ElasticMQ (`softwaremill/elasticmq-native`, `compose.yaml:63-72`) with `cc-tasks`, `cc-replies` and their DLQs declared in `deploy/elasticmq.conf`, redrive included, and `sqs-limits = strict`.
  - MinIO (`chainguard/minio` pinned by digest, `compose.yaml:74-93`); the bucket is created by the application (`cc storage init`), not by `mc` (docs/DECISIONS.md, 2026-10-16).
  - WireMock 3.13.2 for Twilio (`compose.yaml:95-105`) with the mappings in `deploy/wiremock/mappings/twilio-messages.json`. The debtor simulator reads its request journal and posts signed callbacks and replies (`apps/debtor-sim/src/debtor_sim/main.py:115`).
- The real adapters are used against the doubles; only the endpoint changes. Production settings refuse a localhost endpoint, and an empty one means the AWS default (`src/collections_copilot/config.py:100-104`, `:129-138`).
- Contract tests pin the adapters to the doubles and to a second reference: the S3 contract runs against MinIO and moto (`tests/contract/test_s3_contract.py:19-23`, moto in server mode in `tests/contract/conftest.py`), and the Twilio contract runs against WireMock and checks signatures with the official `twilio` library (`tests/contract/test_twilio_contract.py:1-2`). The SQS contract runs against ElasticMQ and moto (`tests/contract/test_sqs_contract.py:14-16`).

## Alternatives considered

- **LocalStack for SQS and S3.** Rejected. It does not remove a double: Twilio is not an AWS service, so WireMock and the debtor simulator stay either way, and Cognito and KMS already have their own doubles. It would put two unrelated services into one large container that emulates many more, so a failure or an upgrade in one affects both, and the healthchecks could no longer tell queue readiness (the ElasticMQ check waits for `cc-tasks`) from storage readiness. The single-purpose images are pinned one by one, and their versions are recorded where a pull failed (docs/DECISIONS.md, 2026-10-16, MinIO and WireMock rows).
- **moto in server mode as the compose double for SQS and S3.** Rejected. It keeps state in memory, so uploads would be lost on every restart of the stack, while MinIO keeps a volume (`miniodata`); and the queues with their redrive would have to be created by code at start-up instead of being declared in `deploy/elasticmq.conf`. moto stays the second reference in unit and contract tests.

## Consequences

- Each double is a separate implementation of the provider's API, and the contract tests show where it differs from the second reference. A behaviour that only AWS has, for example a limit ElasticMQ does not enforce, can still pass locally; `sqs-limits = strict` narrows that gap for SQS.
- Three images to pin and update instead of one. MinIO and WireMock versions were chosen by what the mirror could pull (docs/DECISIONS.md, 2026-10-16), and Dependabot's `docker-compose` ecosystem tracks them (`.github/dependabot.yml`).
- The doubles are reachable only on `127.0.0.1` (docs/DECISIONS.md, 2026-10-16, compose ports), but the compose network itself is not isolated from the internet (docs/KNOWN_LIMITATIONS.md, Local network isolation).
- If the stack needs more AWS services locally than SQS and S3, a combined emulator should be reconsidered.
