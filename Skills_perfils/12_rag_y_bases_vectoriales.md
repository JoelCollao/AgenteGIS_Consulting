# RAG y bases vectoriales
**Requisito(s) del perfil:** D2 — RAG y bases de datos vectoriales.
**Estado en mi proyecto:** ❌ (🔍 el adaptador Foundry soportaría `file_search`/`azure_ai_search` gestionados si el agente los tiene configurados en el portal; no verificable desde el repo)

## 1. Qué pide el perfil, en concreto
Construir y evaluar un pipeline de recuperación: ingesta, chunking, embeddings, índice vectorial/híbrido, reranking, citas y métricas de calidad.

## 2. Qué tengo hoy (evidencia)
- 0 coincidencias de `embedding`, `pgvector`, `vector(`, `qdrant`, `chroma`, `faiss`.
- `src/infrastructure/llm/azure_agent.py` (docstring y `_normalize`) → ignora items de herramientas gestionadas (RAG del servicio). Infraestructura lista para RAG gestionado, sin RAG propio.
- Ventaja: ya tengo **PostgreSQL + PostGIS** → añadir `pgvector` es natural y permite **búsqueda híbrida espacial + semántica** (diferenciador).

## 3. Brechas
Todo el pipeline.

## 4. Qué implementar (tareas concretas)
Caso de uso en el WebGIS: preguntas sobre **normativa urbana, manuales técnicos y fichas de proyectos**, filtrables por zona ("¿qué dice la ordenanza sobre retiros en el distrito X?").

| ID | Tarea | Archivos | Criterio de terminado | Esfuerzo |
|---|---|---|---|---|
| RAG1 | Migración: `CREATE EXTENSION vector`; tabla `doc_chunks(id, doc_id, content, embedding vector(1536), tsv tsvector, metadata jsonb, geom geometry)`; índices HNSW + GIN + GIST | `scripts/migrations/005_rag.sql` | `EXPLAIN` usa HNSW | S |
| RAG2 | Ingesta: PDF → texto (Azure Document Intelligence o `pypdf`), chunking por secciones ~800 tokens con 10–15% overlap, embeddings `text-embedding-3-small` por lotes | `scripts/ingest_docs.py`, `src/infrastructure/rag/` | 1 corpus indexado, idempotente por hash | M |
| RAG3 | Puerto `IRetriever` + adaptador pgvector con **búsqueda híbrida** (BM25 por `ts_rank` + coseno, fusión RRF) y filtro espacial `ST_Intersects` | `src/domain/ports/iretriever.py`, `src/infrastructure/rag/pgvector_retriever.py` | Tests con fixtures | M |
| RAG4 | Skill `search_documents` en el registro (function calling) que devuelve chunks con `doc_id`/página para citar | `skills/data/`, `system_prompts.py` | Respuesta con citas `[doc, p.]` | S |
| RAG5 | Reranking opcional (Azure AI Search semantic ranker o cross-encoder) | — | +X en nDCG@5 | M |
| RAG6 | Evaluación: 30 preguntas doradas → recall@5, MRR; generación: groundedness/relevance con `azure-ai-evaluation` | `tests/evals/rag/` | Umbrales en CI | M |
| RAG7 | Alternativa gestionada: Azure AI Search como segundo adaptador de `IRetriever` o `azure_ai_search` en el agente Foundry | — | Comparativa documentada | M |

## 5. Teoría que debo dominar para la entrevista
- RAG = recuperar contexto relevante y fundamentar la respuesta; reduce alucinaciones, no las elimina.
- Chunking: tamaño vs precisión; por estructura (títulos) > por tamaño fijo; overlap; metadatos para filtrar.
- Embeddings: dimensión, normalización, coseno; re-embebido al cambiar de modelo.
- Índices: HNSW (recall alto, más memoria) vs IVFFlat; parámetros `ef_search`, `m`.
- Híbrida: léxica para códigos/nombres exactos (artículos de norma, números de cuenta) + semántica; fusión RRF.
- Reranking con cross-encoder sobre top-k.
- Evaluación separada: recuperación (recall@k, MRR, nDCG) y generación (groundedness, relevance, citas correctas).
- Seguridad en banca: filtrar por permisos del usuario en la consulta (security trimming), PII en documentos, inyección indirecta desde documentos.
- pgvector vs Azure AI Search: el primero simplifica (una BD, joins con PostGIS); el segundo da semantic ranker, escalado y conectores gestionados.

## 6. Preguntas probables y respuesta guía
- *¿Cómo sabes que tu RAG funciona?* Dataset dorado + métricas de recuperación y generación en CI, más feedback de usuarios.
- *¿Por qué híbrida?* Las consultas bancarias tienen términos exactos (códigos de producto) donde lo semántico falla.
- *¿Cómo evitas que el RAG muestre documentos a quien no debe?* Filtros por ACL en la consulta, no después del LLM.

## 7. Cómo lo demuestro
Pregunta en el chat con respuesta citada + mapa resaltando la zona del documento + reporte de recall@5.

## 8. Recursos
- https://github.com/pgvector/pgvector
- https://learn.microsoft.com/azure/search/hybrid-search-overview
- https://learn.microsoft.com/azure/ai-foundry/agents/how-to/tools/file-search
- https://learn.microsoft.com/azure/ai-foundry/how-to/develop/evaluate-sdk
