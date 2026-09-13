# LLM Reasoning Proxy

Proxy HTTP asíncrono (FastAPI + httpx) que permite que las VMs **aisladas** del
laboratorio de ScyWare hablen con la API de un LLM (Groq / OpenAI-compatible) **sin
tener salida directa a Internet**.

Es la única grieta controlada hacia el exterior: corre en el **host**
(`somath-server`), no dentro de ninguna VM.

---

## ¿Por qué existe y dónde corre?

El principio de contención del laboratorio exige que **ninguna VM tenga salida a
Internet ni a la LAN**. Pero el agente que corre en la VM-0 necesita razonar con un
LLM. La solución es un proxy que vive en el host, que sí tiene un pie en cada red:

- un pie **dentro** de la red host-only aislada (`vboxnet0`, IP `192.168.56.1`),
  alcanzable por las VMs;
- un pie **fuera**, con salida a Internet hacia `api.groq.com`.

```
┌─────────────── HOST: somath-server ───────────────┐
│                                                     │
│   Proxy FastAPI  ──HTTPS──► api.groq.com (Internet)│
│   (192.168.56.1:8080)                               │
│        ▲                                             │
│        │ HTTP (solo dentro de vboxnet0)             │
│   ═════╪══════════ vboxnet0 (aislada) ═════════════ │
│        │                                             │
│   ┌────┴────┐  ┌─────────┐  ┌─────────┐            │
│   │  VM-0   │  │  VM-1   │  │  VM-2   │  ...         │
│   │ Kali    │  │ víctima │  │ víctima │            │
│   │ .10     │  │ .11     │  │ .12     │            │
│   │ SIN NET │  │ SIN NET │  │ SIN NET │            │
│   └─────────┘  └─────────┘  └─────────┘            │
└─────────────────────────────────────────────────────┘
```

> **Importante:** `192.168.56.1` es la IP del **host** en la interfaz host-only
> `vboxnet0`. Solo el host puede bindear esa IP, por eso el proxy **no** va dentro de
> una VM. Si intentaras arrancarlo dentro de la VM-0, uvicorn fallaría con
> `Cannot assign requested address`.

---

## Cómo protege la API key

La credencial real de Groq **nunca sale del host**. Las VMs usan una key dummy; el
proxy la descarta y la reemplaza por la real antes de reenviar:

```python
headers["authorization"] = f"Bearer {GROQ_API_KEY}"   # key REAL, solo en el host
```

Así, aunque el agente-gusano se replique a otras VMs o alguien capture el tráfico
interno de `vboxnet0`, la credencial de Groq queda protegida.

---

## Endpoints

| Método | Ruta | Descripción |
|---|---|---|
| `GET` | `/health` | Diagnóstico. Devuelve `{status, target, key_configured}`. |
| `*` | `/v1/{path}` | Reenvía cualquier petición OpenAI-compatible a `GROQ_TARGET_URL/{path}`. Soporta streaming SSE. |

Ejemplo de mapeo de rutas:

```
VM-0:  POST http://192.168.56.1:8080/v1/chat/completions
proxy: captura /v1/{path}  →  path = "chat/completions"
Groq:  POST https://api.groq.com/openai/v1/chat/completions
```

---

## Configuración (`.env`)

Copia `.env.example` a `.env` y rellena la key:

```dotenv
GROQ_API_KEY=gsk_tu_api_key_real_aqui
```

Variables disponibles (con sus valores por defecto):

| Variable | Default | Descripción |
|---|---|---|
| `GROQ_API_KEY` | *(vacío)* | **Obligatoria.** Key real de Groq. Sin ella, `/v1/*` responde 500. |
| `GROQ_TARGET_URL` | `https://api.groq.com/openai/v1` | API destino. No dupliques el `/v1` (ya lo añade la ruta). |
| `PROXY_HOST` | `192.168.56.1` | IP host-only donde escucha. |
| `PROXY_PORT` | `8080` | Puerto de escucha. |

> El `.env` está en `.gitignore` — la key nunca se commitea.

---

## Puesta en marcha

