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
    atribuicoes_setores = relationship(
        "SetorAgente",
        back_populates="agente",
        cascade="all, delete-orphan",
    )
    # ACL multiempresa (ADR-024). `company_id` acima continua existindo como
    # empresa PRINCIPAL/default -- branding e contexto inicial --, nao como
    # autoridade de autorizacao: quem responde "pode ver este projeto?" e a
    # tabela de acessos abaixo, consultada no banco a cada request.
    empresa_acessos = relationship(
        "UsuarioEmpresaAcesso",
        back_populates="usuario",
        cascade="all, delete-orphan",
    )
    projeto_acessos = relationship(
        "UsuarioProjetoAcesso",
        back_populates="usuario",
        cascade="all, delete-orphan",
    )

    @property
    def perfil_nome(self):
        return self.perfil.nome if self.perfil else None

class AuditEvent(Base):
    """Evento de seguranca/acesso (ADR-039).

    Registro append-only: nunca e atualizado nem apagado pela aplicacao. Quem
    pergunta "quem tentou entrar / quem acessou este projeto / houve tentativa
    cross-tenant" le daqui. Nunca guarda senha, token ou corpo de requisicao --
    apenas identidade, contexto HTTP e um `details` pequeno e nao sensivel.

    FKs sao nullable e sem cascade: apagar um usuario ou projeto nao pode apagar
    a trilha do que ele fez.
    """

    __tablename__ = "audit_events"

    id = Column(Integer, primary_key=True, index=True)
    occurred_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)
    event_type = Column(String(64), nullable=False, index=True)
    severity = Column(String(16), nullable=False, index=True)

    user_id = Column(Integer, ForeignKey("usuarios.id"), nullable=True, index=True)
    company_id = Column(Integer, ForeignKey("companies.id"), nullable=True, index=True)
    project_id = Column(Integer, ForeignKey("projetos.id"), nullable=True, index=True)

    # E-mail informado num login que falhou: o usuario pode nem existir.
    attempted_email = Column(String(320), nullable=True)

    ip_address = Column(String(45), nullable=True)      # IPv6 cabe em 45
    user_agent = Column(String(512), nullable=True)
    http_method = Column(String(10), nullable=True)
    path = Column(String(512), nullable=True)
    status_code = Column(Integer, nullable=True)
    request_id = Column(String(64), nullable=True, index=True)

    details = Column(JSON().with_variant(JSONB, "postgresql"), nullable=True)

    __table_args__ = (
        Index("ix_audit_events_tipo_usuario_projeto_data", "event_type", "user_id", "project_id", "occurred_at"),
    )


class UserActivationToken(Base):
    """Convite de ativacao de conta (uso unico, com validade).

    O token em texto puro existe SOMENTE no e-mail do convidado: aqui fica
    apenas o SHA-256 dele. Vazamento do banco nao permite ativar conta alheia.
    Uso unico e representado por `usado_em`, nunca por DELETE -- apagar apagaria
    tambem a evidencia de que o convite foi consumido.
    """

    __tablename__ = "user_activation_tokens"

    id = Column(Integer, primary_key=True, index=True)
    usuario_id = Column(Integer, ForeignKey("usuarios.id"), nullable=False, index=True)
    # SHA-256 hex do token. Unico: dois convites nunca colidem.
    token_hash = Column(String(64), nullable=False, unique=True, index=True)
    criado_em = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    expira_em = Column(DateTime(timezone=True), nullable=False)
    usado_em = Column(DateTime(timezone=True), nullable=True)

    usuario = relationship("Usuario")


