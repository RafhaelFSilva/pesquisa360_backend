# pesquisa360/db/models.py
from sqlalchemy import Column, Integer, String, Boolean, ForeignKey, Date, Text, DateTime, and_, Float, Index, UniqueConstraint, CheckConstraint, ForeignKeyConstraint, Numeric, text, JSON
from sqlalchemy.orm import relationship, declarative_base
from sqlalchemy.dialects.postgresql import JSONB
from geoalchemy2 import Geometry
from sqlalchemy.sql import func

Base = declarative_base()

class Company(Base):
    __tablename__ = "companies"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    cnpj = Column(String(14), nullable=True, unique=True)
    logo_url = Column(String(2048), nullable=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relacionamentos
    users = relationship("Usuario", back_populates="company")
    projects = relationship("Projeto", back_populates="company")

# Adicione ao final do arquivo pesquisa360/db/models.py

class LocalVotacao(Base):
    __tablename__ = "locais_votacao"

    id = Column(Integer, primary_key=True, index=True)
    nome = Column(String, nullable=False)
    zona = Column(Integer, index=True)
    secoes = Column(JSONB)  # Estrutura: [{"secao": 98, "votos": 241}, ...]
    municipio = Column(String)
    bairro = Column(String)
    endereco = Column(String)
    
    # Georreferenciamento (PostGIS)
    # Armazenamos como POINT(longitude latitude)
    localizacao = Column(Geometry("POINT", srid=4326), nullable=True)
    
    # Multitenancia: Cada local pertence a uma empresa/cliente
    company_id = Column(Integer, ForeignKey("companies.id"), nullable=False)
    
    # Relacionamento
    company = relationship("Company")

class Bairro(Base):
    __tablename__ = "bairros"

    id = Column(Integer, primary_key=True, index=True)
    nome = Column(String, nullable=False, index=True)
    
    # Dados demográficos extras que vieram no seu GeoJSON
    area_ha = Column(Float, nullable=True)
    populacao = Column(Integer, nullable=True)
    eleitores = Column(Integer, nullable=True)
    
    # Georreferenciamento do Polígono (PostGIS)
    # Usamos MULTIPOLYGON porque alguns bairros podem ter ilhas/áreas separadas
    geometria = Column(Geometry("MULTIPOLYGON", srid=4326), nullable=False)
    
    # Multitenancia: Cada mapa pertence a uma empresa/cliente
    company_id = Column(Integer, ForeignKey("companies.id"), nullable=False)
    
    # Relacionamento
    company = relationship("Company")

class Usuario(Base):
    __tablename__ = "usuarios"
    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True, nullable=False)
    nome = Column(String)
    senha_hash = Column(String, nullable=False)
    ativo = Column(Boolean, default=True, nullable=False)
    perfil_id = Column(Integer, ForeignKey("perfis.id"), nullable=False)
    perfil = relationship("Perfil")
    projetos = relationship("Projeto", back_populates="coordenador")
    coletas = relationship("Coleta", back_populates="agente")
    company_id = Column(Integer, ForeignKey("companies.id"), nullable=False) # Note: nullable=False
    company = relationship("Company", back_populates="users")

    @property
    def perfil_nome(self):
        return self.perfil.nome if self.perfil else None

class Perfil(Base):
    __tablename__ = "perfis"
    id = Column(Integer, primary_key=True, index=True)
    nome = Column(String, unique=True, index=True, nullable=False)
    descricao = Column(String)