Se levanta a mano cada vez que se usa el laboratorio:

```bash
cd ~/ScyWare/llm-proxy          # ruta de despliegue en el server
python3 -m venv .venv           # solo la primera vez
source .venv/bin/activate
pip install -r requirements.txt # solo la primera vez
uvicorn main:app --host 192.168.56.1 --port 8080
```

> **Requisito de orden:** `vboxnet0` (y por tanto la IP `192.168.56.1`) debe existir
> **antes** de arrancar el proxy; si no, uvicorn falla con
> `Cannot assign requested address`. Levanta la red host-only primero.

Para dejarlo corriendo en segundo plano durante la sesión:

```bash
uvicorn main:app --host 192.168.56.1 --port 8080 &
# o, para ver/guardar los logs:
uvicorn main:app --host 192.168.56.1 --port 8080 > proxy.log 2>&1 &
```

---

## Firewall del host (contención)

El proxy solo tiene sentido junto a las reglas que aíslan las VMs. En el host:

```bash
# Bloquear forwarding desde vboxnet0 hacia cualquier otra interfaz (LAN/WAN)
sudo iptables -A FORWARD -i vboxnet0 -j DROP

# Permitir solo el puerto del proxy dentro de la red host-only
sudo iptables -A INPUT -i vboxnet0 -p tcp --dport 8080 -j ACCEPT

# Persistir
sudo apt-get install -y iptables-persistent
sudo netfilter-persistent save
```

---

## Cómo lo usa la VM-0

La VM-0 apunta su cliente LLM al proxy y usa una key dummy:

```python
from langchain_groq import ChatGroq

llm = ChatGroq(
    model=settings.LLM_MODEL,               # p.ej. "openai/gpt-oss-20b"
    temperature=0.4,
    api_key=settings.GROQ_API_KEY,          # "proxy_dummy_key" — la real vive en el host
    base_url=settings.LLM_PROXY_URL,        # "http://192.168.56.1:8080/v1"
)
```

> Si tu versión de `langchain-groq` ignora `base_url`, exporta también
> `GROQ_API_BASE="http://192.168.56.1:8080/v1"`. Confírmalo mirando los logs del
> proxy: cada request loguea `Proxying POST -> ...`.

---

## Verificación

Desde la **VM-0** (`192.168.56.10`), con el laboratorio arriba:

```bash
ping -c1 192.168.56.1                        # responde (el host)
curl http://192.168.56.1:8080/health         # {"status":"ok","key_configured":true}
curl http://192.168.56.1:8080/v1/models      # lista de modelos reales de Groq
ping -c1 8.8.8.8                             # DEBE fallar (contención OK)
```

Test de una completion real:

```bash
curl http://192.168.56.1:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"openai/gpt-oss-20b","messages":[{"role":"user","content":"ping"}]}'
```

---

## Troubleshooting

| Síntoma | Causa | Solución |
|---|---|---|
| `Cannot assign requested address` al arrancar | `vboxnet0` no existe todavía (no tiene la `.1`) | Levanta la red host-only antes que el proxy. |
| `/v1/*` responde `500 GROQ_API_KEY no configurada` | Falta la key en el entorno | Rellena `.env` con `GROQ_API_KEY`. |
| Las peticiones de la VM-0 no aparecen en los logs | langchain no está usando el proxy | Exporta `GROQ_API_BASE`; revisa `base_url`. |
| `502 Error interno en proxy LLM` | El host no llega a `api.groq.com` | Verifica salida a Internet **del host** y `GROQ_TARGET_URL`. |
| Doble `/v1/v1/...` en el destino | `GROQ_TARGET_URL` termina en `/v1` y además llega `/v1/...` | Deja `GROQ_TARGET_URL` en `.../openai/v1` (la ruta ya aporta el segundo segmento correcto). |

---

## Archivos del proyecto

```
llm-proxy/
├── main.py              # el proxy FastAPI
├── requirements.txt     # fastapi, uvicorn, httpx, python-dotenv
├── .env.example         # plantilla de configuración
└── .env                 # key real (ignorado por git)
```
