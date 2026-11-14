"""Trivio API Principal (FastAPI Application).

Configura:
1. Instância FastAPI com metadados e documentação Swagger/Redoc.
2. Lifespan assíncrono para inicialização e encerramento de conexões de banco de dados.
3. Middlewares:
   - CORS Middleware para integração com Next.js (http://localhost:3000).
   - ProcessTimeMiddleware para telemetria de latência (X-Process-Time-Ms).
4. Tratamento global de exceções (HTTPException, RequestValidationError e Exception não tratada).
5. Rotas de Healthcheck (/health e /api/v1/health).
6. Inclusão dos roteadores REST e WebSocket da API v1.
"""

from contextlib import asynccontextmanager
from datetime import datetime, timezone
import logging
import time
from typing import Any, AsyncGenerator, Dict

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from sqlalchemy import text

from app.config import settings
from app.database import engine, ensure_schema

logger = logging.getLogger("trivio.api")


def configure_logging() -> None:
    """Aplica o LOG_LEVEL do .env aos loggers do Trivio.

    Sem isso o Python usa o handler de último recurso, que só mostra WARNING para
    cima: todo o rastro INFO dos coletores (incluindo as decisões de conformidade,
    robots.txt e recusas de acesso) ficava invisível no terminal do uvicorn.
    """
    level = getattr(logging, settings.LOG_LEVEL.strip().upper(), logging.INFO)
    trivio_logger = logging.getLogger("trivio")
    trivio_logger.setLevel(level)

    if not trivio_logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(
            logging.Formatter("%(levelname)-8s %(name)s: %(message)s")
        )
        trivio_logger.addHandler(handler)

    # Já temos handler próprio; evita duplicar a mensagem via logger raiz.
    trivio_logger.propagate = False


configure_logging()


# ==============================================================================
# 1. Lifespan (Startup e Shutdown Gerenciados)
# ==============================================================================

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Gerencia ciclo de vida da aplicação (inicialização e encerramento limpo)."""
    logger.info("Iniciando Trivio API Engine...")
    start_time = time.perf_counter()

    # Valida conectividade inicial com o banco de dados
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        logger.info("Conexão com banco de dados validada com sucesso.")
        ensure_schema(engine)
    except Exception as exc:
        logger.error(f"Erro ao conectar ao banco de dados durante startup: {exc}")

    elapsed_ms = round((time.perf_counter() - start_time) * 1000, 1)
    logger.info(f"Trivio API inicializada em {elapsed_ms}ms. Ambiente: {settings.ENVIRONMENT}")

    yield

    logger.info("Encerrando Trivio API Engine...")
    engine.dispose()
    logger.info("Conexões de banco finalizadas.")


# ==============================================================================
# 2. Inicialização do App FastAPI
# ==============================================================================

app = FastAPI(
    title=f"{settings.PROJECT_NAME} API",
    version="1.0.0",
    description=(
        "Plataforma de Inteligência em Viagens Intermodais no Brasil. "
        "Compara e conecta Aéreo x Rodoviário x Virtual Interlining na mesma régua analítica."
    ),
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
    debug=settings.DEBUG,
)


# ==============================================================================
# 3. Middlewares
# ==============================================================================

class ProcessTimeMiddleware(BaseHTTPMiddleware):
    """Mede e injeta o tempo de processamento de cada requisição no cabeçalho."""

    async def dispatch(self, request: Request, call_next):
        start_time = time.perf_counter()
        response = await call_next(request)
        process_time_ms = round((time.perf_counter() - start_time) * 1000, 2)
        response.headers["X-Process-Time-Ms"] = str(process_time_ms)
        return response


app.add_middleware(ProcessTimeMiddleware)

# Configuração de CORS para permitir requisições do frontend Next.js
allowed_origins = settings.allowed_origins()

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Process-Time-Ms"],
)


# ==============================================================================
# 4. Tratamento Global de Exceções
# ==============================================================================

@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    """Padroniza respostas de erro HTTP da aplicação (incluindo 404 de roteamento)."""
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": True,
            "status_code": exc.status_code,
            "message": exc.detail,
            "path": request.url.path,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """Formata erros de validação de schemas Pydantic de forma amigável."""
    formatted_errors = []
    for err in exc.errors():
        field_path = " -> ".join(str(loc) for loc in err.get("loc", []))
        formatted_errors.append({
            "field": field_path,
            "message": err.get("msg"),
            "type": err.get("type"),
        })

    return JSONResponse(
        status_code=422,   # literal: o nome da constante mudou entre versões do Starlette
        content={
            "error": True,
            "status_code": 422,
            "message": "Dados da requisição inválidos ou incompletos.",
            "details": formatted_errors,
            "path": request.url.path,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    )


@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Captura exceções não tratadas prevenindo vazamento de dados internos."""
    logger.error(f"Exceção não tratada na rota {request.url.path}: {exc}", exc_info=True)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "error": True,
            "status_code": 500,
            "message": "Ocorreu um erro interno no servidor ao processar sua requisição.",
            "path": request.url.path,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    )


# ==============================================================================
# 5. Endpoints de Healthcheck e Metadados
# ==============================================================================

@app.get("/", tags=["Meta"])
async def root() -> Dict[str, Any]:
    """Endpoint raiz com informações e links da API."""
    return {
        "project": settings.PROJECT_NAME,
        "status": "online",
        "version": "1.0.0",
        "docs": "/docs",
        "environment": settings.ENVIRONMENT,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/health", tags=["Meta"])
@app.get("/api/v1/health", tags=["Meta"])
async def health_check() -> Dict[str, Any]:
    """Healthcheck completo para orquestradores e balanceadores de carga."""
    db_status = "ok"
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:
        logger.warning(f"Healthcheck: falha de conexão com o banco de dados: {exc}")
        db_status = "degraded"

    return {
        "status": "healthy" if db_status == "ok" else "degraded",
        "database": db_status,
        "environment": settings.ENVIRONMENT,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# ==============================================================================
# 6. Roteadores da API v1 (REST & WebSockets)
# ==============================================================================

from app.api.routes import router as api_router
from app.api.websocket import router as ws_router

app.include_router(api_router)
app.include_router(ws_router)


