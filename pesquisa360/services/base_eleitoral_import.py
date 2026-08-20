"""Core de importacao da Base Eleitoral.

Este modulo nao conhece FastAPI nem o layout de nenhuma fonte especifica.
Ele recebe registros ja normalizados e cuida de hash, conferencia
resumo x detalhe e persistencia transacional.

    fonte especifica -> adapter -> RegistroTerritorioImportacao[] -> este motor

O adapter da fonte real (TSE/PDF/CSV) pertence a uma fase posterior: sem o
arquivo em maos, inventar colunas, paginas ou regex produziria um parser falso.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass, field, replace
from typing import Iterable, Optional, Sequence

from sqlalchemy.orm import Session

from pesquisa360.db import models


# --- Erros de dominio ---------------------------------------------------------


class ImportacaoBaseEleitoralError(Exception):
    """Erro de dominio da importacao. O router traduz para HTTP."""


class RegistroInvalidoError(ImportacaoBaseEleitoralError):
    """Registro estruturalmente invalido: aborta o lote inteiro."""


class ImportacaoDuplicadaError(ImportacaoBaseEleitoralError):
    """Arquivo com hash ja importado. Nao sobrescreve nada."""

    def __init__(self, hash_arquivo: str, importacao_anterior_id: int, base_eleitoral_id: int):
        self.hash_arquivo = hash_arquivo
        self.importacao_anterior_id = importacao_anterior_id
        self.base_eleitoral_id = base_eleitoral_id
        super().__init__(
            "Arquivo ja importado (hash {}). Importacao anterior: {} na base {}.".format(
                hash_arquivo, importacao_anterior_id, base_eleitoral_id
            )
        )


# --- Normalizacao -------------------------------------------------------------


def normalizar_nome_territorio(valor: Optional[str]) -> str:
    """Chave de matching. O nome humano original nunca e substituido por ela."""
    if valor is None or not isinstance(valor, str):
        return ""
    texto = valor.strip().casefold()
    if not texto:
        return ""
    texto = unicodedata.normalize("NFKD", texto)
    texto = "".join(ch for ch in texto if not unicodedata.combining(ch))
    texto = re.sub(r"[\W_]+", " ", texto, flags=re.UNICODE)
    return re.sub(r"\s+", " ", texto).strip()


_ELEITORADO_ACEITO = re.compile(r"^\d{1,3}(\.\d{3})*$|^\d+$")


def parse_eleitorado(valor) -> Optional[int]:
    """Parsing estrito. Valor invalido levanta erro; nunca vira 0 nem NULL."""
    if valor is None:
        return None
    if isinstance(valor, bool):
        raise RegistroInvalidoError("eleitorado nao pode ser booleano")
    if isinstance(valor, int):
        if valor < 0:
            raise RegistroInvalidoError("eleitorado nao pode ser negativo: {}".format(valor))
        return valor
    if not isinstance(valor, str):
        raise RegistroInvalidoError("eleitorado com tipo nao suportado: {!r}".format(valor))

    texto = valor.strip()
    if not texto:
        return None
    # Aceita apenas inteiro puro ou separador de milhar com ponto (padrao pt-BR).
    if not _ELEITORADO_ACEITO.match(texto):
        raise RegistroInvalidoError("eleitorado em formato nao suportado: {!r}".format(valor))
    return int(texto.replace(".", ""))


def calcular_hash_arquivo(conteudo: bytes) -> str:
    """SHA-256 sobre os bytes originais do arquivo."""
    if not isinstance(conteudo, (bytes, bytearray)):
        raise RegistroInvalidoError("hash exige os bytes originais do arquivo")
    return hashlib.sha256(bytes(conteudo)).hexdigest()


# --- Representacao normalizada ------------------------------------------------


@dataclass(frozen=True)
class RegistroTerritorioImportacao:
    """Territorio ja normalizado por um adapter, pronto para persistencia.

    `chave` identifica o registro dentro do lote e amarra parent/municipio sem
    depender de ids do banco, que ainda nao existem no momento do parsing.
    """

    tipo: str
    nome: str
    chave: str
    parent_chave: Optional[str] = None
    municipio_chave: Optional[str] = None
    codigo: Optional[str] = None
    nome_normalizado: Optional[str] = None
    zona_eleitoral: Optional[int] = None
    numero_secao: Optional[int] = None
    eleitorado_apto: Optional[int] = None
    metadados: dict = field(default_factory=dict)

    def com_nome_normalizado(self) -> "RegistroTerritorioImportacao":
        if self.nome_normalizado:
            return self
        return replace(self, nome_normalizado=normalizar_nome_territorio(self.nome))


@dataclass(frozen=True)
class DivergenciaConferencia:
    """Divergencia entre o valor de resumo e a soma dos detalhes."""

    tipo_divergencia: str
    territorio_chave: str
    territorio: str
    valor_resumo: int
    valor_detalhe: int
    diferenca: int
    origem: str
    territorio_codigo: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "tipo_divergencia": self.tipo_divergencia,
            "territorio_chave": self.territorio_chave,
            "territorio": self.territorio,
            "territorio_codigo": self.territorio_codigo,
            "valor_resumo": self.valor_resumo,
            "valor_detalhe": self.valor_detalhe,
            "diferenca": self.diferenca,
            "origem": self.origem,
            "resolvida": False,
        }


TIPO_DIVERGENCIA_RESUMO_DETALHE = "RESUMO_DETALHE_DIVERGENTE"


# --- Validacao estrutural do lote --------------------------------------------


def validar_estrutura(registros: Sequence[RegistroTerritorioImportacao]) -> None:
    """Falhas aqui sao fatais: abortam o lote inteiro, sem persistir nada."""
    tipos_validos = set(models.TIPOS_TERRITORIO_ELEITORAL)
    chaves = set()
    for registro in registros:
        if not registro.chave:
            raise RegistroInvalidoError("registro sem chave: {!r}".format(registro.nome))
        if registro.chave in chaves:
            raise RegistroInvalidoError("chave duplicada no lote: {!r}".format(registro.chave))
        chaves.add(registro.chave)
        if registro.tipo not in tipos_validos:
            raise RegistroInvalidoError(
                "tipo territorial invalido: {!r}".format(registro.tipo)
            )
        if not (registro.nome or "").strip():
            raise RegistroInvalidoError("registro sem nome: {!r}".format(registro.chave))
        if registro.tipo != "ESTADO" and not registro.parent_chave:
            raise RegistroInvalidoError(
                "apenas ESTADO pode existir sem parent: {!r}".format(registro.chave)
            )
        if registro.tipo == "SECAO" and registro.numero_secao is None:
            raise RegistroInvalidoError(
                "SECAO exige numero_secao: {!r}".format(registro.chave)
            )
        if registro.eleitorado_apto is not None and registro.eleitorado_apto < 0:
            raise RegistroInvalidoError(
                "eleitorado negativo: {!r}".format(registro.chave)
            )

    for registro in registros:
        for campo, referencia in (
            ("parent_chave", registro.parent_chave),
            ("municipio_chave", registro.municipio_chave),
        ):
            if referencia is not None and referencia not in chaves:
                raise RegistroInvalidoError(
                    "{} aponta para chave inexistente no lote: {!r}".format(campo, referencia)
                )

    _detectar_ciclo(registros)


def _detectar_ciclo(registros: Sequence[RegistroTerritorioImportacao]) -> None:
    pai_por_chave = {registro.chave: registro.parent_chave for registro in registros}
    for chave in pai_por_chave:
        visitados = set()
        atual = chave
        while atual is not None:
            if atual in visitados:
                raise RegistroInvalidoError("ciclo na arvore territorial: {!r}".format(chave))
            visitados.add(atual)
            atual = pai_por_chave.get(atual)


# --- Conferencia resumo x detalhe (funcao pura) ------------------------------


def conferir_resumo_detalhe(
    registros: Sequence[RegistroTerritorioImportacao],
) -> list[DivergenciaConferencia]:
    """Compara o valor declarado de um territorio com a soma dos filhos.

    Nao soma, nao arredonda, nao escolhe e nao corrige nada: apenas devolve as
    divergencias encontradas. A conferencia so ocorre quando TODOS os filhos
    declaram valor, pois um detalhe incompleto produziria divergencia falsa.
    """
    filhos: dict[str, list[RegistroTerritorioImportacao]] = {}
    for registro in registros:
        if registro.parent_chave:
            filhos.setdefault(registro.parent_chave, []).append(registro)

    divergencias: list[DivergenciaConferencia] = []
    for registro in registros:
        if registro.eleitorado_apto is None:
            continue
        seus_filhos = filhos.get(registro.chave, [])
        if not seus_filhos:
            continue
        if any(filho.eleitorado_apto is None for filho in seus_filhos):
            continue
        soma_detalhe = sum(filho.eleitorado_apto for filho in seus_filhos)
        if soma_detalhe == registro.eleitorado_apto:
            continue
        divergencias.append(
            DivergenciaConferencia(
                tipo_divergencia=TIPO_DIVERGENCIA_RESUMO_DETALHE,
                territorio_chave=registro.chave,
                territorio=registro.nome,
                territorio_codigo=registro.codigo,
                valor_resumo=registro.eleitorado_apto,
                valor_detalhe=soma_detalhe,
                diferenca=registro.eleitorado_apto - soma_detalhe,
                origem=registro.tipo,
            )
        )
    return divergencias


# --- Persistencia transacional ------------------------------------------------


def _ordenar_por_profundidade(
    registros: Sequence[RegistroTerritorioImportacao],
) -> list[RegistroTerritorioImportacao]:
    """Pais antes dos filhos: o banco exige o parent_id ja existente."""
    pai_por_chave = {registro.chave: registro.parent_chave for registro in registros}

    def profundidade(chave: str) -> int:
        nivel = 0
        atual = pai_por_chave.get(chave)
        while atual is not None:
            nivel += 1
            atual = pai_por_chave.get(atual)
        return nivel

    return sorted(registros, key=lambda registro: (profundidade(registro.chave), registro.chave))


def _verificar_hash_inedito(db: Session, hash_arquivo: Optional[str]) -> None:
    if not hash_arquivo:
        return
    anterior = (
        db.query(models.ImportacaoBaseEleitoral)
        .filter(models.ImportacaoBaseEleitoral.hash_arquivo == hash_arquivo)
        .order_by(models.ImportacaoBaseEleitoral.id)
        .first()
    )
    if anterior is not None:
        raise ImportacaoDuplicadaError(
            hash_arquivo=hash_arquivo,
            importacao_anterior_id=anterior.id,
            base_eleitoral_id=anterior.base_eleitoral_id,
        )


def importar_registros(
    db: Session,
    *,
    base_eleitoral: models.BaseEleitoral,
    registros: Iterable[RegistroTerritorioImportacao],
    arquivo_origem: str,
    executado_por_id: int,
    hash_arquivo: Optional[str] = None,
    conteudo_arquivo: Optional[bytes] = None,
) -> models.ImportacaoBaseEleitoral:
    """Importa um lote de forma atomica.

    Erro estrutural derruba a transacao inteira. Divergencia de negocio NAO e
    erro: ela e persistida e move a base para EM_CONFERENCIA.
    """
    if base_eleitoral.status == "SUBSTITUIDA":
        raise ImportacaoBaseEleitoralError(
            "Base substituida nao aceita nova importacao."
        )

    registros = [registro.com_nome_normalizado() for registro in registros]
    if not registros:
        raise RegistroInvalidoError("lote vazio")

    if hash_arquivo is None and conteudo_arquivo is not None:
        hash_arquivo = calcular_hash_arquivo(conteudo_arquivo)

    try:
        _verificar_hash_inedito(db, hash_arquivo)
        validar_estrutura(registros)
        divergencias = conferir_resumo_detalhe(registros)
        divergentes = {item.territorio_chave for item in divergencias}

        importacao = models.ImportacaoBaseEleitoral(
            base_eleitoral_id=base_eleitoral.id,
            arquivo_origem=arquivo_origem,
            hash_arquivo=hash_arquivo,
            total_linhas=len(registros),
            total_importadas=0,
            total_divergencias=len(divergencias),
            divergencias=[item.to_dict() for item in divergencias],
            executado_por_id=executado_por_id,
        )
        db.add(importacao)
        db.flush()

        id_por_chave: dict[str, int] = {}
        total_importadas = 0
        for registro in _ordenar_por_profundidade(registros):
            divergente = registro.chave in divergentes
            metadados = dict(registro.metadados)
            metadados.update(
                {
                    "arquivo": arquivo_origem,
                    "hash_arquivo": hash_arquivo,
                    "importacao_id": importacao.id,
                    # Liga o territorio a sua divergencia sem depender do nome,
                    # que pode se repetir entre municipios.
                    "chave_importacao": registro.chave,
                }
            )
            if registro.eleitorado_apto is not None:
                # Preserva sempre o valor declarado pela fonte.
                metadados.setdefault("valor_original", registro.eleitorado_apto)

            territorio = models.TerritorioEleitoral(
                base_eleitoral_id=base_eleitoral.id,
                parent_id=id_por_chave.get(registro.parent_chave)
                if registro.parent_chave
                else None,
                municipio_id=id_por_chave.get(registro.municipio_chave)
                if registro.municipio_chave
                else None,
                tipo=registro.tipo,
                codigo=registro.codigo,
                nome=registro.nome,
                nome_normalizado=registro.nome_normalizado,
                zona_eleitoral=registro.zona_eleitoral,
                numero_secao=registro.numero_secao,
                eleitorado_apto=registro.eleitorado_apto,
                eleitorado_apto_origem=registro.eleitorado_apto,
                eleitorado_apto_divergente=divergente,
                status_validacao="EM_CONFERENCIA" if divergente else "IMPORTADA",
                metadados=metadados,
            )
            db.add(territorio)
            db.flush()
            id_por_chave[registro.chave] = territorio.id
            total_importadas += 1

        importacao.total_importadas = total_importadas
        # Zero divergencia nao promove a base: VALIDADA exige acao humana.
        base_eleitoral.status = "EM_CONFERENCIA" if divergencias else "IMPORTADA"
        db.add(base_eleitoral)
        db.commit()
    except Exception:
        db.rollback()
        raise

    db.refresh(importacao)
    return importacao
