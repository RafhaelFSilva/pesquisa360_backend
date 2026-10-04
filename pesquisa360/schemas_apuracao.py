"""Contratos HTTP da Apuracao Eleitoral (TSE + paineis).

As leituras analiticas devolvem dicionarios ja serializaveis montados em
`services/tse/analytics.py`; aqui ficam os contratos de ESCRITA (paineis) e
os modelos de resposta dos paineis. Nenhum schema aceita `company_id`.
"""

from __future__ import annotations

from datetime import datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Origem = Literal["OFICIAL", "SIMULADO"]
TipoItem = Literal["CARGO", "CANDIDATO", "NOMINATA"]


class PainelItemIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tipo: TipoItem
    cargo_codigo: str = Field(min_length=1, max_length=4)
    sqcand: Optional[str] = Field(default=None, max_length=20)
    partido_numero: Optional[str] = Field(default=None, max_length=5)
    federacao_numero: Optional[str] = Field(default=None, max_length=10)
    ativo: bool = True

    @field_validator("cargo_codigo")
    @classmethod
    def _cargo(cls, value: str) -> str:
        if not value.isdigit():
            raise ValueError("cargo_codigo deve ser numérico")
        return f"{int(value):04d}"

    @model_validator(mode="after")
    def _coerencia(self):
        if self.tipo == "CANDIDATO" and not self.sqcand:
            raise ValueError("Item CANDIDATO exige sqcand")
        if self.tipo != "CANDIDATO" and self.sqcand:
            raise ValueError("sqcand só é aceito em item CANDIDATO")
        if self.partido_numero and self.federacao_numero:
            raise ValueError("Informe partido ou federação, não os dois")
        if self.tipo != "NOMINATA" and (self.partido_numero or self.federacao_numero):
            raise ValueError("Partido/federação só são aceitos em item NOMINATA")
        return self


class PainelIn(BaseModel):
    """Criacao/edicao. O tenant vem do usuario autenticado, nunca do corpo."""

    model_config = ConfigDict(extra="forbid")

    nome: str = Field(min_length=1, max_length=120)
    descricao: Optional[str] = Field(default=None, max_length=2000)
    eleicao_id: int
    uf: str = Field(min_length=2, max_length=2)
    # A ordem dos itens e a ordem da lista.
    itens: List[PainelItemIn] = Field(default_factory=list, max_length=60)

    @field_validator("nome")
    @classmethod
    def _nome(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Nome é obrigatório")
        return value

    @field_validator("uf")
    @classmethod
    def _uf(cls, value: str) -> str:
        return value.lower()


class PainelItemOut(BaseModel):
    id: int
    tipo: TipoItem
    cargo_codigo: str
    sqcand: Optional[str] = None
    partido_numero: Optional[str] = None
    federacao_numero: Optional[str] = None
    ordem: int
    ativo: bool


class PainelResumo(BaseModel):
    id: int
    nome: str
    descricao: Optional[str] = None
    origem: Origem
    pleito: str
    codigo_eleicao: str
    # None quando a eleicao do painel nao esta ingerida nesta base.
    eleicao_id: Optional[int] = None
    eleicao_nome: Optional[str] = None
    uf: str
    total_itens: int
    criado_em: Optional[datetime] = None
    atualizado_em: Optional[datetime] = None


class PainelDetalhe(PainelResumo):
    itens: List[PainelItemOut]