class UsuarioEmpresaAcesso(Base):
    """Vinculo do usuario com uma Empresa (ADR-024).

    `acesso_todos_projetos=True` reproduz o comportamento legado (usuario da
    empresa enxerga todos os projetos dela) e e o que o backfill grava; com
    `False`, o alcance passa a ser exatamente o que houver em
    `usuario_projeto_acessos`.
    """

    __tablename__ = "usuario_empresa_acessos"
    __table_args__ = (
        UniqueConstraint("usuario_id", "company_id", name="uq_usuario_empresa_acesso"),
    )

    id = Column(Integer, primary_key=True, index=True)
    usuario_id = Column(Integer, ForeignKey("usuarios.id"), nullable=False, index=True)
    company_id = Column(Integer, ForeignKey("companies.id"), nullable=False, index=True)
    acesso_todos_projetos = Column(Boolean, nullable=False, default=True)
    ativo = Column(Boolean, nullable=False, default=True)
    # Espelha `usuarios.company_id` no backfill; serve de contexto padrao da UI.
    principal = Column(Boolean, nullable=False, default=False)
    criado_em = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    atualizado_em = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    usuario = relationship("Usuario", back_populates="empresa_acessos")
    company = relationship("Company")


class UsuarioProjetoAcesso(Base):
    """Autorizacao explicita a UM projeto.

    Nao carrega `company_id`: o tenant do dado ja e `Projeto.company_id`.
    Duplicar aqui criaria uma segunda verdade sobre o mesmo fato.
    """

    __tablename__ = "usuario_projeto_acessos"
    __table_args__ = (
        UniqueConstraint("usuario_id", "projeto_id", name="uq_usuario_projeto_acesso"),
    )

    id = Column(Integer, primary_key=True, index=True)
    usuario_id = Column(Integer, ForeignKey("usuarios.id"), nullable=False, index=True)
    projeto_id = Column(Integer, ForeignKey("projetos.id"), nullable=False, index=True)
    ativo = Column(Boolean, nullable=False, default=True)
    criado_em = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    atualizado_em = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    usuario = relationship("Usuario", back_populates="projeto_acessos")
    projeto = relationship("Projeto")


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


class Modulo(Base):
    """Produto comercializavel do catalogo modular."""

    __tablename__ = "modulos"
    __table_args__ = (UniqueConstraint("chave", name="uq_modulos_chave"),)

    id = Column(Integer, primary_key=True, index=True)
    chave = Column(String(100), nullable=False)
    nome = Column(String(200), nullable=False)
    descricao = Column(Text, nullable=True)
    ativo = Column(Boolean, nullable=False, default=True, server_default=text("true"))
    criado_em = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    atualizado_em = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    funcionalidades = relationship("ModuloFuncionalidade", back_populates="modulo")
    entitlements = relationship("ModuloEntitlement", back_populates="modulo")


class ModuloFuncionalidade(Base):
    """Capacidade comercial explicita de um modulo."""

    __tablename__ = "modulo_funcionalidades"
    __table_args__ = (
        UniqueConstraint("modulo_id", "chave", name="uq_modulo_funcionalidade_chave"),
    )

    id = Column(Integer, primary_key=True, index=True)
    modulo_id = Column(Integer, ForeignKey("modulos.id", ondelete="CASCADE"), nullable=False, index=True)
    chave = Column(String(100), nullable=False)
    nome = Column(String(200), nullable=False)
    descricao = Column(Text, nullable=True)
    ativo = Column(Boolean, nullable=False, default=True, server_default=text("true"))
    criado_em = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    atualizado_em = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    modulo = relationship("Modulo", back_populates="funcionalidades")