class Projeto(Base):
    __tablename__ = "projetos"
    id = Column(Integer, primary_key=True, index=True)
    nome = Column(String, index=True, nullable=False)
    descricao = Column(String, nullable=True)
    status = Column(String, default="Planejamento", nullable=False)
    data_inicio = Column(Date, nullable=True, default=func.current_date())
    data_fim = Column(Date, nullable=True)
    coordenador_id = Column(Integer, ForeignKey("usuarios.id"), nullable=False)
    coordenador = relationship("Usuario", back_populates="projetos")
    pesquisas = relationship(
        "Pesquisa", 
        primaryjoin="and_(Projeto.id == Pesquisa.projeto_id, Pesquisa.ativo == True)",
        back_populates="projeto", 
        cascade="all, delete-orphan"
    )
    company_id = Column(Integer, ForeignKey("companies.id"), nullable=False)
    company = relationship("Company", back_populates="projects")


class Pesquisa(Base):
    __tablename__ = "pesquisas"
    id = Column(Integer, primary_key=True, index=True)
    titulo = Column(String, nullable=False)
    tipo_pesquisa = Column(String)
    ativo = Column(Boolean, default=True, nullable=False)
    projeto_id = Column(Integer, ForeignKey("projetos.id"), nullable=False)

    # --- NOVAS COLUNAS PARA GEOFENCING ---
    cerca_eletronica = Column(Geometry(geometry_type='POLYGON', srid=4326), nullable=True)
    tolerancia_metros = Column(Integer, nullable=True)

    projeto = relationship("Projeto", back_populates="pesquisas")
    perguntas = relationship(
        "Pergunta", 
        primaryjoin="and_(Pesquisa.id == Pergunta.pesquisa_id, Pergunta.ativo == True)",
        back_populates="pesquisa", 
        cascade="all, delete-orphan"
    )
    coletas = relationship("Coleta", back_populates="pesquisa", cascade="all, delete-orphan")
    apuracoes = relationship("Apuracao", back_populates="pesquisa", cascade="all, delete-orphan")
    categorias_resposta_espontanea = relationship(
        "CategoriaRespostaEspontanea",
        back_populates="pesquisa",
        cascade="all, delete-orphan",
    )
    mapeamentos_resposta_espontanea = relationship(
        "MapeamentoRespostaEspontanea",
        back_populates="pesquisa",
        cascade="all, delete-orphan",
    )
    setores = relationship("Setor", back_populates="pesquisa", cascade="all, delete-orphan")
    configuracoes_relatorio_executivo = relationship(
        "ConfiguracaoRelatorioExecutivo",
        back_populates="pesquisa",
        cascade="all, delete-orphan",
    )

class Pergunta(Base):
    __tablename__ = "perguntas"
    id = Column(Integer, primary_key=True, index=True)
    texto_pergunta = Column(Text, nullable=False)
    tipo_pergunta = Column(String, nullable=False)
    ordem = Column(Integer, nullable=False)
    eh_obrigatoria = Column(Boolean, default=True, nullable=False)
    eh_resposta_espontanea = Column(Boolean, default=False, nullable=False)
    papel_analitico = Column(String(50), nullable=True, index=True)
    metadados_analiticos = Column(
        JSON().with_variant(JSONB, "postgresql"),
        nullable=False,
        default=dict,
        server_default=text("'{}'"),
    )
    
    ativo = Column(Boolean, default=True, nullable=False)
    pesquisa_id = Column(Integer, ForeignKey("pesquisas.id"), nullable=False)
    
    pesquisa = relationship("Pesquisa", back_populates="perguntas")
    respostas = relationship("Resposta", back_populates="pergunta")
    
    # CORREÇÃO AQUI: Adicionamos foreign_keys para desambiguar
    opcoes = relationship(
        "Opcao", 
        back_populates="pergunta", 
        cascade="all, delete-orphan",
        foreign_keys="Opcao.pergunta_id" 
    )

class Opcao(Base):
    __tablename__ = "opcoes"
    
    id = Column(Integer, primary_key=True, index=True)
    texto = Column(String, nullable=False)
    ordem = Column(Integer, default=0)
    pergunta_id = Column(Integer, ForeignKey("perguntas.id"))
    proxima_pergunta_id = Column(Integer, ForeignKey("perguntas.id"), nullable=True)
    
    # Relacionamentos
    pergunta = relationship("Pergunta", back_populates="opcoes", foreign_keys=[pergunta_id])
    # Opcional: relacionamento para saber qual é a próxima pergunta
    proxima_pergunta = relationship("Pergunta", foreign_keys=[proxima_pergunta_id])

