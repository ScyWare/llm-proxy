import os
import logging
from fastapi import FastAPI, Request, Response
from fastapi.responses import StreamingResponse
import httpx
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger("llm_proxy")

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GROQ_TARGET_URL = os.getenv("GROQ_TARGET_URL", "https://api.groq.com/openai/v1")
HOST = os.getenv("PROXY_HOST", "192.168.56.1")
PORT = int(os.getenv("PROXY_PORT", "8080"))

app = FastAPI(
    title="LLM Reasoning Proxy",
    description="Proxy HTTP para redirigir peticiones de VM aisladas hacia la API de Groq/OpenAI.",
)

client = httpx.AsyncClient(timeout=120.0)


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "target": GROQ_TARGET_URL,
        "key_configured": bool(GROQ_API_KEY),
    }


@app.api_route(
    "/v1/{path:path}",
    methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"],
)
async def proxy_v1(path: str, request: Request):
    if not GROQ_API_KEY:
        logger.error("GROQ_API_KEY no configurada en el proxy host!")
        return Response(
            content='{"error": "GROQ_API_KEY no configurada en el servidor proxy"}',
            status_code=500,
            media_type="application/json",
        )

    url = f"{GROQ_TARGET_URL.rstrip('/')}/{path}"
    headers = dict(request.headers)

    # Eliminar header host original y setear la clave de API
    headers.pop("host", None)
    headers.pop("content-length", None)
    headers["authorization"] = f"Bearer {GROQ_API_KEY}"

    body = await request.body()
    logger.info(f"Proxying {request.method} -> {url}")

    try:
        req = client.build_request(
            method=request.method,
            url=url,
            headers=headers,
            content=body,
            params=request.query_params,
        )
        res = await client.send(req, stream=True)

        # Si el response viene como streaming SSE
        if res.headers.get("content-type", "").startswith("text/event-stream"):
            return StreamingResponse(
                res.aiter_raw(),
                status_code=res.status_code,
                headers=dict(res.headers),
                media_type="text/event-stream",
            )
        else:
            content = await res.aread()
            await res.aclose()
            return Response(
                content=content, status_code=res.status_code, headers=dict(res.headers)
            )
    except Exception as e:
        logger.error(f"Error reenviando solicitud a Groq: {e}")
        return Response(
            content=f'{{"error": "Error interno en proxy LLM: {str(e)}"}}',
            status_code=502,
            media_type="application/json",
        )


if __name__ == "__main__":
    import uvicorn

    logger.info(f"Iniciando proxy LLM FastAPI en http://{HOST}:{PORT}")
    uvicorn.run("main:app", host=HOST, port=PORT, reload=False)
