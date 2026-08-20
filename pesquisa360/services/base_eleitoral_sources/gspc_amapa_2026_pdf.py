"""Adapter da fonte "Eleitorado Apto (Amapa 2026).pdf" — GSPC.

Escopo desta fase: SOMENTE os agregados de bairro/localidade. Locais de votacao,
escolas, enderecos e secoes existem no PDF e sao deliberadamente ignorados.

Layout observado (40 paginas, texto nativo):

    pagina 1   capa: "578.157 ELEITORES APTOS" / "NO ESTADO AMAPA"
    pagina 2   tabela-resumo (Macapa, Santana e totais municipais)
    paginas 3+ detalhamento municipal, um municipio por bloco de paginas

Dentro do detalhamento:

    MUNICIPIO DE AMAPA - ZONA 1 - 7.601 ELEITORES        <- cabecalho do municipio
    1 - SETE MANGUEIRAS - 2.956 ELEITORES                <- agregado desejado
    01 - E.E. VIDAL DE NEGREIROS - 09 SECOES             <- local de votacao (ignorar)
    SECAO 12 17 18 ... ELEITORES 2.478                   <- secoes (ignorar)
    BAIRROS 08 - LOCAIS DE VOTACAO - 10 / ...            <- rodape, usado como conferencia

Dois detalhes do arquivo real que quebram parsers ingenuos:

1. O separador e travessao (EN DASH), nao hifen, e as duas formas convivem na
   mesma linha: "23 - MACARANDUBA - 1 ELEITOR".
2. O unico bairro com 1 eleitor usa "ELEITOR" no singular. Exigir "ELEITORES"
   perde exatamente 1 dos 198 registros.

O texto extraido pelo pypdf NAO respeita a ordem visual: o cabecalho do
municipio pode aparecer depois dos bairros da mesma pagina. Por isso o municipio
e atribuido POR PAGINA, e nao pela ultima linha vista.

A pagina 2 nao produz registros: suas linhas de resumo nao tem os travessoes do
cabecalho agregado, entao Macapa e Santana entram uma unica vez, pelo
detalhamento.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional, Sequence

from pesquisa360.services.base_eleitoral_import import (
    ImportacaoBaseEleitoralError,
    RegistroTerritorioImportacao,
    normalizar_nome_territorio,
    parse_eleitorado,
)

NOME_FONTE = "GSPC"
CLASSIFICACAO_AGREGADO = "BAIRRO_LOCALIDADE"

# Travessao, travessao longo e hifen convivem na mesma fonte.
_TRACO = r"[–—-]"

# Agregado desejado: "1 - SETE MANGUEIRAS - 2.956 ELEITORES".
# O sufixo ELEITOR(ES) e o valor puramente numerico sao o que separa este
# cabecalho de uma linha de escola como
# "01 - E.P.G. MATAO III - 01 SECAO / 286 ELEITORES".
RE_BAIRRO = re.compile(
    r"^\s*(\d{1,3})\s*" + _TRACO + r"\s*(.+?)\s*" + _TRACO + r"\s*([\d.]+)\s*ELEITOR(?:ES)?\s*$",
    re.IGNORECASE,
)

RE_MUNICIPIO = re.compile(
    r"MUNIC[IÍ]PIO\s+DE\s+(.+?)\s*-\s*ZONAS?\s+(.+?)\s*-\s*([\d.]+)\s*ELEITOR(?:ES)?",
    re.IGNORECASE,
)

# Rodape de fechamento do municipio: "BAIRROS 08 - LOCAIS DE VOTACAO ...".
RE_RODAPE_BAIRROS = re.compile(r"BAIRROS\s+(\d+)", re.IGNORECASE)

RE_ESTADO_TOTAL = re.compile(r"([\d.]+)\s*ELEITORES\s+APTOS", re.IGNORECASE)
RE_ESTADO_NOME = re.compile(r"NO\s+ESTADO\s+(.+?)\s*$", re.IGNORECASE)

RE_ZONA_UNICA = re.compile(r"^\s*(\d{1,3})\s*$")


class FonteGspcError(ImportacaoBaseEleitoralError):
    """Erro de leitura da fonte GSPC, com localizacao no arquivo."""


@dataclass(frozen=True)
class _Municipio:
    nome: str
    pagina: int
    zona_texto: str
    eleitorado_declarado: int
    texto_original: str


def extrair_texto_paginas(pdf_bytes: bytes) -> list[str]:
    """Texto nativo por pagina. Sem OCR: o arquivo real tem texto extraivel."""
    if not isinstance(pdf_bytes, (bytes, bytearray)):
        raise FonteGspcError("a fonte deve ser fornecida como bytes")
    if not pdf_bytes:
        raise FonteGspcError("arquivo vazio")

    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover - dependencia declarada em pyproject
        raise FonteGspcError("pypdf e necessario para ler a fonte GSPC") from exc

    from io import BytesIO

    try:
        leitor = PdfReader(BytesIO(bytes(pdf_bytes)))
        return [(pagina.extract_text() or "") for pagina in leitor.pages]
    except FonteGspcError:
        raise
    except Exception as exc:
        raise FonteGspcError("nao foi possivel ler o PDF: {}".format(exc)) from exc


def _municipio_da_pagina(linhas: Sequence[str], pagina: int) -> Optional[_Municipio]:
    for linha in linhas:
        encontrado = RE_MUNICIPIO.search(linha)
        if not encontrado:
            continue
        nome = encontrado.group(1).strip()
        zona_texto = encontrado.group(2).strip()
        declarado = parse_eleitorado(encontrado.group(3))
        if declarado is None:
            raise FonteGspcError(
                "municipio sem eleitorado declarado na pagina {}: {!r}".format(pagina, linha)
            )
        return _Municipio(
            nome=nome,
            pagina=pagina,
            zona_texto=zona_texto,
            eleitorado_declarado=declarado,
            texto_original=linha.strip(),
        )
    return None


def _zona_unica(zona_texto: str) -> Optional[int]:
    """So preenche zona_eleitoral quando o municipio declara uma unica zona.

    Macapa declara "ZONAS 2 e 10": nesse caso o campo fica nulo e o texto bruto
    permanece nos metadados, em vez de escolher uma das zonas.
    """
    encontrado = RE_ZONA_UNICA.match(zona_texto or "")
    return int(encontrado.group(1)) if encontrado else None


def _chave(*partes) -> str:
    return "|".join(str(parte) for parte in partes)


def parse_paginas(
    paginas: Sequence[str], nome_arquivo: str
) -> list[RegistroTerritorioImportacao]:
    """Converte o texto ja extraido em registros normalizados.

    Separado de `extrair_texto_paginas` para permitir teste sem PDF binario.
    """
    if not paginas:
        raise FonteGspcError("nenhuma pagina para processar")

    estado_nome: Optional[str] = None
    estado_total: Optional[int] = None
    estado_pagina: Optional[int] = None

    municipio_atual: Optional[_Municipio] = None
    municipios: list[_Municipio] = []
    bairros_por_municipio: dict[str, list[RegistroTerritorioImportacao]] = {}
    rodape_por_municipio: dict[str, int] = {}
    ordens_por_municipio: dict[str, list[int]] = {}

    for numero_pagina, texto in enumerate(paginas, start=1):
        linhas = texto.splitlines()

        if estado_total is None:
            for linha in linhas:
                total = RE_ESTADO_TOTAL.search(linha)
                if total:
                    estado_total = parse_eleitorado(total.group(1))
                    estado_pagina = numero_pagina
                nome = RE_ESTADO_NOME.search(linha)
                if nome and estado_nome is None:
                    estado_nome = nome.group(1).strip()

        # Atribuicao por pagina: o cabecalho pode aparecer em qualquer posicao do
        # texto extraido. Pagina sem cabecalho e continuacao do municipio anterior.
        encontrado = _municipio_da_pagina(linhas, numero_pagina)
        if encontrado is not None:
            municipio_atual = encontrado
            municipios.append(encontrado)
            bairros_por_municipio.setdefault(encontrado.nome, [])
            ordens_por_municipio.setdefault(encontrado.nome, [])

        for linha in linhas:
            if municipio_atual is not None:
                rodape = RE_RODAPE_BAIRROS.search(linha)
                if rodape:
                    rodape_por_municipio[municipio_atual.nome] = int(rodape.group(1))

            agregado = RE_BAIRRO.match(linha)
            if not agregado:
                continue
            if municipio_atual is None:
                raise FonteGspcError(
                    "agregado antes de qualquer cabecalho municipal na pagina {}: {!r}".format(
                        numero_pagina, linha.strip()
                    )
                )

            ordem = int(agregado.group(1))
            nome = agregado.group(2).strip()
            bruto = agregado.group(3)
            eleitorado = parse_eleitorado(bruto)
            if not nome:
                raise FonteGspcError(
                    "agregado sem nome na pagina {}: {!r}".format(numero_pagina, linha.strip())
                )
            if eleitorado is None:
                raise FonteGspcError(
                    "agregado sem eleitorado na pagina {}: {!r}".format(
                        numero_pagina, linha.strip()
                    )
                )

            chave_municipio = _chave("MUNICIPIO", normalizar_nome_territorio(municipio_atual.nome))
            registro = RegistroTerritorioImportacao(
                tipo="BAIRRO",
                nome=nome,
                chave=_chave("BAIRRO", normalizar_nome_territorio(municipio_atual.nome), ordem),
                parent_chave=chave_municipio,
                municipio_chave=chave_municipio,
                # A fonte nao traz codigo oficial: a numeracao e ordem de
                # apresentacao e fica apenas nos metadados.
                codigo=None,
                nome_normalizado=normalizar_nome_territorio(nome),
                eleitorado_apto=eleitorado,
                metadados={
                    "origem": nome_arquivo,
                    "classificacao_fonte": CLASSIFICACAO_AGREGADO,
                    "pagina_fonte": numero_pagina,
                    "numero_ordem_fonte": ordem,
                    "texto_original": linha.strip(),
                    "municipio_original": municipio_atual.nome,
                    "eleitorado_original": bruto,
                },
            )
            bairros_por_municipio[municipio_atual.nome].append(registro)
            ordens_por_municipio[municipio_atual.nome].append(ordem)

    if not municipios:
        raise FonteGspcError("nenhum cabecalho municipal encontrado na fonte")
    if estado_nome is None or estado_total is None:
        raise FonteGspcError("total estadual nao encontrado na fonte")

    _validar_contra_a_propria_fonte(
        municipios, bairros_por_municipio, rodape_por_municipio, ordens_por_municipio
    )

    chave_estado = _chave("ESTADO", normalizar_nome_territorio(estado_nome))
    registros: list[RegistroTerritorioImportacao] = [
        RegistroTerritorioImportacao(
            tipo="ESTADO",
            nome=estado_nome,
            chave=chave_estado,
            nome_normalizado=normalizar_nome_territorio(estado_nome),
            eleitorado_apto=estado_total,
            metadados={
                "origem": nome_arquivo,
                "pagina_fonte": estado_pagina,
                "eleitorado_original": estado_total,
                "papel": "PAI_TECNICO",
            },
        )
    ]

    for municipio in municipios:
        chave_municipio = _chave("MUNICIPIO", normalizar_nome_territorio(municipio.nome))
        registros.append(
            RegistroTerritorioImportacao(
                tipo="MUNICIPIO",
                nome=municipio.nome,
                chave=chave_municipio,
                parent_chave=chave_estado,
                nome_normalizado=normalizar_nome_territorio(municipio.nome),
                zona_eleitoral=_zona_unica(municipio.zona_texto),
                eleitorado_apto=municipio.eleitorado_declarado,
                metadados={
                    "origem": nome_arquivo,
                    "pagina_fonte": municipio.pagina,
                    "texto_original": municipio.texto_original,
                    "zona_original": municipio.zona_texto,
                    "papel": "PAI_TECNICO",
                },
            )
        )
        registros.extend(bairros_por_municipio[municipio.nome])

    return registros


def _validar_contra_a_propria_fonte(
    municipios: Sequence[_Municipio],
    bairros_por_municipio: dict,
    rodape_por_municipio: dict,
    ordens_por_municipio: dict,
) -> None:
    """Conferencias estruturais usando o que o proprio documento declara.

    Nao ha tabela de contagens fixa no codigo: o rodape "BAIRROS NN" e a
    numeracao sequencial do proprio PDF sao a referencia. Uma linha capturada
    indevidamente quebraria a sequencia e derrubaria a importacao.
    """
    for municipio in municipios:
        capturados = bairros_por_municipio.get(municipio.nome, [])
        esperado = rodape_por_municipio.get(municipio.nome)
        if esperado is None:
            raise FonteGspcError(
                "municipio {!r} sem rodape 'BAIRROS NN' para conferencia".format(municipio.nome)
            )
        if esperado != len(capturados):
            raise FonteGspcError(
                "municipio {!r}: rodape declara {} agregados, extraidos {}".format(
                    municipio.nome, esperado, len(capturados)
                )
            )
        ordens = sorted(ordens_por_municipio.get(municipio.nome, []))
        if ordens != list(range(1, len(capturados) + 1)):
            raise FonteGspcError(
                "municipio {!r}: numeracao dos agregados nao e sequencial: {}".format(
                    municipio.nome, ordens
                )
            )


def parse_gspc_amapa_2026_bairros(
    pdf_bytes: bytes, nome_arquivo: str
) -> list[RegistroTerritorioImportacao]:
    """Ponto de entrada do adapter.

    Devolve os agregados de bairro/localidade mais os pais tecnicos exigidos pela
    arvore (1 ESTADO + N MUNICIPIOS). Nenhum LOCAL_VOTACAO, SECAO ou LOCALIDADE
    e produzido nesta fase.
    """
    return parse_paginas(extrair_texto_paginas(pdf_bytes), nome_arquivo)


def resumo_extracao(registros: Sequence[RegistroTerritorioImportacao]) -> dict:
    """Preview auditavel: contagens e conferencia declarado x soma, sem persistir."""
    por_tipo: dict[str, int] = {}
    for registro in registros:
        por_tipo[registro.tipo] = por_tipo.get(registro.tipo, 0) + 1

    por_chave = {registro.chave: registro for registro in registros}
    municipios = []
    for registro in registros:
        if registro.tipo != "MUNICIPIO":
            continue
        filhos = [
            item
            for item in registros
            if item.tipo == "BAIRRO" and item.parent_chave == registro.chave
        ]
        soma = sum(item.eleitorado_apto or 0 for item in filhos)
        municipios.append(
            {
                "municipio": registro.nome,
                "pagina": registro.metadados.get("pagina_fonte"),
                "bairros": len(filhos),
                "soma_bairros": soma,
                "declarado": registro.eleitorado_apto,
                "diferenca": (registro.eleitorado_apto or 0) - soma,
                "amostras": [
                    {
                        "nome": item.nome,
                        "eleitorado": item.eleitorado_apto,
                        "pagina": item.metadados.get("pagina_fonte"),
                    }
                    for item in filhos[:3]
                ],
            }
        )

    estado = next((item for item in registros if item.tipo == "ESTADO"), None)
    soma_municipios = sum(
        item.eleitorado_apto or 0 for item in registros if item.tipo == "MUNICIPIO"
    )
    return {
        "por_tipo": por_tipo,
        "total_registros": len(registros),
        "estado": {
            "nome": estado.nome if estado else None,
            "declarado": estado.eleitorado_apto if estado else None,
            "soma_municipios": soma_municipios,
            "diferenca": ((estado.eleitorado_apto or 0) - soma_municipios) if estado else None,
        },
        "municipios": municipios,
        "chaves_unicas": len(por_chave) == len(registros),
    }
