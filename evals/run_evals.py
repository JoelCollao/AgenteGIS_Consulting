"""
Evals de comportamiento del agente
===================================
Corre cada caso de `cases.yaml` por el grafo completo (IntentRouter → agente
→ tools) con el LLM REAL configurado y el backend simulado con
httpx.MockTransport. Mide si el modelo elige la tool y los argumentos
correctos; no evalúa el SQL (eso es del backend).

    python evals/run_evals.py            # todos
    python evals/run_evals.py conteo     # solo los ids que contienen "conteo"

Sale con código 1 si algún caso falla. No forma parte de `pytest` porque
llama a un modelo de verdad (latencia y costo).
"""

import asyncio
import json
import sys
from pathlib import Path

import httpx
import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
load_dotenv(ROOT / ".env")

from agent_service.clients.backend import BackendClient  # noqa: E402
from agent_service.orchestrator.langgraph_flow import orchestrator  # noqa: E402
from agent_service.tools.base import set_backend_client  # noqa: E402


def _match(expected, actual) -> bool:
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(_match(v, actual.get(k)) for k, v in expected.items())
    if isinstance(expected, str) and isinstance(actual, str):
        return expected.lower() == actual.lower()
    if isinstance(expected, (int, float)) and isinstance(actual, (int, float)):
        return float(expected) == float(actual)
    return expected == actual


async def _run_case(case: dict, routes: dict) -> tuple[bool, str]:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.url.path, json.loads(request.content or b"{}")))
        body = routes.get(request.url.path)
        return httpx.Response(200 if body else 404, json=body or {"detail": "Not Found"})

    set_backend_client(BackendClient("http://backend-simulado", transport=httpx.MockTransport(handler)))
    result = await orchestrator.arun({"message": case["message"], "context": case.get("context", {})})

    expect = case["expect"]
    executed = [t["name"] for t in result.get("tools_executed", [])]
    problems = []
    if sorted(executed) != sorted(expect.get("tools", [])):
        problems.append(f"tools={executed} esperado={expect.get('tools', [])}")

    # Argumentos: se reconstruyen de lo que llegó al backend
    by_tool = {
        "/api/v1/analisis/conteo": "count_features", "/api/v1/analisis/buffer": "buffer_analysis",
        "/api/v1/analisis/filtro": "filter_features", "/api/v1/analisis/estadisticas": "statistics",
        "/api/v1/analisis/distancia": "distance", "/api/v1/analisis/interseccion": "intersect",
    }
    sent = {by_tool.get(path, path): body for path, body in calls}
    for tool, args in (expect.get("args") or {}).items():
        if not _match(args, sent.get(tool)):
            problems.append(f"args {tool}={sent.get(tool)} esperado⊇{args}")

    reply = (result.get("reply") or "").lower()
    for fragment in expect.get("reply_contains", []):
        if fragment.lower() not in reply:
            problems.append(f"respuesta sin '{fragment}'")

    return not problems, "; ".join(problems)


def main() -> int:
    data = yaml.safe_load((Path(__file__).parent / "cases.yaml").read_text(encoding="utf-8"))
    routes = data.get("backend", {})
    cases = data["cases"]
    filtro = sys.argv[1] if len(sys.argv) > 1 else ""
    failed = ran = 0
    for case in cases:
        if filtro and filtro not in case["id"]:
            continue
        ok, detail = asyncio.run(_run_case(case, routes))
        ran += 1
        failed += not ok
        print(f"{'PASS' if ok else 'FAIL'}  {case['id']}" + (f"  — {detail}" if detail else ""))
    print(f"\n{ran - failed}/{ran} OK")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