class Coleta(Base):
    __tablename__ = "coletas"
    __table_args__ = (
        UniqueConstraint("company_id", "client_uuid", name="uq_coletas_company_client_uuid"),
    )

    id = Column(Integer, primary_key=True, index=True)
    pesquisa_id = Column(Integer, ForeignKey("pesquisas.id"), nullable=False)
    agente_id = Column(Integer, ForeignKey("usuarios.id"), nullable=False)
    company_id = Column(Integer, ForeignKey("companies.id"), nullable=False)
    client_uuid = Column(String(36), nullable=False)

    # --- NOVOS CAMPOS DE AUDITORIA ---
    foi_offline = Column(Boolean, default=False)  # Indica se o app estava offline
    endereco_estimado = Column(String, nullable=True) # Endereço reverso (GPS -> Rua)
    status_sincronizacao = Column(String)
    # ---------------------------------

    data_inicio_coleta = Column(DateTime(timezone=True), nullable=False)
    data_fim_coleta = Column(DateTime(timezone=True), nullable=True)
    localizacao_inicio = Column(Geometry(geometry_type='POINT', srid=4326), nullable=True)
    localizacao_fim = Column(Geometry(geometry_type='POINT', srid=4326), nullable=True)

    # --- NOVA COLUNA PARA GEOFENCING ---
    inconformidade_localizacao = Column(Boolean, default=False, nullable=False)

    pesquisa = relationship("Pesquisa", back_populates="coletas")
    agente = relationship("Usuario", back_populates="coletas")
    respostas = relationship("Resposta", back_populates="coleta", cascade="all, delete-orphan")

class Resposta(Base):
    __tablename__ = "respostas"
    id = Column(Integer, primary_key=True, index=True)
    pergunta_id = Column(Integer, ForeignKey("perguntas.id"), nullable=False)
    coleta_id = Column(Integer, ForeignKey("coletas.id"), nullable=False)
    valor_resposta = Column(Text, nullable=False)
    pergunta = relationship("Pergunta", back_populates="respostas")
    coleta = relationship("Coleta", back_populates="respostas")

class Setor(Base):
    __tablename__ = "setores"
    
    id = Column(Integer, primary_key=True, index=True)
    nome = Column(String, nullable=False) # Ex: "Setor 01 - Centro"
    meta = Column(Integer, nullable=False, default=0) # Ex: 50 coletas
    # Tolerância específica deste setor em metros. 
    # Se for 0 ou Null, o sistema pode usar a tolerância padrão da Pesquisa.
    tolerancia = Column(Integer, default=50, nullable=False)
    finalidade = Column(String, nullable=False, default="OPERACAO", server_default="OPERACAO")
    
    # Geometria do Setor (Polígono específico desta área)
    geometria = Column(Geometry("POLYGON", srid=4326), nullable=True)
    
    pesquisa_id = Column(Integer, ForeignKey("pesquisas.id"), nullable=False)
    
    # Responsável pelo setor (Agente)
    agente_id = Column(Integer, ForeignKey("usuarios.id"), nullable=True)
    
    # Relacionamentos
    pesquisa = relationship("Pesquisa", back_populates="setores")
    agente = relationship("Usuario") # Agente responsável


# --- NOVAS TABELA PARA APURAÇÃO ---
class Apuracao(Base):
    __tablename__ = "apuracoes"
    id = Column(Integer, primary_key=True, index=True)
    nome = Column(String, nullable=False)
    descricao = Column(String, nullable=True)
    data_criacao = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    
    pesquisa_id = Column(Integer, ForeignKey("pesquisas.id"), nullable=False)
    pesquisa = relationship("Pesquisa", back_populates="apuracoes")
    
    analises = relationship("AnaliseSalva", back_populates="apuracao", cascade="all, delete-orphan")

