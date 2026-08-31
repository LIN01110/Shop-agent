# Server

FastAPI backend for the RAG shopping assistant.

## Run

```powershell
cd D:\RAG
pip install -r server\requirements.txt
.\scripts\run_server.ps1
```

## Endpoints

- `GET /health`
- `POST /chat` with SSE response

The first scaffold uses `data/sample_products.json` as a local searchable store. The `VectorStore` interface is already in place so the local store can be replaced by Chroma without changing the Agent flow.
