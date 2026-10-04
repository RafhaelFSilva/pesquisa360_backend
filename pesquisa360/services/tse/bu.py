"""Boletim de Urna (BU): fonte analitica por secao.

STATUS: PENDENTE. O BU e binario ASN.1 BER (comprovado). A decodificacao so
e feita com a especificacao oficial `bu.asn1` do TSE; este modulo nao embute
estrutura alguma do BU nem interpreta bytes por tentativa. A extracao de
votos por candidato sera escrita depois de a especificacao oficial ter sido
lida -- ate la, nada e persistido em nivel de secao.
"""

from __future__ import annotations

from pathlib import Path


def sniff(data: bytes) -> dict:
    """Identifica o formato pelo cabecalho TLV (X.690), sem interpretar o conteudo."""
    info = {"bytes": len(data), "json": data[:1] in (b"{", b"["), "asn1_ber_sequence": False}
    if not info["json"] and len(data) > 2 and data[0] == 0x30:
        first = data[1]
        if first < 0x80:
            header, length = 2, first
        else:
            n = first & 0x7F
            header, length = 2 + n, int.from_bytes(data[2:2 + n], "big")
        info["asn1_ber_sequence"] = header + length == len(data)
    return info


def decode_with_official_spec(data: bytes, spec_path: Path) -> dict:
    """Decodifica um BU com o `bu.asn1` oficial, pelo procedimento do exemplo do TSE.

    Envelope generico e, dentro dele, a entidade do boletim. Exige `asn1tools`,
    dependencia opcional que so e importada aqui.
    """
    import asn1tools

    spec = asn1tools.compile_files(str(spec_path), codec="ber")
    envelope = spec.decode("EntidadeEnvelopeGenerico", data)
    return spec.decode("EntidadeBoletimUrna", envelope["conteudo"])
