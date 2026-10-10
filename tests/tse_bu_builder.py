"""Monta Boletins de Urna SINTETICOS para os testes, com o schema oficial de 2026.

Os bytes sao gerados pelo mesmo `bu-2026.asn1` que o decoder usa (codec BER):
nada aqui inventa estrutura. Servem a cenarios controlados (A -> B -> A,
conferencia 100 + 200 + 300). O contrato com o TSE e provado pelos boletins
OFICIAIS reais em `tests/fixtures_tse/bu/`.
"""

from __future__ import annotations

import hashlib
import json

from pesquisa360.services.tse.bu_decoder import _spec

NOMINAL, BRANCO, NULO, LEGENDA = 1, 2, 3, 4
MAJORITARIO, PROPORCIONAL = 1, 2
SIMULADO_FASE, OFICIAL_FASE, TREINAMENTO_FASE = 1, 2, 3


def cargo(codigo: int, votos: list[tuple], *, tipo: int = PROPORCIONAL, comparecimento: int = 0,
          ordem: int = 1) -> dict:
    """`votos`: (NOMINAL, qtd, partido, numero) | (LEGENDA, qtd, partido) | (BRANCO|NULO, qtd)."""
    votaveis = []
    for i, voto in enumerate(votos, start=1):
        item = {"tipoVoto": voto[0], "quantidadeVotos": voto[1], "ordemGeracaoHash": i,
                "hash": hashlib.sha512(f"{codigo}:{i}".encode()).digest()}
        if voto[0] == NOMINAL:
            item["identificacaoVotavel"] = {"partido": voto[2], "codigo": voto[3]}
        elif voto[0] == LEGENDA:
            item["identificacaoVotavel"] = {"partido": voto[2], "codigo": voto[2]}
        votaveis.append(item)
    return {"tipoCargo": tipo, "qtdComparecimento": comparecimento,
            "totaisVotosCargo": [{"codigoCargo": ("cargoConstitucional", codigo),
                                  "ordemImpressao": ordem, "votosVotaveis": votaveis}]}


def montar_bu(*, secao: int, municipio: int = 6050, zona: int = 2, local: int = 1, pleito: int = 17801,
              fase: int = SIMULADO_FASE, eleicoes: dict[int, list[dict]] | None = None,
              aptos: int = 300, comparecimento: int = 250, emissao: str = "20260929T170500",
              versao: str = "10.23.0.0 - Teste", tipo_envelope: int = 1,
              secao_do_envelope: int | None = None, seguranca: dict | None = None) -> bytes:
    """Arquivo `*-bu.dat` completo (envelope + boletim). `eleicoes`: id -> lista de `cargo(...)`."""
    spec = _spec()

    def ident(numero: int) -> dict:
        return {"municipioZona": {"municipio": municipio, "zona": zona}, "local": local,
                "secao": numero}

    cabecalho = {"dataGeracao": emissao, "idEleitoral": ("idPleito", pleito)}
    resultados = []
    for id_eleicao, cargos in (eleicoes or {}).items():
        for c in cargos:
            c["qtdComparecimento"] = c["qtdComparecimento"] or comparecimento
        resultados.append({
            "idEleicao": id_eleicao, "qtdEleitoresAptos": aptos, "qtdEleitoresAptosSecao": aptos,
            "qtdEleitoresAptosTTE": 0, "resultadosVotacao": cargos,
            "ultimoHashVotosVotavel": b"\x00" * 64, "assinaturaUltimoHashVotosVotavel": b"\x01" * 64})
    boletim = spec.encode("EntidadeBoletimUrna", {
        "cabecalho": cabecalho, "fase": fase,
        "urna": {
            "tipoUrna": 1, "versaoVotacao": versao,
            "correspondenciaResultado": {
                "identificacao": ("identificacaoSecaoEleitoral", ident(secao)),
                "carga": {"numeroInternoUrna": 1234567, "numeroSerieFC": b"\x00\x00\x00\x01",
                          "identificadorGeradorMidia": {"nome": "TESTE", "serialCertificadoTPM": "A1",
                                                        "serialInstalacao": "B2"},
                          "dataHoraCarga": "20260920T101500",
                          "codigoCarga": "123456789012345678901234"}},
            "tipoArquivo": 1, "numeroSerieFV": b"\x00\x00\x00\x02"},
        "identificacaoSecao": ident(secao),
        "dataHoraEmissao": emissao,
        "dadosSecaoSA": ("dadosSecao", {"dataHoraAbertura": "20260929T080000",
                                        "dataHoraEncerramento": "20260929T170000"}),
        "qtdEleitoresCompareceram": comparecimento,
        "resultadosVotacaoPorEleicao": resultados,
        "historicoCodigosCarga": ["123456789012345678901234"],
    })
    envelope = {"cabecalho": cabecalho, "fase": fase,
                "identificacao": ("identificacaoSecaoEleitoral",
                                  ident(secao if secao_do_envelope is None else secao_do_envelope)),
                "tipoEnvelope": tipo_envelope, "conteudo": bytes(boletim)}
    if seguranca is not None:
        envelope["seguranca"] = seguranca
    return bytes(spec.encode("EntidadeEnvelopeGenerico", envelope))


def hash_ea18(conteudo: bytes) -> str:
    """Hash de diretorio no formato do EA18 (hexadecimal); derivado do arquivo so para o teste."""
    return hashlib.sha256(conteudo).hexdigest() + "3d"


def ea18(conteudo: bytes | None, nome: str, *, fase: str = "s", situacao_hash: str = "Totalizado",
         hash_: str | None = None, idg: str = "1") -> dict:
    """EA18 de uma secao apontando para o BU `conteudo` (ou sem arquivo, se None)."""
    hashes = [{"arq": []}] if conteudo is None else [{
        "hash": hash_ or hash_ea18(conteudo), "dr": "29/09/2026", "hr": "17:30:00",
        "st": situacao_hash,
        "arq": [{"nm": nome, "tp": "bu"}, {"nm": nome.replace("-bu.dat", "-rdv.dat"), "tp": "rdv"}]}]
    return {"dg": "29/09/2026", "hg": "18:00:00", "idg": idg, "f": fase, "st": "Totalizada",
            "hashes": hashes}


def corpo(doc) -> bytes:
    return doc if isinstance(doc, bytes) else json.dumps(doc, ensure_ascii=False).encode("utf-8")
