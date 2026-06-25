# Task Brief: OPS-BFF-CORS-COMPOSE-DEFAULT

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: docker-compose CORS default must include dev/staging FE sslip.io origins
- Status: todo
- Owner: Claude
- Reviewer: Codex
- Next: Assignment created

## Summary
docker-compose.yml 的 PANTHEON_BFF_CORS_ORIGINS 預設只含 lovable 網域,缺 dev FE origin https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io(staging 同理),任何沒帶 override 的 docker compose up 就讓瀏覽器跨域被擋(FE 載不到 BFF)。把 dev(及 staging)FE 的 sslip.io origin 加進 compose 預設,與 deploy_nonprod_vm.sh 的 canonical origin 對齊。