class AnaliseSalva(Base):
    __tablename__ = "analises_salvas"
    id = Column(Integer, primary_key=True, index=True)
    tipo_analise = Column(String, nullable=False) # Ex: "ESTATISTICA" ou "CROSSTAB"
    configuracao = Column(JSONB, nullable=False)  # Armazena os detalhes da análise
    ordem = Column(Integer, nullable=False)
    
    apuracao_id = Column(Integer, ForeignKey("apuracoes.id"), nullable=False)
    apuracao = relationship("Apuracao", back_populates="analises")


class ConfiguracaoRelatorioExecutivo(Base):
    __tablename__ = "configuracoes_relatorio_executivo"

    id = Column(Integer, primary_key=True, index=True)
    pesquisa_id = Column(Integer, ForeignKey("pesquisas.id", ondelete="CASCADE"), nullable=False, index=True)
    tipo_relatorio = Column(String, nullable=False, index=True)
    nome = Column(String, nullable=False)
    descricao = Column(Text, nullable=True)
    parametros_gerais = Column(JSONB, nullable=True)
    ativo = Column(Boolean, nullable=False, default=True, server_default=text("true"), index=True)
    criado_por_id = Column(Integer, ForeignKey("usuarios.id"), nullable=False)
    atualizado_por_id = Column(Integer, ForeignKey("usuarios.id"), nullable=False)
    criado_em = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    atualizado_em = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    pesquisa = relationship("Pesquisa", back_populates="configuracoes_relatorio_executivo")
    criador = relationship("Usuario", foreign_keys=[criado_por_id])
    atualizador = relationship("Usuario", foreign_keys=[atualizado_por_id])
    secoes = relationship(
        "SecaoRelatorioExecutivo",
        back_populates="configuracao",
        cascade="all, delete-orphan",
        order_by="SecaoRelatorioExecutivo.ordem, SecaoRelatorioExecutivo.id",
    )


