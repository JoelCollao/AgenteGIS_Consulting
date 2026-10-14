# Cloud, Docker y CI/CD
**Requisito(s) del perfil:** R6 (Cloud Azure/AWS, Docker, CI/CD), F4 (despliegue y operación en la nube).
**Estado en mi proyecto:** 🟡

## 1. Qué pide el perfil, en concreto
Imágenes reproducibles y seguras, pipeline que construye, prueba, escanea y despliega por entornos, IaC, identidades sin secretos y capacidad de rollback.

## 2. Qué tengo hoy (evidencia)
- `docker/Dockerfile` → multi-stage, `python:3.11-slim`, usuario `appuser` no root, `HEALTHCHECK` a `/api/v1/health`, `$PORT`.
- `docker/docker-compose.yml` → PostGIS + Redis + API.
- `.github/workflows/ci-cd-lint.yaml` → pylint `--fail-under=9.5` en push/PR. **Sin tests.**
- `.github/workflows/ci-cd-deploy.yaml` → OIDC (`azure/login@v2`, `id-token: write`), `az acr login` sin admin, tag por SHA, `az containerapp update`, health check post-deploy. **Inactivo** (`workflow_dispatch`); el propio archivo dice que el deploy real es Seenode.
- `infra/provision-container-apps.ps1:142-144` → Container App con identidad `--system-assigned`, réplicas 0–3. `infra/setup-github-oidc.ps1` → federación GitHub.
- `requirements.lock`, `scripts/check_secrets.py`, `.githooks/pre-commit`.
- `src/infrastructure/llm/entra.py` → `DefaultAzureCredential` con Managed Identity en Azure.

## 3. Brechas
- Tests y escaneo (Trivy/`pip-audit`) fuera del pipeline.
- IaC imperativo (PowerShell), no declarativo.
- Un solo entorno; sin aprobaciones; sin estrategia de rollback documentada.
- Secretos (DB, JWT) por variables, no Key Vault references.
- Instalación usa `requirements.txt`, no el lock; `azure-ai-projects>=2.0.0b4` y `openai>=2.8.0` sin techo (builds no reproducibles).
- Dockerfile copia `.env.example` a la imagen (inocuo, pero innecesario).

## 4. Qué implementar (tareas concretas)
| ID | Tarea | Archivos | Criterio de terminado | Esfuerzo |
|---|---|---|---|---|
| C1 | Job `test` (pytest + cobertura) y `pip-audit` en CI | `ci-cd-lint.yaml` | Check obligatorio en PR | S |
| C2 | Trivy sobre la imagen; fallar en CRITICAL | `ci-cd-deploy.yaml` | Reporte SARIF en GitHub Security | S |
| C3 | Bicep: Log Analytics, App Insights, ACR, Container Apps env, Container App (MI, `min 1`, probes liveness/readiness), Key Vault + role assignments (`AcrPull`, `Key Vault Secrets User`, `Cognitive Services OpenAI User`) | `infra/main.bicep`, `infra/params.dev.json`, `params.prod.json` | `az deployment group what-if` limpio | M |
| C4 | Entornos GitHub `dev`/`prod` con aprobación; deploy por revisión con tráfico 0% → smoke → 100% | `ci-cd-deploy.yaml` | Rollback = activar revisión anterior (1 comando documentado) | M |
| C5 | Pinear versiones y usar `requirements.lock` en Docker | `Dockerfile`, `requirements.txt` | Build reproducible | S |
| C6 | Decidir y documentar: Seenode (actual) vs Azure (objetivo); para la entrevista, tener el despliegue Azure funcionando al menos en dev | README | URL de dev viva | M |

## 5. Teoría que debo dominar para la entrevista
- Multi-stage, capas cacheables, imágenes slim/distroless, no root, escaneo.
- OIDC federado (sin secretos de larga duración) — ya lo tengo diseñado.
- Managed Identity + RBAC de mínimo privilegio; Key Vault references en Container Apps.
- Revisiones de Container Apps, blue/green y canary por pesos de tráfico; KEDA.
- Probes: liveness (proceso vivo) vs readiness (dependencias) vs startup.
- Promoción de la **misma imagen** entre entornos (build once, deploy many).

## 6. Preguntas probables y respuesta guía
- *¿Cómo haces rollback?* Imagen inmutable por SHA; activar revisión anterior y mover tráfico; migraciones de BD compatibles hacia atrás (expand/contract).
- *¿Cómo autentica tu app con Azure OpenAI sin API key?* Managed Identity + rol `Cognitive Services OpenAI User`; en local `az login` (`entra.py` salta IMDS fuera de Azure).

## 7. Cómo lo demuestro
Pipeline verde con lint → tests → Trivy → deploy dev → aprobación → prod; `what-if` de Bicep; demo de rollback.

## 8. Recursos
- https://learn.microsoft.com/azure/container-apps/revisions
- https://learn.microsoft.com/azure/container-apps/manage-secrets
- https://docs.github.com/actions/deployment/security-hardening-your-deployments/configuring-openid-connect-in-azure
- https://learn.microsoft.com/azure/azure-resource-manager/bicep/
