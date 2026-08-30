"""Ambiente de execucao da aplicacao (ADR-038).

O projeto nao tinha nenhuma nocao de ambiente -- nem `APP_ENV`, nem
`ENVIRONMENT`, nem `DEBUG`. Esta e a UNICA variavel introduzida, e existe por um
motivo especifico: decidir se a documentacao automatica (Swagger/ReDoc/OpenAPI)
e registrada.

    APP_ENV=development   (default)  -> /docs, /redoc e /openapi.json existem
    APP_ENV=production               -> as tres rotas nao sao registradas

O default e `development` de proposito: hoje a aplicacao serve a documentacao
sem configuracao nenhuma, e mudar isso silenciosamente tiraria o Swagger de todo
desenvolvedor. Em compensacao, **o deploy precisa definir `APP_ENV=production`
explicitamente** -- nao ha inferencia por hostname, IP, dominio, porta ou
presenca de Docker, porque adivinhar ambiente e como se descobre em producao que
o palpite estava errado.
"""
from __future__ import annotations

import os

DESENVOLVIMENTO = "development"
PRODUCAO = "production"

# Grafias aceitas para producao. Qualquer outro valor (inclusive vazio ou
# ausente) e tratado como desenvolvimento.
_VALORES_PRODUCAO = {"production", "producao", "prod"}


def nome_ambiente() -> str:
    """Valor normalizado de `APP_ENV`. Ausente/vazio => development."""
    bruto = (os.getenv("APP_ENV") or "").strip().casefold()
    if not bruto:
        return DESENVOLVIMENTO
    return PRODUCAO if bruto in _VALORES_PRODUCAO else bruto


def is_producao() -> bool:
    return nome_ambiente() == PRODUCAO


def opcoes_documentacao(producao: bool | None = None) -> dict[str, str | None]:
    """Kwargs de documentacao para o construtor do FastAPI.

    Em producao as tres viram `None` JUNTAS. Desligar so `/docs` e `/redoc`
    deixaria `/openapi.json` publico -- e com ele a estrutura inteira da API
    continua enumeravel, que e exatamente o que se quer evitar.
    """
    if producao is None:
        producao = is_producao()
    if producao:
        return {"docs_url": None, "redoc_url": None, "openapi_url": None}
    return {"docs_url": "/docs", "redoc_url": "/redoc", "openapi_url": "/openapi.json"}
