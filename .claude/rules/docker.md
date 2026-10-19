---
paths:
  - "docker/**"
  - "web/Dockerfile"
  - "compose*.yaml"
---
# Docker rules

- Non-root users (UID 10001), a `HEALTHCHECK` in every image, slim bases, no `apt-get` (Appendix A).
- Declare `ARG HTTPS_PROXY` in every stage that downloads packages; compose builds use `network: host`.
- Host ports stay in 8300–8399; every service has a healthcheck and dependents use `condition: service_healthy`.
- Containers stay offline at runtime; no secrets baked into images.