class SecaoRelatorioExecutivo(Base):
    __tablename__ = "secoes_relatorio_executivo"
    __table_args__ = (
        Index("ix_secoes_relatorio_executivo_configuracao_ordem", "configuracao_id", "ordem"),
    )

    id = Column(Integer, primary_key=True, index=True)
    configuracao_id = Column(
        Integer,
        ForeignKey("configuracoes_relatorio_executivo.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    ordem = Column(Integer, nullable=False)
    tipo_secao = Column(String, nullable=False)
    titulo = Column(String, nullable=True)
    ativo = Column(Boolean, nullable=False, default=True, server_default=text("true"), index=True)

    configuracao = relationship("ConfiguracaoRelatorioExecutivo", back_populates="secoes")
    analises = relationship(
        "AnaliseRelatorioExecutivo",
        back_populates="secao",
        cascade="all, delete-orphan",
        order_by="AnaliseRelatorioExecutivo.ordem, AnaliseRelatorioExecutivo.id",
    )


class AnaliseRelatorioExecutivo(Base):
    __tablename__ = "analises_relatorio_executivo"
    __table_args__ = (
        Index("ix_analises_relatorio_executivo_secao_ordem", "secao_id", "ordem"),
    )

    id = Column(Integer, primary_key=True, index=True)
    secao_id = Column(
        Integer,
        ForeignKey("secoes_relatorio_executivo.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    ordem = Column(Integer, nullable=False)
    tipo_analise = Column(String, nullable=False, index=True)
    titulo_customizado = Column(String, nullable=True)
    parametros = Column(JSONB, nullable=False)
    ativo = Column(Boolean, nullable=False, default=True, server_default=text("true"), index=True)

    secao = relationship("SecaoRelatorioExecutivo", back_populates="analises")


class CategoriaRespostaEspontanea(Base):
    __tablename__ = "categorias_resposta_espontanea"
    __table_args__ = (
        Index(
            "uq_categoria_resposta_espontanea_pesquisa_nome_ativo",
            "pesquisa_id",
            "nome_normalizado",
            unique=True,
            postgresql_where=text("ativo IS TRUE"),
            sqlite_where=text("ativo = 1"),
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    pesquisa_id = Column(Integer, ForeignKey("pesquisas.id"), nullable=False, index=True)
    nome = Column(String, nullable=False)
    nome_normalizado = Column(String, nullable=False)
    ativo = Column(Boolean, nullable=False, default=True, server_default=text("true"), index=True)
    criado_por_id = Column(Integer, ForeignKey("usuarios.id"), nullable=False)
    atualizado_por_id = Column(Integer, ForeignKey("usuarios.id"), nullable=False)
    criado_em = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    atualizado_em = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    pesquisa = relationship("Pesquisa", back_populates="categorias_resposta_espontanea")
    criador = relationship("Usuario", foreign_keys=[criado_por_id])
    atualizador = relationship("Usuario", foreign_keys=[atualizado_por_id])
    mapeamentos = relationship(
        "MapeamentoRespostaEspontanea",
        back_populates="categoria",
        cascade="all, delete-orphan",
    )


class MapeamentoRespostaEspontanea(Base):
    __tablename__ = "mapeamentos_resposta_espontanea"
    __table_args__ = (
        Index(
            "uq_mapeamento_resposta_espontanea_pesquisa_chave_ativo",
            "pesquisa_id",
            "chave_normalizada",
            unique=True,
            postgresql_where=text("ativo IS TRUE"),
            sqlite_where=text("ativo = 1"),
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    pesquisa_id = Column(Integer, ForeignKey("pesquisas.id"), nullable=False, index=True)
    categoria_id = Column(Integer, ForeignKey("categorias_resposta_espontanea.id"), nullable=False, index=True)
    chave_normalizada = Column(String, nullable=False)
    texto_referencia = Column(Text, nullable=False)
    ativo = Column(Boolean, nullable=False, default=True, server_default=text("true"), index=True)
    criado_por_id = Column(Integer, ForeignKey("usuarios.id"), nullable=False)
    atualizado_por_id = Column(Integer, ForeignKey("usuarios.id"), nullable=False)
    criado_em = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    atualizado_em = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    pesquisa = relationship("Pesquisa", back_populates="mapeamentos_resposta_espontanea")
    categoria = relationship("CategoriaRespostaEspontanea", back_populates="mapeamentos")
    criador = relationship("Usuario", foreign_keys=[criado_por_id])
    atualizador = relationship("Usuario", foreign_keys=[atualizado_por_id])


# ==============================================================================
# BASE ELEITORAL VERSIONADA
# ==============================================================================
# Dado de referencia eleitoral, independente dos setores operacionais.
# `company_id IS NULL` marca a base oficial/global; `company_id` preenchido
# marca uma base privada do tenant. O vinculo com a campanha e feito pelo
# Projeto (projeto_base_eleitoral), nunca pela Pesquisa.

STATUS_BASE_ELEITORAL = ("IMPORTADA", "EM_CONFERENCIA", "VALIDADA", "SUBSTITUIDA")
TIPOS_TERRITORIO_ELEITORAL = (
    "ESTADO",
    "MUNICIPIO",
    "BAIRRO",
    "LOCALIDADE",
    "LOCAL_VOTACAO",
    "SECAO",
)


def _sql_in(coluna: str, valores) -> str:
    """Monta `coluna IN ('A','B')` para CHECKs portateis entre PostgreSQL e SQLite."""
    return "{} IN ({})".format(coluna, ", ".join("'{}'".format(item) for item in valores))


class BaseEleitoral(Base):
    __tablename__ = "base_eleitoral"
    __table_args__ = (
        CheckConstraint(_sql_in("status", STATUS_BASE_ELEITORAL), name="ck_base_eleitoral_status"),
        CheckConstraint(
            "comparecimento_estimado IS NULL OR (comparecimento_estimado >= 0 AND comparecimento_estimado <= 1)",
            name="ck_base_eleitoral_comparecimento",
        ),
        CheckConstraint(
            "percentual_votos_validos IS NULL OR (percentual_votos_validos >= 0 AND percentual_votos_validos <= 1)",
            name="ck_base_eleitoral_votos_validos",
        ),
        # NULL nao participa de UNIQUE no PostgreSQL: duas bases oficiais passariam
        # por UNIQUE(uf, ano, versao, company_id). Por isso sao dois indices parciais.
        Index(
            "uq_base_eleitoral_oficial",
            "uf",
            "ano",
            "versao",
            unique=True,
            postgresql_where=text("company_id IS NULL"),
            sqlite_where=text("company_id IS NULL"),
        ),
        Index(
            "uq_base_eleitoral_privada",
            "uf",
            "ano",
            "versao",
            "company_id",
            unique=True,
            postgresql_where=text("company_id IS NOT NULL"),
            sqlite_where=text("company_id IS NOT NULL"),
        ),
        Index("ix_base_eleitoral_uf_ano", "uf", "ano"),
        Index("ix_base_eleitoral_company_id", "company_id"),
        Index("ix_base_eleitoral_status", "status"),
    )

    id = Column(Integer, primary_key=True)
    nome = Column(String, nullable=False)
    ano = Column(Integer, nullable=False)
    uf = Column(String(2), nullable=False)
    fonte = Column(String, nullable=False)
    fonte_referencia = Column(String, nullable=True)
    versao = Column(String, nullable=False)
    data_referencia = Column(Date, nullable=False)
    status = Column(String, nullable=False, default="IMPORTADA", server_default=text("'IMPORTADA'"))
    substituida_por_id = Column(Integer, ForeignKey("base_eleitoral.id"), nullable=True)

    # NULL = base oficial/global, visivel por todos os tenants.
    company_id = Column(Integer, ForeignKey("companies.id"), nullable=True)

    # Parametros de projecao. O produto eleitorado x comparecimento x validos
    # NAO e calculado nesta fase; aqui apenas persistimos os parametros.
    comparecimento_estimado = Column(Numeric(5, 4), nullable=True)
    percentual_votos_validos = Column(Numeric(5, 4), nullable=True)

    criado_por_id = Column(Integer, ForeignKey("usuarios.id"), nullable=False)
    criado_em = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    atualizado_em = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    company = relationship("Company", foreign_keys=[company_id])
    criado_por = relationship("Usuario", foreign_keys=[criado_por_id])
    substituida_por = relationship(
        "BaseEleitoral", remote_side=[id], foreign_keys=[substituida_por_id]
    )
    territorios = relationship(
        "TerritorioEleitoral",
        back_populates="base_eleitoral",
        foreign_keys="TerritorioEleitoral.base_eleitoral_id",
        passive_deletes=True,
    )
    projetos_vinculados = relationship(
        "ProjetoBaseEleitoral", back_populates="base_eleitoral"
    )
    importacoes = relationship(
        "ImportacaoBaseEleitoral", back_populates="base_eleitoral", passive_deletes=True
    )


class TerritorioEleitoral(Base):
    __tablename__ = "territorio_eleitoral"
    __table_args__ = (
        # Alvo das FKs compostas: amarra cada no a uma unica versao de base.
        UniqueConstraint("id", "base_eleitoral_id", name="uq_territorio_eleitoral_id_base"),
        ForeignKeyConstraint(
            ["parent_id", "base_eleitoral_id"],
            ["territorio_eleitoral.id", "territorio_eleitoral.base_eleitoral_id"],
            name="fk_territorio_parent_mesma_base",
        ),
        ForeignKeyConstraint(
            ["municipio_id", "base_eleitoral_id"],
            ["territorio_eleitoral.id", "territorio_eleitoral.base_eleitoral_id"],
            name="fk_territorio_municipio_mesma_base",
        ),
        CheckConstraint(_sql_in("tipo", TIPOS_TERRITORIO_ELEITORAL), name="ck_territorio_tipo"),
        CheckConstraint(
            _sql_in("status_validacao", STATUS_BASE_ELEITORAL),
            name="ck_territorio_status_validacao",
        ),
        CheckConstraint(
            "parent_id IS NOT NULL OR tipo = 'ESTADO'", name="ck_territorio_raiz"
        ),
        CheckConstraint(
            "tipo <> 'SECAO' OR numero_secao IS NOT NULL", name="ck_territorio_secao"
        ),
        CheckConstraint(
            "eleitorado_apto IS NULL OR eleitorado_apto >= 0",
            name="ck_territorio_eleitorado_apto",
        ),
        CheckConstraint(
            "eleitorado_apto_origem IS NULL OR eleitorado_apto_origem >= 0",
            name="ck_territorio_eleitorado_origem",
        ),
        # GeometryType() e funcao PostGIS: nao existe em SQLite, onde a suite de
        # migrations roda. `ddl_if` mantem o CHECK no metadata sem emiti-lo fora
        # do PostgreSQL; a migration aplica a mesma condicao por dialeto.
        CheckConstraint(
            "geometria IS NULL"
            " OR (tipo IN ('LOCAL_VOTACAO', 'SECAO') AND GeometryType(geometria) = 'POINT')"
            " OR (tipo IN ('ESTADO', 'MUNICIPIO', 'BAIRRO', 'LOCALIDADE')"
            " AND GeometryType(geometria) IN ('POLYGON', 'MULTIPOLYGON'))",
            name="ck_territorio_geometria_tipo",
        ).ddl_if(dialect="postgresql"),
        Index("ix_territorio_base_tipo", "base_eleitoral_id", "tipo"),
        Index("ix_territorio_parent", "parent_id"),
        Index("ix_territorio_municipio", "municipio_id"),
        Index("ix_territorio_nome_norm", "base_eleitoral_id", "nome_normalizado"),
        Index("ix_territorio_zona", "zona_eleitoral"),
        Index(
            "uq_territorio_base_tipo_codigo",
            "base_eleitoral_id",
            "tipo",
            "codigo",
            unique=True,
            postgresql_where=text("codigo IS NOT NULL"),
            sqlite_where=text("codigo IS NOT NULL"),
        ),
    )

    id = Column(Integer, primary_key=True)
    base_eleitoral_id = Column(
        Integer, ForeignKey("base_eleitoral.id", ondelete="CASCADE"), nullable=False
    )
    parent_id = Column(Integer, nullable=True)
    tipo = Column(String, nullable=False)
    codigo = Column(String, nullable=True)
    nome = Column(String, nullable=False)
    nome_normalizado = Column(String, nullable=False)
    municipio_id = Column(Integer, nullable=True)
    zona_eleitoral = Column(Integer, nullable=True)
    numero_secao = Column(Integer, nullable=True)

    # eleitorado_apto e o valor em uso; _origem preserva o valor bruto da fonte.
    # Divergencia entre resumo e detalhe NAO e reconciliada automaticamente.
    eleitorado_apto = Column(Integer, nullable=True)
    eleitorado_apto_origem = Column(Integer, nullable=True)
    eleitorado_apto_divergente = Column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    status_validacao = Column(
        String, nullable=False, default="IMPORTADA", server_default=text("'IMPORTADA'")
    )

    # GEOMETRY generico: poligonos para ESTADO/MUNICIPIO/BAIRRO/LOCALIDADE e
    # pontos para LOCAL_VOTACAO/SECAO. Nulo porque a fonte pode nao trazer geometria.
    geometria = Column(Geometry(geometry_type="GEOMETRY", srid=4326), nullable=True)
    metadados = Column(
        JSON().with_variant(JSONB, "postgresql"),
        nullable=False,
        default=dict,
        server_default=text("'{}'"),
    )

    criado_em = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    atualizado_em = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    base_eleitoral = relationship(
        "BaseEleitoral", back_populates="territorios", foreign_keys=[base_eleitoral_id]
    )
    # As auto-relacoes compartilham base_eleitoral_id entre as duas FKs compostas,
    # o que impede o ORM de separar lado local e remoto. Como `id` e chave primaria,
    # o join por id ja identifica o no; a base comum fica garantida pelas FKs no banco.
    parent = relationship(
        "TerritorioEleitoral",
        primaryjoin="foreign(TerritorioEleitoral.parent_id) == remote(TerritorioEleitoral.id)",
        viewonly=True,
    )
    children = relationship(
        "TerritorioEleitoral",
        primaryjoin="remote(foreign(TerritorioEleitoral.parent_id)) == TerritorioEleitoral.id",
        viewonly=True,
    )
    municipio = relationship(
        "TerritorioEleitoral",
        primaryjoin="foreign(TerritorioEleitoral.municipio_id) == remote(TerritorioEleitoral.id)",
        viewonly=True,
    )


class ProjetoBaseEleitoral(Base):
    __tablename__ = "projeto_base_eleitoral"
    __table_args__ = (
        UniqueConstraint("projeto_id", "base_eleitoral_id", name="uq_projeto_base"),
        Index("ix_projeto_base_projeto", "projeto_id"),
        # Historico livre de vinculos, mas apenas uma base principal por projeto.
        Index(
            "uq_projeto_base_principal",
            "projeto_id",
            unique=True,
            postgresql_where=text("principal IS TRUE"),
            sqlite_where=text("principal = 1"),
        ),
    )

    id = Column(Integer, primary_key=True)
    projeto_id = Column(Integer, ForeignKey("projetos.id"), nullable=False)
    base_eleitoral_id = Column(Integer, ForeignKey("base_eleitoral.id"), nullable=False)
    principal = Column(Boolean, nullable=False, default=True, server_default=text("true"))
    vinculado_em = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    projeto = relationship("Projeto", foreign_keys=[projeto_id])
    base_eleitoral = relationship(
        "BaseEleitoral", back_populates="projetos_vinculados", foreign_keys=[base_eleitoral_id]
    )


class ImportacaoBaseEleitoral(Base):
    __tablename__ = "importacao_base_eleitoral"
    __table_args__ = (
        CheckConstraint("total_linhas >= 0", name="ck_importacao_total_linhas"),
        CheckConstraint("total_importadas >= 0", name="ck_importacao_total_importadas"),
        CheckConstraint("total_divergencias >= 0", name="ck_importacao_total_divergencias"),
    )

    id = Column(Integer, primary_key=True)
    base_eleitoral_id = Column(
        Integer, ForeignKey("base_eleitoral.id", ondelete="CASCADE"), nullable=False
    )
    arquivo_origem = Column(String, nullable=False)
    hash_arquivo = Column(String, nullable=True)
    total_linhas = Column(Integer, nullable=False)
    total_importadas = Column(Integer, nullable=False)
    total_divergencias = Column(Integer, nullable=False)
    divergencias = Column(
        JSON().with_variant(JSONB, "postgresql"),
        nullable=False,
        default=list,
        server_default=text("'[]'"),
    )
    executado_por_id = Column(Integer, ForeignKey("usuarios.id"), nullable=False)
    executado_em = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    base_eleitoral = relationship(
        "BaseEleitoral", back_populates="importacoes", foreign_keys=[base_eleitoral_id]
    )
    executado_por = relationship("Usuario", foreign_keys=[executado_por_id])
