# pesquisa360/db/models.py
from sqlalchemy import Column, Integer, String, Boolean, ForeignKey, Date, Text, DateTime, and_, Float
from sqlalchemy.orm import relationship, declarative_base
from sqlalchemy.dialects.postgresql import JSONB
from geoalchemy2 import Geometry
from sqlalchemy.sql import func

Base = declarative_base()

class Company(Base):
    __tablename__ = "companies"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    cnpj = Column(String, nullable=True)
    logo_url = Column(String, nullable=True)
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
    setores = relationship("Setor", back_populates="pesquisa", cascade="all, delete-orphan")

class Pergunta(Base):
    __tablename__ = "perguntas"
    id = Column(Integer, primary_key=True, index=True)
    texto_pergunta = Column(Text, nullable=False)
    tipo_pergunta = Column(String, nullable=False)
    ordem = Column(Integer, nullable=False)
    eh_obrigatoria = Column(Boolean, default=True, nullable=False)
    
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
    id = Column(Integer, primary_key=True, index=True)
    pesquisa_id = Column(Integer, ForeignKey("pesquisas.id"), nullable=False)
    agente_id = Column(Integer, ForeignKey("usuarios.id"), nullable=False)

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