class ModuloEntitlement(Base):
    """Licenca comercial de um modulo em escopo de empresa, projeto ou pesquisa."""

    __tablename__ = "modulo_entitlements"
    __table_args__ = (
        CheckConstraint(
            "NOT (projeto_id IS NOT NULL AND pesquisa_id IS NOT NULL)",
            name="ck_modulo_entitlement_um_escopo",
        ),
        CheckConstraint(
            "status IN ('ATIVO', 'SUSPENSO', 'CANCELADO')",
            name="ck_modulo_entitlement_status",
        ),
        Index(
            "uq_modulo_entitlement_empresa",
            "company_id",
            "modulo_id",
            unique=True,
            postgresql_where=text("projeto_id IS NULL AND pesquisa_id IS NULL"),
            sqlite_where=text("projeto_id IS NULL AND pesquisa_id IS NULL"),
        ),
        Index(
            "uq_modulo_entitlement_projeto",
            "company_id",
            "modulo_id",
            "projeto_id",
            unique=True,
            postgresql_where=text("projeto_id IS NOT NULL AND pesquisa_id IS NULL"),
            sqlite_where=text("projeto_id IS NOT NULL AND pesquisa_id IS NULL"),
        ),
        Index(
            "uq_modulo_entitlement_pesquisa",
            "company_id",
            "modulo_id",
            "pesquisa_id",
            unique=True,
            postgresql_where=text("projeto_id IS NULL AND pesquisa_id IS NOT NULL"),
            sqlite_where=text("projeto_id IS NULL AND pesquisa_id IS NOT NULL"),
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    company_id = Column(Integer, ForeignKey("companies.id"), nullable=False, index=True)
    modulo_id = Column(Integer, ForeignKey("modulos.id"), nullable=False, index=True)
    projeto_id = Column(Integer, ForeignKey("projetos.id"), nullable=True, index=True)
    pesquisa_id = Column(Integer, ForeignKey("pesquisas.id"), nullable=True, index=True)
    status = Column(String(20), nullable=False, default="ATIVO", server_default=text("'ATIVO'"))
    inicia_em = Column(DateTime(timezone=True), nullable=True)
    expira_em = Column(DateTime(timezone=True), nullable=True)
    criado_por_usuario_id = Column(Integer, ForeignKey("usuarios.id"), nullable=True)
    criado_em = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    atualizado_em = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    modulo = relationship("Modulo", back_populates="entitlements")
    funcionalidades = relationship(
        "ModuloEntitlementFuncionalidade",
        back_populates="entitlement",
        cascade="all, delete-orphan",
    )


class ModuloEntitlementFuncionalidade(Base):
    """Concessao explicita: novas features nunca entram em contratos antigos."""

    __tablename__ = "modulo_entitlement_funcionalidades"
    __table_args__ = (
        UniqueConstraint(
            "entitlement_id",
            "funcionalidade_id",
            name="uq_entitlement_funcionalidade",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    entitlement_id = Column(
        Integer, ForeignKey("modulo_entitlements.id", ondelete="CASCADE"), nullable=False, index=True
    )
    funcionalidade_id = Column(
        Integer, ForeignKey("modulo_funcionalidades.id", ondelete="CASCADE"), nullable=False, index=True
    )
    criado_em = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    entitlement = relationship("ModuloEntitlement", back_populates="funcionalidades")
    funcionalidade = relationship("ModuloFuncionalidade")

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

    # GLOBAL aparece em todos os setores; TERRITORIAL so nos setores cujo
    # municipio resolvido esta em `territorios_municipais`. Explicito de
    # proposito: "sem municipio associado" nao pode ser lido como GLOBAL.
    aplicabilidade = Column(
        String(20), nullable=False, default="GLOBAL", server_default=text("'GLOBAL'")
    )
    
    pesquisa = relationship("Pesquisa", back_populates="perguntas")
    respostas = relationship("Resposta", back_populates="pergunta")
    territorios_municipais = relationship(
        "PerguntaTerritorioEleitoral",
        back_populates="pergunta",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    
    # CORREÇÃO AQUI: Adicionamos foreign_keys para desambiguar
    opcoes = relationship(
        "Opcao", 
        back_populates="pergunta", 
        cascade="all, delete-orphan",
        foreign_keys="Opcao.pergunta_id" 
    )

class PerguntaTerritorioEleitoral(Base):
    """Municipios em que uma Pergunta TERRITORIAL e apresentada.

    O alvo e um TerritorioEleitoral de tipo MUNICIPIO, que pertence a UMA
    versao de BaseEleitoral. Nao existe identidade municipal estavel (sem IBGE,
    sem entidade propria), entao trocar a base principal do projeto deixa
    estas associacoes apontando para a base anterior: elas continuam
    consistentes, mas os setores compostos pela base nova nao as encontram.
    """

    __tablename__ = "pergunta_territorio_eleitoral"
    __table_args__ = (
        UniqueConstraint(
            "pergunta_id", "territorio_eleitoral_id", name="uq_pergunta_territorio"
        ),
        Index("ix_pergunta_territorio_pergunta", "pergunta_id"),
        Index("ix_pergunta_territorio_territorio", "territorio_eleitoral_id"),
    )

    id = Column(Integer, primary_key=True)
    pergunta_id = Column(
        Integer, ForeignKey("perguntas.id", ondelete="CASCADE"), nullable=False
    )
    territorio_eleitoral_id = Column(
        Integer,
        ForeignKey("territorio_eleitoral.id", ondelete="CASCADE"),
        nullable=False,
    )

    pergunta = relationship("Pergunta", back_populates="territorios_municipais")
    territorio_eleitoral = relationship("TerritorioEleitoral")


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
        Index("ix_coletas_setor_id", "setor_id"),
    )

    id = Column(Integer, primary_key=True, index=True)
    pesquisa_id = Column(Integer, ForeignKey("pesquisas.id"), nullable=False)
    agente_id = Column(Integer, ForeignKey("usuarios.id"), nullable=False)
    company_id = Column(Integer, ForeignKey("companies.id"), nullable=False)
    client_uuid = Column(String(36), nullable=False)
    setor_id = Column(Integer, ForeignKey("setores.id"), nullable=True)

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
    setor = relationship("Setor", back_populates="coletas")
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

    # ADR-035: municipio operacional de referencia (TerritorioEleitoral tipo
    # MUNICIPIO da Base principal do Projeto). Agregacao para Cota por Perfil;
    # NAO substitui a composicao eleitoral. Nullable: historico/analitico.
    municipio_territorio_id = Column(
        Integer, ForeignKey("territorio_eleitoral.id"), nullable=True, index=True
    )
    municipio_referencia = relationship("TerritorioEleitoral", foreign_keys=[municipio_territorio_id])
    
    # Relacionamentos
    pesquisa = relationship("Pesquisa", back_populates="setores")
    # passive_deletes: sem isto o ORM CARREGA as coletas ao apagar o setor e
    # emite UPDATE coletas SET setor_id = NULL antes do DELETE -- desvinculando
    # o historico em silencio, com HTTP 200. O setor faz parte da identidade
    # daquela coleta; quem decide e a FK fk_coletas_setor_id_setores (NO
    # ACTION), que barra o DELETE e deixa o endpoint traduzir para 409.
    coletas = relationship("Coleta", back_populates="setor", passive_deletes=True)
    agente = relationship("Usuario", foreign_keys=[agente_id]) # Legado: responsavel singular
    atribuicoes_agentes = relationship(
        "SetorAgente",
        back_populates="setor",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    agentes = relationship(
        "Usuario",
        secondary="setor_agentes",
        primaryjoin=lambda: and_(
            Setor.id == SetorAgente.setor_id,
            SetorAgente.ativo.is_(True),
        ),
        secondaryjoin=lambda: Usuario.id == SetorAgente.agente_id,
        viewonly=True,
        order_by=lambda: Usuario.id,
    )

    @property
    def agente_ids(self):
        return [agente.id for agente in self.agentes]


class SetorAgente(Base):
    """Atribuicao N:N; ``Setor.agente_id`` permanece como legado."""

    __tablename__ = "setor_agentes"
    __table_args__ = (
        UniqueConstraint("setor_id", "agente_id", name="uq_setor_agentes_setor_agente"),
        Index("ix_setor_agentes_setor_id", "setor_id"),
        Index("ix_setor_agentes_agente_id", "agente_id"),
    )

    id = Column(Integer, primary_key=True)
    setor_id = Column(
        Integer,
        ForeignKey("setores.id", ondelete="CASCADE"),
        nullable=False,
    )
    agente_id = Column(
        Integer,
        ForeignKey("usuarios.id", ondelete="CASCADE"),
        nullable=False,
    )
    ativo = Column(Boolean, nullable=False, default=True, server_default=text("true"))

    setor = relationship("Setor", back_populates="atribuicoes_agentes")
    agente = relationship(
        "Usuario",
        back_populates="atribuicoes_setores",
        foreign_keys=[agente_id],
    )


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



# ==============================================================================
# COMPOSICAO ELEITORAL DO SETOR
# ==============================================================================
# Setor e divisao operacional/analitica da Pesquisa; TerritorioEleitoral pertence
# a Base Eleitoral. Sao conceitos distintos e o sistema nao infere um do outro:
# a composicao e declarada pelo usuario, nunca deduzida por geometria, nome ou
# proximidade.
#
# O schema e generico (aponta para territorio_eleitoral.id, qualquer tipo). A
# REGRA de servico hoje aceita apenas BAIRRO, porque e a unica unidade com
# cobertura de eleitorado na Base atual. Quando existir base com SECAO, a
# estrutura ja comporta.


class SetorTerritorioEleitoral(Base):
    """Unidades da Base Eleitoral que compoem o universo de um Setor.

    N:N. Sem company_id: o tenant deriva de setor -> pesquisa -> projeto. Sem
    pesquisa_id: e derivavel por setor.pesquisa_id, e denormalizar so para
    viabilizar um UNIQUE(pesquisa_id, territorio) descreveria a regra errada --
    a exclusividade vale entre setores ANALITICOS, o que depende de
    setores.finalidade e nao cabe num UNIQUE simples.
    """

    __tablename__ = "setor_territorio_eleitoral"
    __table_args__ = (
        # Impede o mesmo bairro duas vezes no MESMO setor. A exclusividade
        # ENTRE setores analiticos vive no servico (depende de finalidade).
        UniqueConstraint(
            "setor_id", "territorio_eleitoral_id", name="uq_setor_territorio"
        ),
        Index("ix_setor_territorio_setor", "setor_id"),
        Index("ix_setor_territorio_territorio", "territorio_eleitoral_id"),
    )

    id = Column(Integer, primary_key=True)
    # Setor tem delete FISICO (endpoint delete_setor_by_projeto_pesquisa):
    # CASCADE, pois o vinculo nao significa nada sem o setor.
    setor_id = Column(
        Integer, ForeignKey("setores.id", ondelete="CASCADE"), nullable=False
    )
    # Mesma politica de lideranca_territorio_eleitoral: territorio morre junto
    # com a base, e o vinculo morre junto com o territorio.
    territorio_eleitoral_id = Column(
        Integer, ForeignKey("territorio_eleitoral.id", ondelete="CASCADE"), nullable=False
    )
    criado_em = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    setor = relationship("Setor", foreign_keys=[setor_id])
    territorio_eleitoral = relationship(
        "TerritorioEleitoral", foreign_keys=[territorio_eleitoral_id]
    )

# ==============================================================================
# GESTAO DE LIDERANCAS
# ==============================================================================
# LiderancaPolitica e a PESSOA cadastrada para a campanha. Nada a ver com
# TipoMapaEstrategico.LIDERANCA_SETOR, que designa a opcao mais votada em um
# setor e permanece intacto.
#
# A lideranca pertence ao PROJETO (campanha) e sobrevive as ondas. Setor e cota
# variam por onda, entao vivem em lideranca_pesquisa_config; os bairros da Base
# Eleitoral sao a ancora territorial estavel, em lideranca_territorio_eleitoral.
#
# POSICIONAMENTO e atributo da lideranca, nao entidade separada: BASE e OPOSICAO
# sao a mesma pessoa cadastrada em campos politicos opostos, com o mesmo CRUD,
# a mesma cota e os mesmos bairros. Nao existe tabela de oposicao (ADR).
POSICIONAMENTOS_LIDERANCA = ("BASE", "OPOSICAO", "INDEFINIDA")
# Lideranca ja cadastrada nunca e presumida aliada: o default e INDEFINIDA.
POSICIONAMENTO_LIDERANCA_PADRAO = "INDEFINIDA"


class LiderancaPolitica(Base):
    __tablename__ = "liderancas_politicas"
    __table_args__ = (
        CheckConstraint(
            _sql_in("posicionamento", POSICIONAMENTOS_LIDERANCA),
            name="ck_liderancas_politicas_posicionamento",
        ),
        Index("ix_liderancas_politicas_projeto", "projeto_id"),
        Index("ix_liderancas_politicas_ativo", "ativo"),
        Index("ix_liderancas_politicas_posicionamento", "posicionamento"),
    )

    id = Column(Integer, primary_key=True)
    # Raiz: Projeto = campanha. O tenant e derivado por projeto.company_id.
    projeto_id = Column(Integer, ForeignKey("projetos.id"), nullable=False)
    nome = Column(String, nullable=False)
    # Preparada para o mapa de uma fase futura; nao usada nesta.
    localizacao = Column(Geometry(geometry_type="POINT", srid=4326), nullable=True)
    ativo = Column(Boolean, nullable=False, default=True, server_default=text("true"))
    # Campo politico da lideranca. Sem valor definido a lideranca e INDEFINIDA;
    # jamais BASE por omissao.
    posicionamento = Column(
        String,
        nullable=False,
        default=POSICIONAMENTO_LIDERANCA_PADRAO,
        server_default=POSICIONAMENTO_LIDERANCA_PADRAO,
    )
    criado_em = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    atualizado_em = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    projeto = relationship("Projeto", foreign_keys=[projeto_id])
    configs_pesquisa = relationship(
        "LiderancaPesquisaConfig",
        back_populates="lideranca",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    territorios = relationship(
        "LiderancaTerritorioEleitoral",
        back_populates="lideranca",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class LiderancaPesquisaConfig(Base):
    """Configuracao da lideranca em UMA onda: setor operacional e cota eleitoral."""

    __tablename__ = "lideranca_pesquisa_config"
    __table_args__ = (
        UniqueConstraint("lideranca_id", "pesquisa_id", name="uq_lideranca_pesquisa"),
        # NULL = cota ainda nao configurada; 0 = cota definida como zero.
        CheckConstraint(
            "cota_votos_validos IS NULL OR cota_votos_validos >= 0",
            name="ck_lideranca_config_cota",
        ),
        Index("ix_lideranca_config_pesquisa", "pesquisa_id"),
        Index("ix_lideranca_config_setor", "setor_id"),
    )

    id = Column(Integer, primary_key=True)
    lideranca_id = Column(
        Integer, ForeignKey("liderancas_politicas.id", ondelete="CASCADE"), nullable=False
    )
    pesquisa_id = Column(Integer, ForeignKey("pesquisas.id"), nullable=False)
    # Setor tem delete fisico e morre com a Pesquisa: SET NULL evita que a
    # configuracao historica da lideranca desapareca junto.
    setor_id = Column(Integer, ForeignKey("setores.id", ondelete="SET NULL"), nullable=True)
    # Cota em VOTOS VALIDOS. Nao confundir com Setor.meta, que e meta de coletas.
    cota_votos_validos = Column(Integer, nullable=True)
    criado_em = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    atualizado_em = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    lideranca = relationship("LiderancaPolitica", back_populates="configs_pesquisa")
    pesquisa = relationship("Pesquisa", foreign_keys=[pesquisa_id])
    setor = relationship("Setor", foreign_keys=[setor_id])


class LiderancaTerritorioEleitoral(Base):
    """Bairros da Base Eleitoral onde a lideranca atua. Ancora estavel entre ondas."""

    __tablename__ = "lideranca_territorio_eleitoral"
    __table_args__ = (
        UniqueConstraint(
            "lideranca_id", "territorio_eleitoral_id", name="uq_lideranca_territorio"
        ),
        Index("ix_lideranca_territorio_territorio", "territorio_eleitoral_id"),
    )

    id = Column(Integer, primary_key=True)
    lideranca_id = Column(
        Integer, ForeignKey("liderancas_politicas.id", ondelete="CASCADE"), nullable=False
    )
    territorio_eleitoral_id = Column(
        Integer, ForeignKey("territorio_eleitoral.id", ondelete="CASCADE"), nullable=False
    )
    criado_em = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    lideranca = relationship("LiderancaPolitica", back_populates="territorios")
    territorio_eleitoral = relationship(
        "TerritorioEleitoral", foreign_keys=[territorio_eleitoral_id]
    )


# ==============================================================================
# TENTATIVA DE CAMPO (PROMPT 03)
#
# Abordagem operacional do agente. NAO e uma Coleta: registra que houve uma
# abordagem, onde, quando, por quem e com que resultado. Quando a abordagem
# vira entrevista concluida, `coleta_id` aponta para a Coleta correspondente.
# Recusa, nao elegivel, desistencia etc. nunca criam Coleta.
# ==============================================================================
class TentativaCampo(Base):
    __tablename__ = "tentativas_campo"
    __table_args__ = (
        UniqueConstraint(
            "company_id", "client_uuid", name="uq_tentativas_campo_company_client_uuid"
        ),
        Index("ix_tentativas_campo_pesquisa_id", "pesquisa_id"),
        Index("ix_tentativas_campo_setor_id", "setor_id"),
        Index("ix_tentativas_campo_agente_id", "agente_id"),
    )

    id = Column(Integer, primary_key=True, index=True)
    # Idempotencia: gerado no Mobile na criacao local e estavel em todo retry.
    client_uuid = Column(String(36), nullable=False)
    pesquisa_id = Column(Integer, ForeignKey("pesquisas.id"), nullable=False)
    setor_id = Column(Integer, ForeignKey("setores.id"), nullable=True)
    # Sempre derivados do usuario autenticado, nunca do payload.
    agente_id = Column(Integer, ForeignKey("usuarios.id"), nullable=False)
    company_id = Column(Integer, ForeignKey("companies.id"), nullable=False)

    iniciada_em = Column(DateTime(timezone=True), nullable=False)
    encerrada_em = Column(DateTime(timezone=True), nullable=True)

    # Posicao da abordagem: o MESMO GeoPoint que vira localizacao_inicio da
    # Coleta quando a abordagem vira entrevista. Sem geometria PostGIS por
    # enquanto: lat/lon crus bastam para auditoria e para o mapa futuro.
    latitude = Column(Float, nullable=False)
    longitude = Column(Float, nullable=False)
    precisao_metros = Column(Float, nullable=True)
    capturada_em = Column(DateTime(timezone=True), nullable=True)

    # Classificacao principal (RECUSA, NAO_ELEGIVEL, ..., CONCLUIDA) e detalhe.
    resultado = Column(String(30), nullable=False)
    motivo = Column(String(60), nullable=True)
    observacao = Column(Text, nullable=True)

    coleta_id = Column(Integer, ForeignKey("coletas.id"), nullable=True)

    criado_em = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    atualizado_em = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    pesquisa = relationship("Pesquisa")
    setor = relationship("Setor")
    agente = relationship("Usuario")
    coleta = relationship("Coleta")


# ==============================================================================
# COTAS DE PERFIL (PROMPT 05) -- amostral, ORIENTATIVA. Nunca bloqueia.
#
# Plano por pesquisa: aponta explicitamente a pergunta de Sexo (com o mapa de
# valores reais -> MASCULINO/FEMININO) e a pergunta de Idade (numerica ou
# categorica). A celula municipio x sexo x faixa e a verdade planejada; totais
# marginais sao derivados.
# ==============================================================================
class PlanoCotaPerfil(Base):
    __tablename__ = "planos_cota_perfil"
    __table_args__ = (
        UniqueConstraint("pesquisa_id", name="uq_planos_cota_perfil_pesquisa"),
    )

    id = Column(Integer, primary_key=True, index=True)
    pesquisa_id = Column(Integer, ForeignKey("pesquisas.id"), nullable=False)
    company_id = Column(Integer, ForeignKey("companies.id"), nullable=False)
    ativo = Column(Boolean, nullable=False, default=True, server_default=text("true"))
    pergunta_sexo_id = Column(Integer, ForeignKey("perguntas.id"), nullable=False)
    pergunta_idade_id = Column(Integer, ForeignKey("perguntas.id"), nullable=False)
    # NUMERICA: classifica pelo valor inteiro; CATEGORICA: pelos textos reais.
    modo_idade = Column(String(20), nullable=False, default="NUMERICA")
    # {"MASCULINO": ["Masculino", ...], "FEMININO": ["Feminino", ...]}
    sexo_valores = Column(JSON, nullable=False)
    criado_em = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    atualizado_em = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    pesquisa = relationship("Pesquisa")
    cotas = relationship(
        "CotaPerfil", back_populates="plano", cascade="all, delete-orphan", order_by="CotaPerfil.ordem"
    )


class CotaPerfil(Base):
    __tablename__ = "cotas_perfil"
    __table_args__ = (
        Index("ix_cotas_perfil_plano_id", "plano_id"),
        Index("ix_cotas_perfil_territorio_id", "territorio_eleitoral_id"),
        CheckConstraint("meta >= 0", name="ck_cotas_perfil_meta"),
    )

    id = Column(Integer, primary_key=True, index=True)
    plano_id = Column(Integer, ForeignKey("planos_cota_perfil.id", ondelete="CASCADE"), nullable=False)
    # Municipio (TerritorioEleitoral tipo MUNICIPIO da base principal do projeto).
    territorio_eleitoral_id = Column(Integer, ForeignKey("territorio_eleitoral.id"), nullable=False)
    sexo = Column(String(20), nullable=False)  # MASCULINO | FEMININO
    faixa_rotulo = Column(String(40), nullable=False)  # "60+", "45-59"...
    idade_min = Column(Integer, nullable=True)
    idade_max = Column(Integer, nullable=True)  # null = sem teto
    idade_valores = Column(JSON, nullable=True)  # modo CATEGORICA: textos reais
    meta = Column(Integer, nullable=False, default=0)
    ordem = Column(Integer, nullable=False, default=0)

    plano = relationship("PlanoCotaPerfil", back_populates="cotas")
    territorio = relationship("TerritorioEleitoral")


# ==============================================================================
# CONFIGURACAO DE CAMPO DA PESQUISA (PROMPT 06)
# Parametros operacionais de campo. Tabela propria (nao coluna em `pesquisas`)
# para nao alterar o contrato legado. Distancia nullable; > 0 quando definida.
# ==============================================================================
class ConfiguracaoCampoPesquisa(Base):
    __tablename__ = "configuracoes_campo_pesquisa"
    __table_args__ = (
        UniqueConstraint("pesquisa_id", name="uq_configuracoes_campo_pesquisa"),
        CheckConstraint(
            "distancia_recomendada_entre_abordagens_metros IS NULL OR "
            "distancia_recomendada_entre_abordagens_metros > 0",
            name="ck_configuracoes_campo_distancia",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    pesquisa_id = Column(Integer, ForeignKey("pesquisas.id"), nullable=False)
    company_id = Column(Integer, ForeignKey("companies.id"), nullable=False)
    distancia_recomendada_entre_abordagens_metros = Column(Integer, nullable=True)
    criado_em = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    atualizado_em = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    pesquisa = relationship("Pesquisa")
