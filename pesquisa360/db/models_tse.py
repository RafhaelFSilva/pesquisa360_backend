"""Modelos do dominio de Apuracao TSE (ADR-076 a ADR-083).

Dados publicos e GLOBAIS: nenhuma tabela `tse_*` tem `company_id` nem FK
para Projeto/Pesquisa/Coleta. `origem` (OFICIAL | SIMULADO) separa o ambiente
de simulacao do TSE do resultado oficial e faz parte das chaves naturais.

Historico e append-only: `tse_snapshots`, `tse_totalizacoes` e os resultados
nunca sofrem UPDATE; cada totalizacao materialmente diferente e uma linha nova.
"""

from sqlalchemy import (
    JSON, Boolean, CheckConstraint, Column, Date, DateTime, ForeignKey, Index, Integer,
    Numeric, String, Text, UniqueConstraint, text,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from .models import Base, _sql_in

ORIGENS_TSE = ("OFICIAL", "SIMULADO")
TIPOS_ABRANGENCIA_TSE = ("BR", "UF", "MUNICIPIO", "ZONA")


class TseEleicao(Base):
    __tablename__ = "tse_eleicoes"
    __table_args__ = (
        # O codigo de eleicao so e unico dentro de um ambiente do TSE.
        UniqueConstraint("origem", "pleito", "codigo_eleicao", name="uq_tse_eleicoes_natural"),
        CheckConstraint(_sql_in("origem", ORIGENS_TSE), name="ck_tse_eleicoes_origem"),
    )

    id = Column(Integer, primary_key=True)
    origem = Column(String(10), nullable=False)
    ambiente = Column(String(40), nullable=False)
    ciclo = Column(String(20), nullable=False)
    pleito = Column(String(10), nullable=False)
    codigo_eleicao = Column(String(10), nullable=False)
    nome = Column(String(200), nullable=False)
    turno = Column(Integer, nullable=False)
    tipo = Column(String(5), nullable=True)
    data_eleicao = Column(Date, nullable=True)
    codigo_segundo_turno = Column(String(10), nullable=True)
    ativo = Column(Boolean, nullable=False, default=True, server_default=text("true"))
    metadata_json = Column(JSON, nullable=True)
    criado_em = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    atualizado_em = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class TseCargo(Base):
    __tablename__ = "tse_cargos"
    __table_args__ = (UniqueConstraint("codigo", name="uq_tse_cargos_codigo"),)

    id = Column(Integer, primary_key=True)
    codigo = Column(String(4), nullable=False)
    nome = Column(String(100), nullable=False)


class TseAbrangencia(Base):
    """BR, UF, municipio ou zona de uma eleicao.

    `chave` e a identidade natural completa ("uf:ap", "mu:ap:06050",
    "zona:ap:06050:0002"): evita UNIQUE sobre colunas anulaveis e carrega o
    municipio da zona, que o EA20 de zona nao traz no payload.
    """

    __tablename__ = "tse_abrangencias"
    __table_args__ = (
        UniqueConstraint("eleicao_id", "chave", name="uq_tse_abrangencias_chave"),
        CheckConstraint(_sql_in("tipo", TIPOS_ABRANGENCIA_TSE), name="ck_tse_abrangencias_tipo"),
        Index("ix_tse_abrangencias_parent", "parent_id"),
        Index("ix_tse_abrangencias_territorio", "eleicao_id", "uf", "municipio_codigo", "zona"),
    )

    id = Column(Integer, primary_key=True)
    eleicao_id = Column(Integer, ForeignKey("tse_eleicoes.id"), nullable=False)
    tipo = Column(String(10), nullable=False)
    chave = Column(String(40), nullable=False)
    uf = Column(String(2), nullable=True)
    municipio_codigo = Column(String(5), nullable=True)
    municipio_nome = Column(String(120), nullable=True)
    zona = Column(String(4), nullable=True)
    parent_id = Column(Integer, ForeignKey("tse_abrangencias.id"), nullable=True)

    eleicao = relationship("TseEleicao")
    parent = relationship("TseAbrangencia", remote_side=[id])


class TseFederacao(Base):
    __tablename__ = "tse_federacoes"
    __table_args__ = (
        UniqueConstraint("eleicao_id", "numero", name="uq_tse_federacoes_numero"),
    )

    id = Column(Integer, primary_key=True)
    eleicao_id = Column(Integer, ForeignKey("tse_eleicoes.id"), nullable=False)
    numero = Column(String(10), nullable=False)
    sigla = Column(String(60), nullable=False)
    nome = Column(String(200), nullable=False)


class TsePartido(Base):
    __tablename__ = "tse_partidos"
    __table_args__ = (UniqueConstraint("eleicao_id", "numero", name="uq_tse_partidos_numero"),)

    id = Column(Integer, primary_key=True)
    eleicao_id = Column(Integer, ForeignKey("tse_eleicoes.id"), nullable=False)
    numero = Column(String(5), nullable=False)
    sigla = Column(String(40), nullable=False)
    nome = Column(String(200), nullable=False)
    federacao_id = Column(Integer, ForeignKey("tse_federacoes.id"), nullable=True)

    federacao = relationship("TseFederacao")


class TseCandidato(Base):
    """`sqcand` e a identidade oficial; `numero` e chave operacional contextual."""

    __tablename__ = "tse_candidatos"
    __table_args__ = (
        UniqueConstraint("eleicao_id", "sqcand", name="uq_tse_candidatos_sqcand"),
        Index("ix_tse_candidatos_numero", "eleicao_id", "cargo_id", "uf", "numero"),
        Index("ix_tse_candidatos_partido", "partido_id"),
    )

    id = Column(Integer, primary_key=True)
    eleicao_id = Column(Integer, ForeignKey("tse_eleicoes.id"), nullable=False)
    cargo_id = Column(Integer, ForeignKey("tse_cargos.id"), nullable=False)
    uf = Column(String(2), nullable=False)
    sqcand = Column(String(20), nullable=False)
    numero = Column(String(10), nullable=False)
    nome = Column(String(200), nullable=False)
    nome_urna = Column(String(200), nullable=False)
    partido_id = Column(Integer, ForeignKey("tse_partidos.id"), nullable=False)
    criado_em = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    cargo = relationship("TseCargo")
    partido = relationship("TsePartido")


class TseSnapshot(Base):
    """Arquivo oficial exatamente como recebido. (url, sha256) e a idempotencia."""

    __tablename__ = "tse_snapshots"
    __table_args__ = (
        UniqueConstraint("url", "sha256", name="uq_tse_snapshots_url_sha256"),
        CheckConstraint(_sql_in("origem", ORIGENS_TSE), name="ck_tse_snapshots_origem"),
        Index("ix_tse_snapshots_url_capturado", "url", "capturado_em"),
        Index("ix_tse_snapshots_tipo", "origem", "tipo"),
    )

    id = Column(Integer, primary_key=True)
    origem = Column(String(10), nullable=False)
    tipo = Column(String(10), nullable=False)
    url = Column(Text, nullable=False)
    http_status = Column(Integer, nullable=False)
    etag = Column(String(200), nullable=True)
    last_modified = Column(String(60), nullable=True)
    idg = Column(String(20), nullable=True)
    gerado_em = Column(DateTime(timezone=True), nullable=True)
    sha256 = Column(String(64), nullable=False)
    tamanho_bytes = Column(Integer, nullable=False)
    # JSON para os arquivos EA; binarios (BU) ficam fora do banco, em `arquivo_path`.
    payload_json = Column(JSON, nullable=True)
    arquivo_path = Column(Text, nullable=True)
    capturado_em = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class TseTotalizacao(Base):
    """Um estado oficial do EA20 para (cargo, abrangencia). Nunca e atualizado."""

    __tablename__ = "tse_totalizacoes"
    __table_args__ = (
        UniqueConstraint("cargo_id", "abrangencia_id", "snapshot_id",
                         name="uq_tse_totalizacoes_snapshot"),
        Index("ix_tse_totalizacoes_atual", "abrangencia_id", "cargo_id", "id"),
        Index("ix_tse_totalizacoes_eleicao", "eleicao_id", "cargo_id"),
    )

    id = Column(Integer, primary_key=True)
    eleicao_id = Column(Integer, ForeignKey("tse_eleicoes.id"), nullable=False)
    cargo_id = Column(Integer, ForeignKey("tse_cargos.id"), nullable=False)
    abrangencia_id = Column(Integer, ForeignKey("tse_abrangencias.id"), nullable=False)
    snapshot_id = Column(Integer, ForeignKey("tse_snapshots.id"), nullable=False)
    idg = Column(String(20), nullable=True)
    gerado_em = Column(DateTime(timezone=True), nullable=True)
    ultima_totalizacao = Column(DateTime(timezone=True), nullable=True)
    andamento = Column(String(2), nullable=True)
    totalizacao_final = Column(String(2), nullable=True)
    vagas = Column(Integer, nullable=True)
    secoes_total = Column(Integer, nullable=True)
    secoes_totalizadas = Column(Integer, nullable=True)
    eleitores = Column(Integer, nullable=True)
    comparecimento = Column(Integer, nullable=True)
    abstencoes = Column(Integer, nullable=True)
    votos_total = Column(Integer, nullable=True)
    votos_validos = Column(Integer, nullable=True)
    votos_nominais = Column(Integer, nullable=True)
    votos_legenda = Column(Integer, nullable=True)
    votos_brancos = Column(Integer, nullable=True)
    votos_nulos = Column(Integer, nullable=True)
    votos_anulados = Column(Integer, nullable=True)
    votos_anulados_sub_judice = Column(Integer, nullable=True)
    quociente_eleitoral = Column(Integer, nullable=True)
    # Hash do conteudo material (sem IDG/data de geracao): define "mudou".
    conteudo_hash = Column(String(64), nullable=False)
    capturado_em = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    cargo = relationship("TseCargo")
    abrangencia = relationship("TseAbrangencia")
    snapshot = relationship("TseSnapshot")
    resultados = relationship("TseResultadoCandidato", back_populates="totalizacao")


class TseResultadoCandidato(Base):
    __tablename__ = "tse_resultados_candidato"
    __table_args__ = (
        UniqueConstraint("totalizacao_id", "candidato_id", name="uq_tse_resultados_candidato"),
        Index("ix_tse_resultados_candidato_candidato", "candidato_id"),
    )

    id = Column(Integer, primary_key=True)
    totalizacao_id = Column(Integer, ForeignKey("tse_totalizacoes.id"), nullable=False)
    candidato_id = Column(Integer, ForeignKey("tse_candidatos.id"), nullable=False)
    votos = Column(Integer, nullable=False)
    percentual = Column(Numeric(7, 2), nullable=True)
    situacao = Column(String(60), nullable=True)
    eleito = Column(String(2), nullable=True)
    # `dvt` do EA20: voto anulado/sub judice aparece em `votos` mas nao e valido.
    destinacao_voto = Column(String(60), nullable=True)

    totalizacao = relationship("TseTotalizacao", back_populates="resultados")
    candidato = relationship("TseCandidato")


class TseResultadoPartido(Base):
    __tablename__ = "tse_resultados_partido"
    __table_args__ = (
        UniqueConstraint("totalizacao_id", "partido_id", name="uq_tse_resultados_partido"),
        Index("ix_tse_resultados_partido_partido", "partido_id"),
    )

    id = Column(Integer, primary_key=True)
    totalizacao_id = Column(Integer, ForeignKey("tse_totalizacoes.id"), nullable=False)
    partido_id = Column(Integer, ForeignKey("tse_partidos.id"), nullable=False)
    votos_nominais = Column(Integer, nullable=True)
    votos_legenda = Column(Integer, nullable=True)
    destinacao_voto = Column(String(60), nullable=True)

    partido = relationship("TsePartido")


class TseSecao(Base):
    """Secao do EA16. Configuracao do PLEITO (nao de uma eleicao).

    Secao agregada nao tem urna propria: seus votos estao no BU da principal.
    Contagens de urna usam apenas `eh_principal`.
    """

    __tablename__ = "tse_secoes"
    __table_args__ = (
        UniqueConstraint("origem", "pleito", "uf", "municipio_codigo", "zona", "secao",
                         name="uq_tse_secoes_natural"),
        CheckConstraint(_sql_in("origem", ORIGENS_TSE), name="ck_tse_secoes_origem"),
        CheckConstraint(
            "(eh_principal AND secao_principal IS NULL) OR "
            "(NOT eh_principal AND secao_principal IS NOT NULL)",
            name="ck_tse_secoes_agregacao",
        ),
        Index("ix_tse_secoes_zona", "origem", "pleito", "uf", "municipio_codigo", "zona"),
    )

    id = Column(Integer, primary_key=True)
    origem = Column(String(10), nullable=False)
    pleito = Column(String(10), nullable=False)
    uf = Column(String(2), nullable=False)
    municipio_codigo = Column(String(5), nullable=False)
    zona = Column(String(4), nullable=False)
    secao = Column(String(4), nullable=False)
    eh_principal = Column(Boolean, nullable=False)
    secao_principal = Column(String(4), nullable=True)
    # Data/hora do arquivo auxiliar (EA18) informada pelo EA16: gatilho de mudanca.
    auxiliar_em = Column(DateTime(timezone=True), nullable=True)
    criado_em = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    atualizado_em = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class TseArquivoSecao(Base):
    """Arquivo de urna listado pelo EA18 (BU, RDV, log...). So metadados."""

    __tablename__ = "tse_arquivos_secao"
    __table_args__ = (
        UniqueConstraint("secao_id", "hash", "nome_arquivo", name="uq_tse_arquivos_secao"),
    )

    id = Column(Integer, primary_key=True)
    secao_id = Column(Integer, ForeignKey("tse_secoes.id"), nullable=False)
    snapshot_id = Column(Integer, ForeignKey("tse_snapshots.id"), nullable=False)
    hash = Column(String(200), nullable=False)
    situacao = Column(String(40), nullable=True)
    recebido_em = Column(DateTime(timezone=True), nullable=True)
    tipo_arquivo = Column(String(20), nullable=False)
    nome_arquivo = Column(String(200), nullable=False)
    url = Column(Text, nullable=False)
    sha256 = Column(String(64), nullable=True)
    capturado_em = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    secao = relationship("TseSecao")


# ------------------------------------------------------------ Boletim de Urna
STATUS_BU_CONTROLE = ("PROCESSADO", "AGUARDANDO", "ERRO")
TIPOS_VOTO_BU = ("NOMINAL", "LEGENDA")


class TseBoletimUrna(Base):
    """Uma versao do BU de uma urna (ADR-090). Fonte oficial do nivel SECAO.

    Pertence a secao PRINCIPAL: o BU nao lista secoes agregadas, e os votos das
    agregadas estao neste mesmo boletim (relacao no EA16 / `tse_secoes`).
    Append-only: arquivo diferente e linha nova; o corrente e o apontado por
    `tse_bu_controle`. Datas `*_local` sao a hora local da urna, sem fuso.
    """

    __tablename__ = "tse_boletins_urna"
    __table_args__ = (
        UniqueConstraint("secao_id", "sha256", name="uq_tse_boletins_urna_arquivo"),
        UniqueConstraint("snapshot_id", name="uq_tse_boletins_urna_snapshot"),
        CheckConstraint(_sql_in("origem", ORIGENS_TSE), name="ck_tse_boletins_urna_origem"),
    )

    id = Column(Integer, primary_key=True)
    origem = Column(String(10), nullable=False)
    pleito = Column(String(10), nullable=False)
    secao_id = Column(Integer, ForeignKey("tse_secoes.id"), nullable=False)
    snapshot_id = Column(Integer, ForeignKey("tse_snapshots.id"), nullable=False)
    # SHA-256 do arquivo original: identidade da versao (idempotencia).
    sha256 = Column(String(64), nullable=False)
    tamanho_bytes = Column(Integer, nullable=False)
    # Hash e situacao do EA18 que apontou este arquivo, e quando o TSE o recebeu.
    hash_ea18 = Column(String(200), nullable=False)
    situacao_ea18 = Column(String(40), nullable=True)
    recebido_em = Column(DateTime(timezone=True), nullable=True)
    tipo_bu = Column(String(10), nullable=False)   # SECAO | SA
    local_votacao = Column(Integer, nullable=True)
    comparecimento = Column(Integer, nullable=False)
    gerado_local = Column(DateTime(timezone=False), nullable=True)
    emitido_local = Column(DateTime(timezone=False), nullable=True)
    abertura_local = Column(DateTime(timezone=False), nullable=True)
    encerramento_local = Column(DateTime(timezone=False), nullable=True)
    tipo_urna = Column(Integer, nullable=True)
    tipo_arquivo = Column(Integer, nullable=True)
    versao_votacao = Column(String(120), nullable=True)
    numero_interno_urna = Column(Integer, nullable=True)
    codigo_carga = Column(String(40), nullable=True)
    processado_em = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    secao = relationship("TseSecao")
    snapshot = relationship("TseSnapshot")
    cargos = relationship("TseBuCargo", back_populates="boletim")


class TseBuCargo(Base):
    """Totais de um cargo num BU. Brancos e nulos sao os do boletim."""

    __tablename__ = "tse_bu_cargos"
    __table_args__ = (
        UniqueConstraint("boletim_id", "cargo_id", name="uq_tse_bu_cargos"),
        Index("ix_tse_bu_cargos_cargo", "cargo_id", "boletim_id"),
    )

    id = Column(Integer, primary_key=True)
    boletim_id = Column(Integer, ForeignKey("tse_boletins_urna.id"), nullable=False)
    eleicao_id = Column(Integer, ForeignKey("tse_eleicoes.id"), nullable=False)
    cargo_id = Column(Integer, ForeignKey("tse_cargos.id"), nullable=False)
    tipo_cargo = Column(String(12), nullable=False)
    eleitores_aptos = Column(Integer, nullable=False)
    comparecimento = Column(Integer, nullable=False)
    votos_nominais = Column(Integer, nullable=False)
    votos_legenda = Column(Integer, nullable=False)
    votos_brancos = Column(Integer, nullable=False)
    votos_nulos = Column(Integer, nullable=False)

    boletim = relationship("TseBoletimUrna", back_populates="cargos")
    cargo = relationship("TseCargo")
    votos = relationship("TseBuVoto", back_populates="bu_cargo")


class TseBuVoto(Base):
    """Voto nominal (por candidato) ou de legenda (por partido) de um cargo no BU.

    `numero` e o votavel do boletim. `candidato_id`/`partido_id` ligam ao
    cadastro vindo do EA20; ficam nulos enquanto o cadastro nao existir.
    """

    __tablename__ = "tse_bu_votos"
    __table_args__ = (
        UniqueConstraint("bu_cargo_id", "tipo", "numero", name="uq_tse_bu_votos"),
        CheckConstraint(_sql_in("tipo", TIPOS_VOTO_BU), name="ck_tse_bu_votos_tipo"),
        Index("ix_tse_bu_votos_candidato", "candidato_id"),
        Index("ix_tse_bu_votos_partido", "partido_id"),
    )

    id = Column(Integer, primary_key=True)
    bu_cargo_id = Column(Integer, ForeignKey("tse_bu_cargos.id"), nullable=False)
    tipo = Column(String(8), nullable=False)
    numero = Column(String(10), nullable=False)
    partido_numero = Column(String(5), nullable=False)
    candidato_id = Column(Integer, ForeignKey("tse_candidatos.id"), nullable=True)
    partido_id = Column(Integer, ForeignKey("tse_partidos.id"), nullable=True)
    votos = Column(Integer, nullable=False)

    bu_cargo = relationship("TseBuCargo", back_populates="votos")


class TseBuControle(Base):
    """Estado da ingestao do BU de cada secao principal (uma linha por secao).

    `auxiliar_em` guarda o carimbo do EA16 ja verificado: a secao so volta a
    ser consultada quando o TSE publica outro. `boletim_id` e o BU corrente e
    so avanca para um boletim NOVO -- um arquivo antigo servido de novo
    (A -> B -> A) nao o faz regredir.
    """

    __tablename__ = "tse_bu_controle"
    __table_args__ = (
        UniqueConstraint("secao_id", name="uq_tse_bu_controle_secao"),
        CheckConstraint(_sql_in("status", STATUS_BU_CONTROLE), name="ck_tse_bu_controle_status"),
        Index("ix_tse_bu_controle_boletim", "boletim_id"),
    )

    id = Column(Integer, primary_key=True)
    secao_id = Column(Integer, ForeignKey("tse_secoes.id"), nullable=False)
    status = Column(String(12), nullable=False)
    boletim_id = Column(Integer, ForeignKey("tse_boletins_urna.id"), nullable=True)
    auxiliar_em = Column(DateTime(timezone=True), nullable=True)
    tentativas = Column(Integer, nullable=False, default=0, server_default=text("0"))
    erro = Column(String(300), nullable=True)
    verificado_em = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    secao = relationship("TseSecao")
    boletim = relationship("TseBoletimUrna")
