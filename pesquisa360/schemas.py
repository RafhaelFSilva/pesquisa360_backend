# pesquisa360/schemas.py (versão final simplificada)

from enum import Enum
from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator, ConfigDict
from typing import Optional, List, Any, Union, Dict, Literal
from datetime import date, datetime, timezone
from enum import StrEnum
from uuid import UUID
import math
import re
from urllib.parse import urlparse
#from geoalchemy2.elements import WKBElement # <-- NOVA IMPORTAÇÃO
#from shapely.wkb import loads # <-- NOVA IMPORTAÇÃO

from .question_types import normalize_question_type
from .analytics import PapelAnalitico

# --- Esquemas para Respostas e Coletas ---
class RespostaBase(BaseModel):
    pergunta_id: int
    valor_resposta: str

class RespostaCreate(RespostaBase):
    pass

class Resposta(RespostaBase):
    id: int
    coleta_id: int
    class Config:
        from_attributes = True

# Schema para a ENTRADA de dados do app mobile
class Point(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)


class WebPoint(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)

class ColetaBase(BaseModel):
    setor_id: Optional[int] = Field(default=None, gt=0)
    localizacao_inicio: Optional[Point] = None
    localizacao_fim: Optional[Point] = None
    # Adicionamos aqui para que a API saiba que pode receber datas!
    data_inicio_coleta: Optional[datetime] = None 
    data_fim_coleta: Optional[datetime] = None
    foi_offline: bool = False

class ColetaCreate(ColetaBase):
    respostas: List[RespostaCreate]
    client_uuid: UUID
    # Garantimos que data_inicio seja obrigatória na criação, se desejar
    data_inicio_coleta: datetime
    # FASE F. Marcador de capability do cliente: True significa "este
    # questionario foi filtrado por pergunta_ids_aplicaveis", e liga a
    # validacao territorial rigida. Cliente legado nao envia -> False.
    questionario_territorial: bool = False

# Schema de SAÍDA simplificado. A conversão da geolocalização será feita manualmente no endpoint.
class Coleta(BaseModel):
    id: int
    client_uuid: UUID
    pesquisa_id: int
    agente_id: int
    setor_id: Optional[int] = None
    status_sincronizacao: str
    data_inicio_coleta: datetime
    data_fim_coleta: Optional[datetime] = None
    endereco_estimado: Optional[str] = None
    agente_nome: Optional[str] = None
    respostas: List[Resposta] = []

    class Config:
        from_attributes = True

class ColetaMonitoramento(BaseModel):
    id: int
    agente_id: int
    setor_id: Optional[int] = None
    agente_nome: Optional[str] = None
    data_inicio_coleta: datetime
    data_fim_coleta: Optional[datetime]
    localizacao_inicio: Optional[WebPoint]
    localizacao_fim: Optional[WebPoint]
    inconformidade_localizacao: bool
    endereco_estimado: Optional[str] = None
    foi_offline: bool = False
    status_sincronizacao: Optional[str] = None

    class Config:
        from_attributes = True

# --- Esquemas para Opções (ADICIONAR ESTE BLOCO) ---
class OpcaoBase(BaseModel):
    texto: str
    ordem: int = 0
    proxima_pergunta_id: Optional[int] = None # <-- Novo campo para o Pulo

class OpcaoCreate(OpcaoBase):
    pass

class Opcao(OpcaoBase):
    id: int
    pergunta_id: int
    class Config:
        from_attributes = True
        
class AplicabilidadePergunta(str, Enum):
    GLOBAL = "GLOBAL"
    TERRITORIAL = "TERRITORIAL"


class PerguntaBase(BaseModel):
    texto_pergunta: str
    tipo_pergunta: str
    ordem: int
    eh_obrigatoria: bool = True
    eh_resposta_espontanea: bool = False
    papel_analitico: Optional[PapelAnalitico] = None
    metadados_analiticos: Dict[str, Any] = Field(default_factory=dict)
    opcoes: Optional[List[OpcaoCreate]] = None # <--- Alterado para OpcaoCreate
    # FASE F. Default GLOBAL preserva todo cliente que ainda nao envia o campo.
    aplicabilidade: AplicabilidadePergunta = AplicabilidadePergunta.GLOBAL
    # TerritorioEleitoral de tipo MUNICIPIO da base principal do projeto.
    municipio_ids: Optional[List[int]] = None

    @field_validator("tipo_pergunta")
    @classmethod
    def normalize_tipo_pergunta(cls, value: str) -> str:
        return normalize_question_type(value)

    @field_validator("papel_analitico", mode="before")
    @classmethod
    def normalize_papel_analitico(cls, value):
        if value is None:
            return None
        return str(value).strip().upper()

    @field_validator("metadados_analiticos", mode="before")
    @classmethod
    def validate_metadados_analiticos(cls, value):
        if not isinstance(value, dict):
            raise ValueError("metadados_analiticos deve ser um objeto JSON")
        return value

class PerguntaCreate(PerguntaBase):
    ordem: Optional[int] = None

class PerguntaUpdate(BaseModel):
    texto_pergunta: Optional[str] = None
    tipo_pergunta: Optional[str] = None
    ordem: Optional[int] = None
    eh_obrigatoria: Optional[bool] = None
    eh_resposta_espontanea: Optional[bool] = None
    papel_analitico: Optional[PapelAnalitico] = None
    metadados_analiticos: Optional[Dict[str, Any]] = None
    opcoes: Optional[List[Any]] = None
    ativo: Optional[bool] = None
    # Ausente nao mexe na aplicabilidade/associacao; presente substitui.
    aplicabilidade: Optional[AplicabilidadePergunta] = None
    municipio_ids: Optional[List[int]] = None

    @field_validator("tipo_pergunta")
    @classmethod
    def normalize_tipo_pergunta(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        return normalize_question_type(value)

    @field_validator("papel_analitico", mode="before")
    @classmethod
    def normalize_papel_analitico(cls, value):
        if value is None:
            return None
        return str(value).strip().upper()

    @field_validator("metadados_analiticos", mode="before")
    @classmethod
    def validate_metadados_analiticos(cls, value):
        if not isinstance(value, dict):
            raise ValueError("metadados_analiticos deve ser um objeto JSON")
        return value

class PerguntaReordenarItem(BaseModel):
    id: int
    ordem: int = Field(..., ge=1)

class PerguntasReordenarPayload(BaseModel):
    perguntas: List[PerguntaReordenarItem]

class MunicipioPergunta(BaseModel):
    id: int
    nome: str


class Pergunta(PerguntaBase):
    id: int
    pesquisa_id: int
    ativo: bool
    opcoes: List[Opcao] = [] # <--- Força o uso do schema com ID na leitura
    municipio_ids: List[int] = Field(default_factory=list)
    municipios: List[MunicipioPergunta] = Field(default_factory=list)
    class Config:
        from_attributes = True

class PesquisaBase(BaseModel):
    titulo: str
    tipo_pesquisa: Optional[str] = None

class PesquisaCreate(PesquisaBase):
    pass

class PesquisaUpdate(BaseModel):
    titulo: Optional[str] = None
    tipo_pesquisa: Optional[str] = None
    ativo: Optional[bool] = None

class Pesquisa(PesquisaBase):
    id: int
    projeto_id: int
    ativo: bool
    cerca_eletronica: Optional[Any] = None
    tolerancia_metros: Optional[int] = None
    perguntas: List[Pergunta] = []
    coletas: List['Coleta'] = []
    class Config:
        from_attributes = True

class ProjetoBase(BaseModel):
    nome: str
    descricao: Optional[str] = None
    data_inicio: date
    data_fim: Optional[date] = None

class ProjetoCreate(ProjetoBase):
    coordenador_id: int
    company_id: Optional[int] = None

class PerfilBase(BaseModel):
    nome: str
    descricao: Optional[str] = None

class PerfilCreate(PerfilBase):
    pass

class UsuarioBase(BaseModel):
    email: EmailStr
    nome: Optional[str] = None
    perfil_id: int # Importante para o filtro do frontend
    ativo: Optional[bool] = True

class UsuarioCreate(UsuarioBase):
    # Opcional desde o fluxo de convite: SEM senha o usuario nasce inativo e
    # recebe link para definir a propria. Com senha, mantem o comportamento
    # anterior (criacao direta) -- compatibilidade com scripts e testes.
    senha: Optional[str] = None
    company_id: Optional[int] = None

class UsuarioAdminCreate(BaseModel):
    email: EmailStr
    nome: Optional[str] = None
    # Ver `UsuarioCreate.senha`: ausente = convite por e-mail.
    senha: Optional[str] = None
    perfil_id: int
    company_id: int
    ativo: Optional[bool] = True

class UsuarioAdminUpdate(BaseModel):
    nome: Optional[str] = None
    ativo: Optional[bool] = None
    perfil_id: Optional[int] = None
    company_id: Optional[int] = None
    senha: Optional[str] = None

class UsuarioPasswordReset(BaseModel):
    senha: str

class Perfil(PerfilBase):
    id: int
    class Config:
        from_attributes = True


class PerfilAtribuivel(BaseModel):
    id: int
    code: str
    nome: str

class ProjetoParaUsuario(ProjetoBase):
    id: int
    status: str
    class Config:
        from_attributes = True
    
class UsuarioParaProjeto(UsuarioBase):
    id: int
    class Config:
        from_attributes = True

class Usuario(UsuarioBase):
    id: int
    # EMPRESA PRINCIPAL/default (ADR-024). Nao e mais a autoridade de
    # autorizacao -- continua no contrato por compatibilidade e branding.
    company_id: int
    perfil_nome: Optional[str] = None
    # Aditivos: preenchidos onde a rota conhece a ACL (ex.: /usuarios/me/).
    company_ids: Optional[List[int]] = None
    multiempresa: Optional[bool] = None
    # ADR-037: papel normalizado e capacidades do perfil. O Web REPRESENTA
    # estas permissoes; quem autoriza continua sendo o Backend, rota a rota.
    papel: Optional[str] = None
    permissions: Optional[List[str]] = None

    class Config:
        from_attributes = True

def normalize_cnpj(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = re.sub(r"[.\-/\s]", "", str(value).strip())
    if not normalized:
        return None
    if not normalized.isdigit() or len(normalized) != 14:
        raise ValueError("CNPJ deve conter 14 digitos.")
    if len(set(normalized)) == 1:
        raise ValueError("CNPJ invalido.")

    def check_digit(base: str, weights: List[int]) -> str:
        remainder = sum(int(digit) * weight for digit, weight in zip(base, weights)) % 11
        return "0" if remainder < 2 else str(11 - remainder)

    first = check_digit(normalized[:12], [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2])
    second = check_digit(normalized[:12] + first, [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2])
    if normalized[-2:] != first + second:
        raise ValueError("CNPJ invalido.")
    return normalized


def normalize_company_name(value: str | None) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError("Nome da empresa e obrigatorio.")
    return normalized


def normalize_logo_url(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    if not normalized:
        return None
    if len(normalized) > 2048:
        raise ValueError("URL do logo excede o tamanho maximo.")
    parsed = urlparse(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("URL do logo deve usar HTTP ou HTTPS.")
    return normalized


class CompanyBase(BaseModel):
    name: str
    cnpj: Optional[str] = None
    logo_url: Optional[str] = None
    is_active: bool = True

    @field_validator("name", mode="before")
    @classmethod
    def validate_name(cls, value):
        return normalize_company_name(value)

    @field_validator("cnpj", mode="before")
    @classmethod
    def validate_cnpj(cls, value):
        return normalize_cnpj(value)

    @field_validator("logo_url", mode="before")
    @classmethod
    def validate_logo_url(cls, value):
        return normalize_logo_url(value)

class CompanyCreate(CompanyBase):
    pass

class CompanyUpdate(BaseModel):
    name: Optional[str] = None
    cnpj: Optional[str] = None
    logo_url: Optional[str] = None
    is_active: Optional[bool] = None

    @field_validator("name", mode="before")
    @classmethod
    def validate_name(cls, value):
        return normalize_company_name(value)

    @field_validator("cnpj", mode="before")
    @classmethod
    def validate_cnpj(cls, value):
        return normalize_cnpj(value)

    @field_validator("logo_url", mode="before")
    @classmethod
    def validate_logo_url(cls, value):
        return normalize_logo_url(value)

class CompanyRead(CompanyBase):
    id: int
    created_at: Optional[datetime] = None

    class Config:
        from_attributes = True

# --- SCHEMAS DE SETORES ---

class FinalidadeSetor(StrEnum):
    OPERACAO = "OPERACAO"
    RELATORIO = "RELATORIO"
    AMBOS = "AMBOS"


def _validar_agente_ids(value: Optional[List[int]]) -> Optional[List[int]]:
    if value is None:
        raise ValueError("agente_ids deve ser uma lista quando informado")
    if any(agente_id <= 0 for agente_id in value):
        raise ValueError("agente_ids deve conter apenas IDs positivos")
    if len(value) != len(set(value)):
        raise ValueError("agente_ids nao pode conter IDs duplicados")
    return value


class AgenteSetor(BaseModel):
    id: int
    nome: Optional[str] = None

    class Config:
        from_attributes = True

class SetorBase(BaseModel):
    nome: str
    meta: int
    agente_id: Optional[int] = None
    agente_ids: Optional[List[int]] = None
    tolerancia: int = 50
    finalidade: FinalidadeSetor = FinalidadeSetor.OPERACAO
    # ADR-035: opcional -- o Backend detecta pelo poligono/composicao; quando
    # informado, e validado contra a Base principal e a geometria.
    municipio_territorio_id: Optional[int] = Field(default=None, gt=0)

    @field_validator("agente_ids")
    @classmethod
    def validar_agente_ids(cls, value: Optional[List[int]]) -> Optional[List[int]]:
        return _validar_agente_ids(value)

class SetorCreate(SetorBase):
    # Receberemos a geometria como uma lista de coordenadas [[lat, lon], ...]
    geometria_coords: List[List[float]] 

    @field_validator("geometria_coords")
    @classmethod
    def validar_geometria_coords(cls, coords: List[List[float]]) -> List[List[float]]:
        for coord in coords:
            if len(coord) != 2:
                raise ValueError("Cada coordenada deve conter latitude e longitude.")
            latitude, longitude = coord
            if not -90 <= latitude <= 90:
                raise ValueError("Latitude deve estar entre -90 e 90.")
            if not -180 <= longitude <= 180:
                raise ValueError("Longitude deve estar entre -180 e 180.")
        return coords

class SetorGeofenceCreate(BaseModel):
    nome: str
    meta: int
    agente_id: Optional[int] = None
    agente_ids: Optional[List[int]] = None
    tolerancia_metros: Optional[int] = 50
    finalidade: FinalidadeSetor = FinalidadeSetor.OPERACAO
    geometria: Optional[List[dict]] = None
    poligono: Optional[List[dict]] = None
    municipio_territorio_id: Optional[int] = Field(default=None, gt=0)

    @field_validator("agente_ids")
    @classmethod
    def validar_agente_ids(cls, value: Optional[List[int]]) -> Optional[List[int]]:
        return _validar_agente_ids(value)

    def get_coords(self) -> List[List[float]]:
        coords = self.geometria or self.poligono
        if not coords:
            raise ValueError("geometria ou poligono é obrigatório")
        parsed = []
        for item in coords:
            if isinstance(item, dict):
                if "lat" in item and "lng" in item:
                    parsed.append([float(item["lat"]), float(item["lng"])])
                elif 0 in item and 1 in item:
                    parsed.append([float(item[0]), float(item[1])])
                else:
                    raise ValueError("Coordenada inválida: espere {lat, lng} ou [lat, lng].")
            else:
                parsed.append([float(item[0]), float(item[1])])
        for latitude, longitude in parsed:
            if not -90 <= latitude <= 90:
                raise ValueError("Latitude deve estar entre -90 e 90.")
            if not -180 <= longitude <= 180:
                raise ValueError("Longitude deve estar entre -180 e 180.")
        return parsed


class SetorUpdate(BaseModel):
    nome: Optional[str] = None
    meta: Optional[int] = None
    agente_id: Optional[int] = None
    agente_ids: Optional[List[int]] = None
    tolerancia_metros: Optional[int] = None
    finalidade: Optional[FinalidadeSetor] = None
    geometria: Optional[List[dict]] = None
    poligono: Optional[List[dict]] = None
    municipio_territorio_id: Optional[int] = Field(default=None, gt=0)

    @field_validator("agente_ids")
    @classmethod
    def validar_agente_ids(cls, value: Optional[List[int]]) -> Optional[List[int]]:
        return _validar_agente_ids(value)

    @field_validator("nome")
    @classmethod
    def validar_nome(cls, value: Optional[str]) -> str:
        if value is None or not value.strip():
            raise ValueError("Nome do setor e obrigatorio quando informado.")
        return value.strip()

    @field_validator("meta")
    @classmethod
    def validar_meta(cls, value: Optional[int]) -> int:
        if value is None or value <= 0:
            raise ValueError("Meta deve ser um inteiro positivo quando informada.")
        return value

    @field_validator("tolerancia_metros")
    @classmethod
    def validar_tolerancia(cls, value: Optional[int]) -> int:
        if value is None or value < 0:
            raise ValueError("Tolerancia deve ser maior ou igual a zero quando informada.")
        return value

    def get_coords(self) -> Optional[List[List[float]]]:
        geometry_fields = {"geometria", "poligono"}
        if not geometry_fields.intersection(self.model_fields_set):
            return None

        coords = SetorGeofenceCreate(
            nome="validacao",
            meta=1,
            geometria=self.geometria,
            poligono=self.poligono,
        ).get_coords()
        if len(coords) < 3:
            raise ValueError("O poligono deve conter pelo menos tres pontos.")
        return coords

class Setor(SetorBase):
    id: int
    pesquisa_id: int
    agente_ids: List[int] = Field(default_factory=list)
    agentes: List[AgenteSetor] = Field(default_factory=list)
    # Para visualização, retornaremos GeoJSON manualmente no endpoint, 
    # então aqui focamos nos dados alfanuméricos
    
    class Config:
        from_attributes = True

class PesquisaResumo(PesquisaBase):
    id: int
    projeto_id: int
    ativo: bool
    perguntas: List[Pergunta] = []
    class Config:
        from_attributes = True

class Projeto(ProjetoBase):
    id: int
    coordenador_id: int
    status: str
    # ADR-024: com ACL multiempresa, `GET /projetos/` pode devolver projetos de
    # empresas diferentes na mesma resposta. Sem o tenant do projeto, a UI nao
    # tem como agrupar nem rotular a origem. Campo aditivo e de leitura -- o
    # cliente nunca o envia de volta em dado operacional.
    company_id: Optional[int] = None
    coordenador: UsuarioParaProjeto
    pesquisas: List[PesquisaResumo] = [] # Usa resumo sem cerca_eletronica para evitar WKBElement
    class Config:
        from_attributes = True

class Token(BaseModel):
    access_token: str
    token_type: str
    # Aditivos: cliente antigo que so le `access_token` continua funcionando.
    refresh_token: Optional[str] = None
    expires_in: Optional[int] = None


class RefreshTokenRequest(BaseModel):
    refresh_token: str

class TokenData(BaseModel):
    email: Optional[EmailStr] = None


class FuncionalidadeComercial(BaseModel):
    chave: str
    nome: str


class ModuloComercial(BaseModel):
    chave: str
    nome: str
    funcionalidades: List[FuncionalidadeComercial]


class ModulosUsuarioResponse(BaseModel):
    modulos: List[ModuloComercial]


def _admin_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


class AdminEntitlementCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    modulo_id: int
    escopo: Literal["EMPRESA", "PROJETO", "PESQUISA"]
    projeto_id: Optional[int] = None
    pesquisa_id: Optional[int] = None
    inicia_em: Optional[datetime] = None
    expira_em: Optional[datetime] = None
    funcionalidade_ids: List[int] = Field(default_factory=list)

    @model_validator(mode="after")
    def validar_escopo_e_datas(self):
        esperado = {
            "EMPRESA": (False, False),
            "PROJETO": (True, False),
            "PESQUISA": (False, True),
        }[self.escopo]
        atual = (self.projeto_id is not None, self.pesquisa_id is not None)
        if atual != esperado:
            raise ValueError("Escopo incompativel com projeto_id/pesquisa_id.")
        if self.inicia_em and self.expira_em and _admin_utc(self.expira_em) < _admin_utc(self.inicia_em):
            raise ValueError("expira_em deve ser maior ou igual a inicia_em.")
        if len(self.funcionalidade_ids) != len(set(self.funcionalidade_ids)):
            raise ValueError("funcionalidade_ids nao pode conter duplicidades.")
        return self


class AdminEntitlementUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Optional[Literal["ATIVO", "SUSPENSO", "CANCELADO"]] = None
    inicia_em: Optional[datetime] = None
    expira_em: Optional[datetime] = None

    @model_validator(mode="after")
    def validar_datas(self):
        if self.inicia_em and self.expira_em and _admin_utc(self.expira_em) < _admin_utc(self.inicia_em):
            raise ValueError("expira_em deve ser maior ou igual a inicia_em.")
        return self


class AdminEntitlementFeaturesUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    funcionalidade_ids: List[int] = Field(default_factory=list)

    @field_validator("funcionalidade_ids")
    @classmethod
    def sem_duplicidades(cls, value):
        if len(value) != len(set(value)):
            raise ValueError("funcionalidade_ids nao pode conter duplicidades.")
        return value

class ResultadoOpcao(BaseModel):
    """Representa o resultado para uma única opção de resposta."""
    opcao: str
    contagem: int
    percentual: Optional[float] = None

class ResultadoPergunta(BaseModel):
    """Representa os resultados agregados para uma única pergunta."""
    pergunta_id: int
    texto_pergunta: str
    tipo_pergunta: str
    total: int = 0
    opcoes_resposta: Dict[str, int] = {}
    dados: List[Any] = []
    resultados: List[ResultadoOpcao]

class RelatorioPesquisa(BaseModel):
    """Representa o relatório completo de uma pesquisa."""
    pesquisa_id: int
    titulo_pesquisa: str
    total_coletas: int
    resultados: List[ResultadoPergunta]
    resultados_por_pergunta: List[ResultadoPergunta]
# --- NOVOS SCHEMAS OTIMIZADOS PARA SINCRONIZAÇÃO (Fase B App) ---

class PerguntaSync(PerguntaBase):
    id: int
    # Não precisa de mais nada, a base já tem texto, tipo, ordem, etc.
    class Config: from_attributes = True

class PesquisaSync(PesquisaBase):
    id: int
    perguntas: List[PerguntaSync] = [] # Aninha apenas as perguntas
    class Config: from_attributes = True

class ProjetoSync(ProjetoBase):
    id: int
    pesquisas: List[PesquisaSync] = [] # Aninha apenas as pesquisas
    class Config: from_attributes = True

class ProjetoUpdate(BaseModel):
    nome: Optional[str] = None
    descricao: Optional[str] = None
    status: Optional[str] = None
    data_inicio: Optional[date] = None
    data_fim: Optional[date] = None
    coordenador_id: Optional[int] = None
    company_id: Optional[int] = None

# --- NOVOS SCHEMAS PARA RELATÓRIOS AVANÇADOS ---

class ResultadoPergunta(BaseModel):
    """Representa os resultados agregados para uma única pergunta."""
    pergunta_id: int
    texto_pergunta: str
    tipo_pergunta: str
    resultados: List[ResultadoOpcao]
    # NOVOS CAMPOS ESTATÍSTICOS (OPCIONAIS)
    media: Optional[float] = None
    mediana: Optional[float] = None
    moda: Optional[Union[float, str]] = None # Moda pode ser numérica ou textual
    desvio_padrao: Optional[float] = None

class CrosstabRequest(BaseModel):
    """Define os parâmetros para uma requisição de cruzamento de dados."""
    pergunta_linha_id: int
    pergunta_coluna_id: int

class CrosstabCell(BaseModel):
    """Representa uma única célula na tabela de cruzamento."""
    valor_coluna: str
    contagem: int
    percentual: float

class CrosstabRow(BaseModel):
    """Representa uma linha na tabela de cruzamento (uma opção da pergunta-linha)."""
    valor_linha: str
    celulas: List[CrosstabCell]

class CrosstabResponse(BaseModel):
    """Representa a resposta completa do cruzamento de dados."""
    pergunta_linha: str
    pergunta_coluna: str
    dados: List[CrosstabRow]


class NivelTerritorial(StrEnum):
    """Niveis territoriais suportados. Hoje apenas SETOR e operacional."""

    SETOR = "SETOR"


class TipoDimensaoCruzamento(StrEnum):
    PERGUNTA = "PERGUNTA"
    TERRITORIO = "TERRITORIO"


class CruzamentoDimensaoConfig(BaseModel):
    """Descriptor de uma posicao da sequencia analitica. Sem ID sentinela."""

    model_config = ConfigDict(extra="forbid")

    tipo: TipoDimensaoCruzamento
    pergunta_id: Optional[int] = None
    nivel: Optional[NivelTerritorial] = None

    @model_validator(mode="after")
    def validate_dimensao(self):
        if self.tipo == TipoDimensaoCruzamento.PERGUNTA:
            if self.pergunta_id is None:
                raise ValueError("pergunta_id e obrigatorio para dimensao do tipo PERGUNTA")
            if self.nivel is not None:
                raise ValueError("nivel nao se aplica a dimensao do tipo PERGUNTA")
        else:
            if self.pergunta_id is not None:
                raise ValueError("pergunta_id nao se aplica a dimensao do tipo TERRITORIO")
            if self.nivel is None:
                self.nivel = NivelTerritorial.SETOR
        return self


class CruzamentoFiltroTerritorial(BaseModel):
    """Restringe o universo antes do cruzamento. Guarda referencias, nunca geometria."""

    model_config = ConfigDict(extra="forbid")

    nivel: NivelTerritorial = NivelTerritorial.SETOR
    setor_ids: List[int] = Field(default_factory=list)
    incluir_sem_setor: bool = False

    @model_validator(mode="after")
    def validate_filtro(self):
        if len(self.setor_ids) != len(set(self.setor_ids)):
            raise ValueError("setor_ids nao pode conter itens duplicados")
        return self

    @property
    def ativo(self) -> bool:
        return bool(self.setor_ids) or self.incluir_sem_setor


class ModoAlvoCruzamento(StrEnum):
    FULL_DISTRIBUTION = "FULL_DISTRIBUTION"
    ONE_VS_REST = "ONE_VS_REST"


def _normalizar_lista_alvo(valores, campo: str) -> list[str]:
    normalizados = [str(item).strip() for item in valores]
    if any(not item for item in normalizados):
        raise ValueError(f"{campo} nao pode conter valores vazios")
    if len(normalizados) != len(set(normalizados)):
        raise ValueError(f"{campo} nao pode conter itens duplicados")
    return normalizados


def _validar_regras_alvo(alvo):
    """Regras comuns ao alvo do request e ao alvo persistido na visao salva."""
    valor = alvo.valor.strip() if isinstance(alvo.valor, str) else alvo.valor
    excluidos = _normalizar_lista_alvo(alvo.valores_excluidos, "valores_excluidos")
    preservados = _normalizar_lista_alvo(alvo.valores_preservados, "valores_preservados")
    if set(excluidos) & set(preservados):
        raise ValueError("um valor nao pode ser excluido e preservado ao mesmo tempo")
    if alvo.modo == ModoAlvoCruzamento.ONE_VS_REST:
        if not valor:
            raise ValueError("valor e obrigatorio no modo ONE_VS_REST")
        if valor in excluidos:
            raise ValueError("valor do alvo nao pode estar em valores_excluidos")
        if valor in preservados:
            raise ValueError("valor do alvo nao pode estar em valores_preservados")
    elif valor:
        raise ValueError("valor deve ser ausente no modo FULL_DISTRIBUTION")
    alvo.valor = valor
    alvo.valores_excluidos = excluidos
    alvo.valores_preservados = preservados
    return alvo


class CruzamentoAlvo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pergunta_id: int
    modo: ModoAlvoCruzamento = ModoAlvoCruzamento.FULL_DISTRIBUTION
    valor: Optional[str] = None
    valores_excluidos: List[str] = Field(default_factory=list)
    valores_preservados: List[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_alvo(self):
        return _validar_regras_alvo(self)


class CruzamentoFiltroResposta(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pergunta_id: int
    valores: List[str] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_values(self):
        normalized = [value.strip() for value in self.valores]
        if any(not value for value in normalized):
            raise ValueError("valores nao pode conter valores vazios")
        if len(normalized) != len(set(normalized)):
            raise ValueError("valores nao pode conter itens duplicados")
        self.valores = normalized
        return self


class CruzamentoMultidimensionalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # min_length=1 porque a sequencia pode combinar uma pergunta com a dimensao
    # territorial; sem `dimensoes` o minimo de duas perguntas continua valendo.
    pergunta_ids: List[int] = Field(min_length=1)
    incluir_sem_resposta: bool = False
    profundidade_maxima: Optional[int] = Field(default=None, ge=1)
    filtros_respostas: List[CruzamentoFiltroResposta] = Field(default_factory=list)
    alvo: Optional[CruzamentoAlvo] = None
    dimensoes: Optional[List[CruzamentoDimensaoConfig]] = None
    filtro_territorial: Optional[CruzamentoFiltroTerritorial] = None

    @model_validator(mode="after")
    def validate_dimensions(self):
        if len(self.pergunta_ids) != len(set(self.pergunta_ids)):
            raise ValueError("pergunta_ids nao pode conter IDs duplicados")
        # A sequencia analitica e [alvo] + dimensoes de aprofundamento.
        if self.dimensoes is not None:
            total_dimensoes = len(self.dimensoes) + (1 if self.alvo is not None else 0)
        else:
            total_dimensoes = len(self.pergunta_ids)
        if total_dimensoes < 2:
            raise ValueError("o cruzamento exige ao menos duas dimensoes")
        if self.profundidade_maxima is not None and self.profundidade_maxima > total_dimensoes:
            raise ValueError("profundidade_maxima nao pode exceder a quantidade de dimensoes")
        filter_ids = [item.pergunta_id for item in self.filtros_respostas]
        if len(filter_ids) != len(set(filter_ids)):
            raise ValueError("filtros_respostas nao pode repetir pergunta_id")
        if not set(filter_ids).issubset(self.pergunta_ids):
            raise ValueError("filtros_respostas deve usar apenas IDs de pergunta_ids")
        if self.alvo is not None and self.alvo.pergunta_id not in self.pergunta_ids:
            raise ValueError("alvo.pergunta_id deve estar em pergunta_ids")
        alvo_id = self.alvo.pergunta_id if self.alvo is not None else None
        if self.dimensoes is not None:
            territoriais = [
                item for item in self.dimensoes
                if item.tipo == TipoDimensaoCruzamento.TERRITORIO
            ]
            if len(territoriais) > 1:
                raise ValueError("dimensoes aceita no maximo uma dimensao territorial")
            perguntas = [
                item.pergunta_id for item in self.dimensoes
                if item.tipo == TipoDimensaoCruzamento.PERGUNTA
            ]
            if alvo_id is not None:
                # O alvo e a raiz da arvore: nunca se repete entre as dimensoes de
                # aprofundamento. A ordem das dimensoes e livre e definida pelo usuario.
                if alvo_id in perguntas:
                    raise ValueError("o alvo nao pode aparecer em dimensoes")
                esperado = [item for item in self.pergunta_ids if item != alvo_id]
                if sorted(perguntas) != sorted(esperado):
                    raise ValueError("dimensoes deve conter as perguntas de pergunta_ids exceto o alvo")
            elif perguntas != self.pergunta_ids:
                # pergunta_ids continua sendo o contrato canonico para consumidores antigos.
                raise ValueError("dimensoes deve conter as mesmas perguntas de pergunta_ids, na mesma ordem")
        return self


class CruzamentoOpcaoValor(BaseModel):
    valor_chave: str
    rotulo: str
    ordem: Optional[int] = None
    contagem_entrevistas: int
    origem: str


class CruzamentoOpcaoDimensao(BaseModel):
    pergunta_id: int
    ordem: int
    texto_pergunta: str
    tipo_pergunta: str
    eh_resposta_espontanea: bool
    papel_analitico: Optional[PapelAnalitico] = None
    metadados_analiticos: Dict[str, Any] = Field(default_factory=dict)
    cardinalidade_observada: int
    valores: List[CruzamentoOpcaoValor]


class CruzamentoOpcaoTerritorioValor(BaseModel):
    setor_id: Optional[int] = None
    valor_chave: str
    rotulo: str
    contagem_entrevistas: int
    origem: str


class CruzamentoOpcaoTerritorio(BaseModel):
    nivel: NivelTerritorial
    valores: List[CruzamentoOpcaoTerritorioValor] = Field(default_factory=list)


class CruzamentoOpcoesResponse(BaseModel):
    pesquisa_id: int
    dimensoes: List[CruzamentoOpcaoDimensao]
    territorios: List[CruzamentoOpcaoTerritorio] = Field(default_factory=list)


class CruzamentoValorCaminho(BaseModel):
    pergunta_id: Optional[int] = None
    valor_chave: str
    rotulo: str
    tipo: TipoDimensaoCruzamento = TipoDimensaoCruzamento.PERGUNTA
    nivel_territorial: Optional[NivelTerritorial] = None


class CruzamentoDimensao(BaseModel):
    posicao: int
    pergunta_id: Optional[int] = None
    tipo: TipoDimensaoCruzamento = TipoDimensaoCruzamento.PERGUNTA
    nivel_territorial: Optional[NivelTerritorial] = None
    ordem: int
    texto_pergunta: str
    tipo_pergunta: str
    eh_obrigatoria: bool
    eh_resposta_espontanea: bool
    papel_analitico: Optional[PapelAnalitico] = None
    metadados_analiticos: Dict[str, Any] = Field(default_factory=dict)
    cardinalidade_observada: int
    eh_multipla_resposta: bool


class CruzamentoNodo(BaseModel):
    nivel: int
    caminho: List[CruzamentoValorCaminho]
    contagem_entrevistas: int
    base_pai: int
    percentual_pai: float
    percentual_total: float
    tem_filhos: bool


class CruzamentoAlvoMetadados(BaseModel):
    pergunta_id: int
    modo: ModoAlvoCruzamento
    valor: Optional[str] = None
    valores_excluidos: List[str] = Field(default_factory=list)
    valores_preservados: List[str] = Field(default_factory=list)


class CruzamentoMetadadosExecucao(BaseModel):
    quantidade_dimensoes_solicitadas: int
    quantidade_dimensoes_processadas: int
    profundidade_maxima: int
    quantidade_nodos: int
    incluir_sem_resposta: bool
    possui_multipla_resposta: bool
    somatorio_percentuais_pode_exceder_100: bool
    alvo: Optional[CruzamentoAlvoMetadados] = None
    filtro_territorial: Optional[CruzamentoFiltroTerritorial] = None
    dimensao_territorial: Optional[NivelTerritorial] = None


class CruzamentoMultidimensionalResponse(BaseModel):
    pesquisa_id: int
    total_entrevistas: int
    base_valida: int
    dimensoes: List[CruzamentoDimensao]
    nodos: List[CruzamentoNodo]
    metadados_execucao: CruzamentoMetadadosExecucao
    avisos: List[str]

class TipoMapaEstrategico(StrEnum):
    COBERTURA = "COBERTURA"
    DISTRIBUICAO_SETOR = "DISTRIBUICAO_SETOR"
    RESULTADO_SETOR = "RESULTADO_SETOR"
    LIDERANCA_SETOR = "LIDERANCA_SETOR"

class MapaPreviewRequest(BaseModel):
    tipo_mapa: TipoMapaEstrategico
    setor_ids: Optional[List[int]] = None
    pergunta_id: Optional[int] = None
    resposta: Optional[str] = None
    agente_ids: Optional[List[int]] = None
    data_inicio: Optional[datetime] = None
    data_fim: Optional[datetime] = None


# --- Mapa de Respostas Georreferenciadas (Fase 2A) ---------------------------
#
# Espacializa cada coleta como um ponto, categorizado pela resposta a uma
# pergunta principal. Cor NAO faz parte deste contrato: `valor -> cor` e
# decisao do cliente.
#
# Modo CRUZADO (opcional): `pergunta_secundaria_id` acrescenta uma segunda
# categoria por coleta. O par nasce SEMPRE do mesmo `coleta_id` -- nunca do
# encontro de dois totais agregados independentes. Ausente, o contrato e
# byte-a-byte o de antes.


class MapaRespostasGeoRequest(BaseModel):
    """Recorte do universo de coletas a espacializar.

    company_id nunca entra aqui: o tenant vem do JWT. Configuracao visual
    (cores, zoom, tamanho de marcador) tambem nao pertence ao contrato.
    """

    model_config = ConfigDict(extra="forbid")

    # Pergunta que define a CATEGORIA de cada ponto.
    pergunta_id: int
    # Categorias a exibir; so estas voltam.
    valores: List[str] = Field(min_length=1)
    # Segunda pergunta do cruzamento geografico. None = modo simples.
    pergunta_secundaria_id: Optional[int] = None
    # Categorias da segunda pergunta a exibir. None/ausente = TODAS as
    # categorias elegiveis dela, que e o comportamento do contrato anterior.
    valores_secundarios: Optional[List[str]] = None
    # Dimensoes adicionais: OR dentro da pergunta, AND entre perguntas.
    filtros_respostas: List[CruzamentoFiltroResposta] = Field(default_factory=list)
    setor_ids: Optional[List[int]] = None
    agente_ids: Optional[List[int]] = None

    # --- Agrupamento em "Outros" ------------------------------------------
    #
    # Muda o PAPEL de `valores`: sem agrupamento ele FILTRA (quem nao esta na
    # lista sai do mapa); com agrupamento ele DESTACA (quem nao esta na lista
    # continua no mapa, reunido em um unico balde). Default False mantem o
    # comportamento legado byte-a-byte.
    agrupar_nao_selecionadas: bool = False
    agrupar_nao_selecionadas_secundaria: bool = False

    # Categorias que NUNCA entram no balde, mesmo desmarcadas: Branco/Nulo,
    # NS/NR e afins. O servidor NAO adivinha quais sao -- nao existe
    # classificador semantico no projeto, e inferir por texto mudaria em
    # silencio a semantica dessas respostas. Quem decide e o cliente, de forma
    # explicita e visivel ao usuario.
    valores_preservados: List[str] = Field(default_factory=list)
    valores_preservados_secundarios: List[str] = Field(default_factory=list)

    @field_validator("valores")
    @classmethod
    def validar_valores(cls, value: List[str]) -> List[str]:
        normalizados = [item.strip() for item in value]
        if any(not item for item in normalizados):
            raise ValueError("valores nao pode conter itens vazios")
        if len(normalizados) != len(set(normalizados)):
            raise ValueError("valores nao pode conter itens duplicados")
        return normalizados

    @field_validator("valores_secundarios")
    @classmethod
    def validar_valores_secundarios(cls, value: Optional[List[str]]) -> Optional[List[str]]:
        # None e "sem recorte"; lista vazia seria "nenhuma categoria", que nao
        # e uma pergunta a se fazer -- o mapa ficaria vazio por construcao.
        if value is None:
            return None
        normalizados = [item.strip() for item in value]
        if not normalizados:
            raise ValueError("valores_secundarios nao pode ser uma lista vazia")
        if any(not item for item in normalizados):
            raise ValueError("valores_secundarios nao pode conter itens vazios")
        if len(normalizados) != len(set(normalizados)):
            raise ValueError("valores_secundarios nao pode conter itens duplicados")
        return normalizados

    @field_validator("valores_preservados", "valores_preservados_secundarios")
    @classmethod
    def validar_preservados(cls, value: List[str]) -> List[str]:
        # Lista vazia e o normal: nem toda pergunta tem categoria especial.
        normalizados = [item.strip() for item in value]
        if any(not item for item in normalizados):
            raise ValueError("valores preservados nao pode conter itens vazios")
        if len(normalizados) != len(set(normalizados)):
            raise ValueError("valores preservados nao pode conter itens duplicados")
        return normalizados

    @model_validator(mode="after")
    def validar_filtros(self):
        ids = [item.pergunta_id for item in self.filtros_respostas]
        if len(ids) != len(set(ids)):
            raise ValueError("filtros_respostas nao pode repetir pergunta_id")
        # Preservar so faz sentido quando existe um balde de onde preservar.
        if self.valores_preservados and not self.agrupar_nao_selecionadas:
            raise ValueError(
                "valores_preservados exige agrupar_nao_selecionadas"
            )
        if self.valores_preservados_secundarios and not self.agrupar_nao_selecionadas_secundaria:
            raise ValueError(
                "valores_preservados_secundarios exige agrupar_nao_selecionadas_secundaria"
            )
        if self.agrupar_nao_selecionadas_secundaria and self.pergunta_secundaria_id is None:
            raise ValueError(
                "agrupar_nao_selecionadas_secundaria exige pergunta_secundaria_id"
            )
        if self.pergunta_secundaria_id is None and self.valores_secundarios is not None:
            raise ValueError(
                "valores_secundarios exige pergunta_secundaria_id"
            )
        if self.pergunta_secundaria_id is not None:
            # Cruzar uma pergunta com ela mesma nao produz par: produz a
            # diagonal, que ja e o modo simples.
            if self.pergunta_secundaria_id == self.pergunta_id:
                raise ValueError(
                    "pergunta_secundaria_id nao pode ser igual a pergunta_id"
                )
            # A secundaria e eixo do cruzamento; usa-la tambem como recorte
            # daria dois papeis a mesma pergunta na mesma leitura.
            if self.pergunta_secundaria_id in ids:
                raise ValueError(
                    "pergunta_secundaria_id nao pode aparecer em filtros_respostas"
                )
        return self


class MapaRespostasGeoResumo(BaseModel):
    # Universo da PESQUISA: nenhum filtro alem do tenant.
    total_universo: int
    # Universo ANALITICO: apos os filtros estruturais (setor, agente e
    # dimensoes de resposta) e ANTES da selecao de categorias. E este o
    # denominador do "% do universo": marcar ou desmarcar uma resposta nao
    # pode mexer no denominador, senao o percentual muda de significado a
    # cada clique.
    total_universo_analitico: int
    # Coletas efetivamente representadas (com categoria atribuida). Sem
    # agrupamento e o recorte destacado; com agrupamento tende ao universo
    # analitico, porque as nao selecionadas permanecem como "Outros".
    total_filtrado: int
    total_com_coordenada: int
    # Contabilizadas, nunca descartadas em silencio nem colocadas em (0,0).
    total_sem_coordenada: int
    # Modo cruzado: coletas com categoria na principal mas sem valor unico na
    # secundaria. Ficam de fora do par e sao declaradas, nunca mascaradas.
    # NAO inclui quem foi excluido por `valores_secundarios`: aquilo e recorte
    # pedido pelo usuario, nao ausencia de resposta.
    total_sem_par: int = 0
    # No universo analitico, quantas coletas nao produziram valor unico na
    # pergunta principal. Explica a diferenca entre universo analitico e
    # representados sem que o cliente precise deduzir por subtracao.
    total_sem_categoria: int = 0


class MapaRespostasGeoCategoria(BaseModel):
    valor: str
    total: int
    # True apenas no balde de nao selecionadas. A identidade da categoria e o
    # PAR (valor, agrupado): uma pergunta pode ter uma opcao real chamada
    # "Outros", e ela continua sendo uma categoria comum, distinta do balde.
    agrupado: bool = False


class MapaRespostasGeoCombinacao(BaseModel):
    """Par observado na MESMA coleta, nunca o encontro de dois totais."""

    valor: str
    valor_secundario: str
    total: int
    agrupado: bool = False
    agrupado_secundario: bool = False


class MapaRespostasGeoPonto(BaseModel):
    """Payload minimo: nenhum dado pessoal do entrevistado."""

    coleta_id: int
    # lat/lng conforme ADR-005 para rotas novas; nunca geometria PostGIS crua.
    lat: float
    lng: float
    valor: str
    # Preenchido apenas no modo cruzado; e a resposta da MESMA coleta.
    valor_secundario: Optional[str] = None
    setor_id: Optional[int] = None
    agrupado: bool = False
    agrupado_secundario: bool = False


class MapaRespostasGeoResponse(BaseModel):
    pesquisa_id: int
    pergunta_id: int
    pergunta_secundaria_id: Optional[int] = None
    resumo: MapaRespostasGeoResumo
    # Ordenadas por total DESC; empate resolvido pela ordem original enviada.
    categorias: List[MapaRespostasGeoCategoria] = Field(default_factory=list)
    # Vazio no modo simples.
    combinacoes: List[MapaRespostasGeoCombinacao] = Field(default_factory=list)
    pontos: List[MapaRespostasGeoPonto] = Field(default_factory=list)


class TipoRelatorioExecutivo(StrEnum):
    MAPAS = "MAPAS"
    EXECUTIVO = "EXECUTIVO"
    SIMPLE = "SIMPLE"
    CROSSTAB = "CROSSTAB"
    CRUZAMENTOS = "CRUZAMENTOS"


class TipoAnaliseRelatorioExecutivo(StrEnum):
    MAPA_COBERTURA = "MAPA_COBERTURA"
    MAPA_DISTRIBUICAO_SETOR = "MAPA_DISTRIBUICAO_SETOR"
    MAPA_RESULTADO_SETOR = "MAPA_RESULTADO_SETOR"
    MAPA_LIDERANCA_SETOR = "MAPA_LIDERANCA_SETOR"
    MAPA_COMPARATIVO = "MAPA_COMPARATIVO"
    SIMPLES = "SIMPLES"
    CROSSTAB = "CROSSTAB"


class ModoRespostaRelatorioExecutivo(StrEnum):
    TODAS = "TODAS"
    ESPECIFICA = "ESPECIFICA"


class OrdenacaoRelatorioSimples(StrEnum):
    DEFAULT = "default"
    FORM = "form"
    DESC = "desc"
    ASC = "asc"
    ALPHABETICAL = "alphabetical"
    CUSTOM = "custom"


class ParametrosOrdenacaoPerguntaSimples(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question_id: int
    mode: OrdenacaoRelatorioSimples = OrdenacaoRelatorioSimples.DEFAULT
    custom_order: Optional[List[str]] = None

    @model_validator(mode="after")
    def validate_custom_order(self):
        if self.mode == OrdenacaoRelatorioSimples.CUSTOM and not self.custom_order:
            raise ValueError("custom_order e obrigatoria para ordenacao custom.")
        if self.mode != OrdenacaoRelatorioSimples.CUSTOM and self.custom_order is not None:
            raise ValueError("custom_order so pode ser usada na ordenacao custom.")
        return self


class ParametrosParCrosstab(BaseModel):
    model_config = ConfigDict(extra="forbid")

    row_question_id: int
    column_question_id: int

    @model_validator(mode="after")
    def validate_distinct_questions(self):
        if self.row_question_id == self.column_question_id:
            raise ValueError("As perguntas do cruzamento devem ser distintas.")
        return self


class ParametrosFiltroRespostaCruzamento(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pergunta_id: int
    valores: List[str] = Field(min_length=1)


class ParametrosAlvoCruzamento(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pergunta_id: int
    modo: ModoAlvoCruzamento
    valor: Optional[str] = None
    valores_excluidos: List[str] = Field(default_factory=list)
    valores_preservados: List[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_alvo(self):
        return _validar_regras_alvo(self)


class ParametrosCruzamentoEstrategico(BaseModel):
    """Visao salva dos Cruzamentos Estrategicos: guarda configuracao, nunca resultados."""

    model_config = ConfigDict(extra="forbid")

    pergunta_ids: List[int] = Field(min_length=2)
    filtros_respostas: List[ParametrosFiltroRespostaCruzamento] = Field(default_factory=list)
    profundidade_maxima: Optional[int] = Field(default=None, ge=1)
    incluir_sem_resposta: bool = True
    alvo: Optional[ParametrosAlvoCruzamento] = None
    dimensoes: Optional[List[CruzamentoDimensaoConfig]] = None
    filtro_territorial: Optional[CruzamentoFiltroTerritorial] = None


class ParametrosGeraisRelatorioExecutivo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    setor_ids: Optional[List[int]] = None
    agente_ids: Optional[List[int]] = None
    data_inicial: Optional[datetime] = None
    data_final: Optional[datetime] = None
    question_order: Optional[List[int]] = None
    question_settings: Optional[List[ParametrosOrdenacaoPerguntaSimples]] = None
    crosses: Optional[List[ParametrosParCrosstab]] = None
    cruzamento: Optional[ParametrosCruzamentoEstrategico] = None
    chart_type: Optional[str] = Field(default=None, max_length=30)

    @model_validator(mode="after")
    def validate_periodo(self):
        if self.data_inicial and self.data_final and self.data_inicial > self.data_final:
            raise ValueError("data_inicial deve ser anterior ou igual a data_final.")
        return self


class ParametrosMapaResultadoSetor(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pergunta_id: int
    modo_resposta: ModoRespostaRelatorioExecutivo
    resposta: Optional[str] = None

    @model_validator(mode="after")
    def validate_resposta(self):
        if self.modo_resposta == ModoRespostaRelatorioExecutivo.ESPECIFICA:
            if not self.resposta or not self.resposta.strip():
                raise ValueError("resposta e obrigatoria no modo ESPECIFICA.")
            self.resposta = self.resposta.strip()
        elif self.resposta is not None:
            raise ValueError("resposta deve ser ausente no modo TODAS.")
        return self


class ParametrosMapaPergunta(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pergunta_id: int


class ParametrosMapaComparativo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pergunta_a_id: int
    pergunta_b_id: int

    @model_validator(mode="after")
    def validate_perguntas(self):
        if self.pergunta_a_id == self.pergunta_b_id:
            raise ValueError("As perguntas do comparativo devem ser distintas.")
        return self


class ConfiguracaoRelatorioExecutivoBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    nome: str = Field(min_length=1, max_length=120)
    descricao: Optional[str] = None
    parametros_gerais: Optional[ParametrosGeraisRelatorioExecutivo] = None

    @field_validator("nome", mode="before")
    @classmethod
    def normalize_nome(cls, value):
        return str(value).strip() if value is not None else value


class ConfiguracaoRelatorioExecutivoCreate(ConfiguracaoRelatorioExecutivoBase):
    tipo_relatorio: TipoRelatorioExecutivo


class ConfiguracaoRelatorioExecutivoUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    nome: Optional[str] = Field(default=None, min_length=1, max_length=120)
    descricao: Optional[str] = None
    parametros_gerais: Optional[ParametrosGeraisRelatorioExecutivo] = None

    @field_validator("nome", mode="before")
    @classmethod
    def normalize_nome(cls, value):
        return str(value).strip() if value is not None else value

    @model_validator(mode="after")
    def validate_nome_not_null(self):
        if "nome" in self.model_fields_set and self.nome is None:
            raise ValueError("nome nao pode ser nulo.")
        return self


class SecaoRelatorioExecutivoCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ordem: Optional[int] = Field(default=None, ge=0)
    tipo_secao: str = Field(default="MAPAS", min_length=1, max_length=100)
    titulo: Optional[str] = Field(default=None, max_length=255)


class SecaoRelatorioExecutivoUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ordem: Optional[int] = Field(default=None, ge=0)
    tipo_secao: Optional[str] = Field(default=None, min_length=1, max_length=100)
    titulo: Optional[str] = Field(default=None, max_length=255)


class AnaliseRelatorioExecutivoCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ordem: Optional[int] = Field(default=None, ge=0)
    tipo_analise: TipoAnaliseRelatorioExecutivo
    titulo_customizado: Optional[str] = Field(default=None, max_length=255)
    parametros: Dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_parametros(self):
        validators = {
            TipoAnaliseRelatorioExecutivo.MAPA_COBERTURA: lambda value: value or {},
            TipoAnaliseRelatorioExecutivo.MAPA_DISTRIBUICAO_SETOR: lambda value: value or {},
            TipoAnaliseRelatorioExecutivo.MAPA_RESULTADO_SETOR: lambda value: ParametrosMapaResultadoSetor.model_validate(value).model_dump(mode="json", exclude_none=True),
            TipoAnaliseRelatorioExecutivo.MAPA_LIDERANCA_SETOR: lambda value: ParametrosMapaPergunta.model_validate(value).model_dump(mode="json"),
            TipoAnaliseRelatorioExecutivo.MAPA_COMPARATIVO: lambda value: ParametrosMapaComparativo.model_validate(value).model_dump(mode="json"),
        }
        validator = validators.get(self.tipo_analise)
        if validator is None:
            raise ValueError(f"Tipo de analise {self.tipo_analise.value} ainda nao suportado.")
        if self.tipo_analise in {
            TipoAnaliseRelatorioExecutivo.MAPA_COBERTURA,
            TipoAnaliseRelatorioExecutivo.MAPA_DISTRIBUICAO_SETOR,
        } and self.parametros:
            raise ValueError("Esta analise nao aceita parametros especificos.")
        self.parametros = validator(self.parametros)
        return self


class AnaliseRelatorioExecutivoUpdate(AnaliseRelatorioExecutivoCreate):
    ordem: Optional[int] = Field(default=None, ge=0)


class OrdemRelatorioExecutivoItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int
    ordem: int = Field(ge=0)


class ReordenarRelatorioExecutivoRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    itens: List[OrdemRelatorioExecutivoItem] = Field(min_length=1)


class AnaliseRelatorioExecutivoRead(BaseModel):
    id: int
    secao_id: int
    ordem: int
    tipo_analise: TipoAnaliseRelatorioExecutivo
    titulo_customizado: Optional[str] = None
    parametros: Dict[str, Any]
    ativo: bool


class SecaoRelatorioExecutivoRead(BaseModel):
    id: int
    configuracao_id: int
    ordem: int
    tipo_secao: str
    titulo: Optional[str] = None
    ativo: bool
    analises: List[AnaliseRelatorioExecutivoRead] = Field(default_factory=list)


class ConfiguracaoRelatorioExecutivoRead(BaseModel):
    id: int
    pesquisa_id: int
    tipo_relatorio: TipoRelatorioExecutivo
    nome: str
    descricao: Optional[str] = None
    parametros_gerais: Optional[Dict[str, Any]] = None
    ativo: bool
    criado_por_id: int
    atualizado_por_id: int
    criado_em: datetime
    atualizado_em: datetime
    secoes: List[SecaoRelatorioExecutivoRead] = Field(default_factory=list)

class Coordenada(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)

class GeofenceUpdate(BaseModel):
    cerca_eletronica: List[Coordenada]
    tolerancia_metros: int

# --- NOVOS SCHEMAS PARA APURAÇÃO DE RELATÓRIOS ---

# Schema para uma análise individual ao criar uma apuração
class AnaliseSalvaCreate(BaseModel):
    tipo_analise: str
    configuracao: dict
    ordem: int

# Schema para exibir uma análise salva
class AnaliseSalva(AnaliseSalvaCreate):
    id: int
    apuracao_id: int

    class Config:
        from_attributes = True

# Schema para criar a apuração principal
class ApuracaoCreate(BaseModel):
    nome: str
    descricao: Optional[str] = None
    analises: List[AnaliseSalvaCreate]

# Schema para exibir a apuração completa
class Apuracao(BaseModel):
    id: int
    nome: str
    descricao: Optional[str] = None
    data_criacao: datetime
    pesquisa_id: int
    analises: List[AnaliseSalva] = []

    class Config:
        from_attributes = True


class RespostaEspontaneaVariacao(BaseModel):
    texto_original: str
    quantidade: int


class RespostaEspontaneaPerguntaRelacionado(BaseModel):
    id: int
    pergunta_id: int
    texto_pergunta: str
    quantidade: int


class RespostaEspontaneaCategoriaBase(BaseModel):
    nome: str

    @field_validator("nome", mode="before")
    @classmethod
    def normalize_nome(cls, value):
        if value is None:
            raise ValueError("Nome da categoria e obrigatorio.")
        texto = str(value).strip()
        if not texto:
            raise ValueError("Nome da categoria e obrigatorio.")
        return texto


class RespostaEspontaneaCategoriaCreate(RespostaEspontaneaCategoriaBase):
    model_config = ConfigDict(extra="forbid")


class RespostaEspontaneaCategoriaUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    nome: Optional[str] = None
    ativo: Optional[bool] = None

    @field_validator("nome", mode="before")
    @classmethod
    def normalize_nome(cls, value):
        if value is None:
            return None
        texto = str(value).strip()
        if not texto:
            raise ValueError("Nome da categoria e obrigatorio.")
        return texto


class RespostaEspontaneaCategoriaRead(BaseModel):
    id: int
    pesquisa_id: int
    nome: str
    nome_normalizado: str
    ativo: bool
    criado_por_id: int
    atualizado_por_id: int
    criado_em: datetime
    atualizado_em: datetime

    class Config:
        from_attributes = True


class RespostaEspontaneaCategoriaRef(BaseModel):
    id: int
    nome: str

    class Config:
        from_attributes = True


class RespostaEspontaneaMapeamentoRead(BaseModel):
    id: int
    pesquisa_id: int
    categoria_id: int
    chave_normalizada: str
    texto_referencia: str
    ativo: bool
    criado_por_id: int
    atualizado_por_id: int
    criado_em: datetime
    atualizado_em: datetime

    class Config:
        from_attributes = True


class RespostaEspontaneaItem(BaseModel):
    chave_normalizada: str
    quantidade_total: int
    variantes: List[RespostaEspontaneaVariacao]
    perguntas: List[RespostaEspontaneaPerguntaRelacionado]
    mapeamento_id: Optional[int] = None
    categoria: Optional[RespostaEspontaneaCategoriaRef] = None
    status: Literal["categorizada", "pendente"]


class RespostaEspontaneaResumo(BaseModel):
    pesquisa_id: int
    total_chaves: int
    total_respostas: int
    respostas_categorizadas: int
    respostas_pendentes: int
    percentual_categorizado: float
    pagina: int
    por_pagina: int
    total_paginas: int
    itens: List[RespostaEspontaneaItem]


class RespostaEspontaneaMapeamentoLote(BaseModel):
    model_config = ConfigDict(extra="forbid")

    categoria_id: int = Field(..., gt=0)
    chaves_normalizadas: List[str] = Field(..., min_length=1, max_length=500)


class RespostaEspontaneaMapeamentoLoteResultado(BaseModel):
    categoria_id: int
    categoria_nome: str
    criadas: int
    atualizadas: int
    inalteradas: int
    total_processado: int

# Adicione ao pesquisa360/schemas.py

class LocalVotacaoBase(BaseModel):
    nome: str
    zona: int
    municipio: str
    bairro: str
    endereco: str

class LocalVotacaoCreate(LocalVotacaoBase):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    secoes_json: List[dict]

class LocalVotacao(LocalVotacaoBase):
    id: int
    company_id: int
    # No retorno, podemos simplificar a geometria para o front
    class Config:
        from_attributes = True



# ==============================================================================
# BASE ELEITORAL VERSIONADA
# ==============================================================================
# Dominio proprio, independente dos Cruzamentos Estrategicos. NivelTerritorial
# continua sendo o enum dos Cruzamentos (SETOR) e nao e reaproveitado aqui.


class StatusBaseEleitoral(StrEnum):
    IMPORTADA = "IMPORTADA"
    EM_CONFERENCIA = "EM_CONFERENCIA"
    VALIDADA = "VALIDADA"
    SUBSTITUIDA = "SUBSTITUIDA"


class TipoTerritorioEleitoral(StrEnum):
    ESTADO = "ESTADO"
    MUNICIPIO = "MUNICIPIO"
    BAIRRO = "BAIRRO"
    LOCALIDADE = "LOCALIDADE"
    LOCAL_VOTACAO = "LOCAL_VOTACAO"
    SECAO = "SECAO"


class BaseEleitoralBase(BaseModel):
    """Campos que o cliente pode informar. `company_id` nunca trafega aqui."""

    model_config = ConfigDict(extra="forbid")

    nome: str = Field(min_length=1)
    ano: int = Field(ge=1900, le=2999)
    uf: str = Field(min_length=2, max_length=2)
    fonte: str = Field(min_length=1)
    fonte_referencia: Optional[str] = None
    versao: str = Field(min_length=1)
    data_referencia: date
    status: StatusBaseEleitoral = StatusBaseEleitoral.IMPORTADA
    comparecimento_estimado: Optional[float] = Field(default=None, ge=0, le=1)
    percentual_votos_validos: Optional[float] = Field(default=None, ge=0, le=1)

    @field_validator("uf")
    @classmethod
    def normalizar_uf(cls, value: str) -> str:
        normalizado = value.strip().upper()
        if len(normalizado) != 2 or not normalizado.isalpha():
            raise ValueError("uf deve conter exatamente duas letras")
        return normalizado

    @field_validator("nome", "fonte", "versao")
    @classmethod
    def exigir_texto(cls, value: str) -> str:
        texto = value.strip()
        if not texto:
            raise ValueError("campo de texto obrigatorio nao pode ser vazio")
        return texto


class BaseEleitoralCreate(BaseEleitoralBase):
    """Contrato do cliente: sem company_id, seguindo a regra de ouro do tenant."""


class BaseEleitoralCreateInterno(BaseEleitoralBase):
    """Uso interno (servico/importador). `company_id=None` cria base oficial."""

    company_id: Optional[int] = None
    criado_por_id: int


class BaseEleitoralResponse(BaseEleitoralBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    substituida_por_id: Optional[int] = None
    criado_por_id: int
    criado_em: datetime
    atualizado_em: datetime


class TerritorioEleitoralBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tipo: TipoTerritorioEleitoral
    nome: str = Field(min_length=1)
    nome_normalizado: Optional[str] = None
    codigo: Optional[str] = None
    parent_id: Optional[int] = None
    municipio_id: Optional[int] = None
    zona_eleitoral: Optional[int] = Field(default=None, ge=0)
    numero_secao: Optional[int] = Field(default=None, gt=0)
    eleitorado_apto: Optional[int] = Field(default=None, ge=0)
    eleitorado_apto_origem: Optional[int] = Field(default=None, ge=0)
    eleitorado_apto_divergente: bool = False
    status_validacao: StatusBaseEleitoral = StatusBaseEleitoral.IMPORTADA
    metadados: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("metadados", mode="before")
    @classmethod
    def validar_metadados(cls, value):
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise ValueError("metadados deve ser um objeto JSON")
        return value

    @model_validator(mode="after")
    def validar_arvore(self):
        # Espelha os CHECKs do banco: so ESTADO e raiz e SECAO exige numero.
        if self.tipo != TipoTerritorioEleitoral.ESTADO and self.parent_id is None:
            raise ValueError("apenas territorio do tipo ESTADO pode existir sem parent_id")
        if self.tipo == TipoTerritorioEleitoral.SECAO and self.numero_secao is None:
            raise ValueError("numero_secao e obrigatorio para territorio do tipo SECAO")
        return self


class TerritorioEleitoralCreate(TerritorioEleitoralBase):
    base_eleitoral_id: int


class TerritorioEleitoralResponse(TerritorioEleitoralBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    base_eleitoral_id: int
    criado_em: datetime
    atualizado_em: datetime


class ProjetoBaseEleitoralBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_eleitoral_id: int
    principal: bool = True


class ProjetoBaseEleitoralCreate(ProjetoBaseEleitoralBase):
    """O projeto_id vem da rota/servico, nunca do corpo enviado pelo cliente."""


class ProjetoBaseEleitoralResponse(ProjetoBaseEleitoralBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    projeto_id: int
    vinculado_em: datetime


class ImportacaoBaseEleitoralResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: int
    base_eleitoral_id: int
    arquivo_origem: str
    hash_arquivo: Optional[str] = None
    total_linhas: int = Field(ge=0)
    total_importadas: int = Field(ge=0)
    total_divergencias: int = Field(ge=0)
    divergencias: List[Any] = Field(default_factory=list)
    executado_por_id: int
    executado_em: datetime




# --- Contratos de API da Base Eleitoral (Fase 3A) ---------------------------
# Nenhum request aceita company_id: o tenant vem sempre do JWT.


class BaseEleitoralListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    nome: str
    ano: int
    uf: str
    fonte: str
    versao: str
    data_referencia: date
    status: StatusBaseEleitoral
    eh_oficial: bool


class BaseEleitoralDetalhe(BaseEleitoralListItem):
    fonte_referencia: Optional[str] = None
    substituida_por_id: Optional[int] = None
    comparecimento_estimado: Optional[float] = None
    percentual_votos_validos: Optional[float] = None
    criado_por_id: int
    criado_em: datetime
    atualizado_em: datetime
    total_territorios: int = 0
    total_em_conferencia: int = 0


class TerritorioEleitoralListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    base_eleitoral_id: int
    tipo: TipoTerritorioEleitoral
    codigo: Optional[str] = None
    nome: str
    nome_normalizado: str
    parent_id: Optional[int] = None
    municipio_id: Optional[int] = None
    zona_eleitoral: Optional[int] = None
    numero_secao: Optional[int] = None
    eleitorado_apto: Optional[int] = None
    eleitorado_apto_origem: Optional[int] = None
    eleitorado_apto_divergente: bool
    status_validacao: StatusBaseEleitoral
    possui_geometria: bool


class AjusteFilhoDivergenciaResponse(BaseModel):
    """Filho cujo valor operacional mudou apos a resolucao da propria divergencia."""

    model_config = ConfigDict(extra="allow")

    territorio_id: int
    territorio: str
    tipo: str
    valor_anterior: int
    valor_final: int
    # Sinal preservado: valor_final - valor_anterior.
    ajuste: int


class ComposicaoValorFinalResponse(BaseModel):
    """Como o valor operacional do territorio foi formado.

    Read model calculado a partir da arvore; nada disso e persistido.
    `soma_filhos_original` e a ancora dos ajustes -- somar os ajustes sobre o
    valor declarado pela fonte produziria semantica errada.
    """

    model_config = ConfigDict(extra="allow")

    soma_filhos_original: int
    soma_filhos_atual: Optional[int] = None
    ajuste_total_filhos: Optional[int] = None
    valor_operacional_final: Optional[int] = None
    # Modulo; a UI escolhe a linguagem ("N eleitores a menos").
    diferenca_final_fonte: Optional[int] = None
    ajustes_filhos: List[AjusteFilhoDivergenciaResponse] = Field(default_factory=list)
    total_filhos: int = 0


class DivergenciaBaseEleitoralResponse(BaseModel):
    """Espelha o JSON auditado do lote; o formato varia por tipo de divergencia."""

    model_config = ConfigDict(extra="allow")

    tipo_divergencia: str
    importacao_id: int
    arquivo_origem: str
    resolvida: bool = False
    territorio_id: Optional[int] = None
    composicao_valor_final: Optional[ComposicaoValorFinalResponse] = None


class ParametrosProjecaoRequest(BaseModel):
    """PATCH dos dois parametros de projecao, e nada mais.

    Campo ausente -> mantem o valor atual.
    Campo explicitamente null -> limpa (volta a "nao configurado").

    Somente estes dois campos existem: company_id, status, eleitorado, versao e
    fonte nao entram por aqui.
    """

    model_config = ConfigDict(extra="forbid")

    comparecimento_estimado: Optional[float] = Field(default=None, ge=0, le=1)
    percentual_votos_validos: Optional[float] = Field(default=None, ge=0, le=1)

    @field_validator("comparecimento_estimado", "percentual_votos_validos")
    @classmethod
    def exigir_numero_finito(cls, value):
        # ge/le ja barram fora de faixa, mas NaN passa por qualquer comparacao.
        if value is not None and not math.isfinite(value):
            raise ValueError("parametro deve ser um numero finito entre 0 e 1")
        return value


class ResolverDivergenciaRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    valor_final: int = Field(ge=0)
    justificativa: str = Field(min_length=1)

    @field_validator("justificativa")
    @classmethod
    def exigir_justificativa(cls, value: str) -> str:
        texto = value.strip()
        if not texto:
            raise ValueError("justificativa e obrigatoria")
        return texto


class VincularBaseProjetoRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    principal: bool = True


class VinculoProjetoBaseResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    projeto_id: int
    base_eleitoral_id: int
    principal: bool
    vinculado_em: datetime


class ResultadoValidacaoBase(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    status: StatusBaseEleitoral
    total_territorios: int
    total_em_conferencia: int




# --- Leitura para o Workspace da Base Eleitoral (Fase 3D-A) ------------------
# Somente contrato de leitura: nenhum destes schemas expoe company_id.


class AuditoriaDataReferencia(BaseModel):
    """Proveniencia da data de referencia, quando a importacao a registrou."""

    model_config = ConfigDict(extra="forbid")

    origem: Optional[str] = None
    convencional: bool = False
    data_fonte_declarada: Optional[date] = None
    motivo: Optional[str] = None
    pdf_creation_date: Optional[str] = None


class TotaisTerritorioEleitoral(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ESTADO: int = 0
    MUNICIPIO: int = 0
    BAIRRO: int = 0
    LOCALIDADE: int = 0
    LOCAL_VOTACAO: int = 0
    SECAO: int = 0


class ImportacaoBaseEleitoralResumo(BaseModel):
    """Lote de importacao. O hash trafega completo; abreviar e papel da UI."""

    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: int
    arquivo_origem: str
    hash_arquivo: Optional[str] = None
    total_linhas: int
    total_importadas: int
    total_divergencias: int
    executado_por_id: int
    executado_em: datetime


class BaseEleitoralDetalheCompleto(BaseEleitoralDetalhe):
    """Detalhe da base acrescido do que o Workspace precisa ler.

    `eleitorado_operacional` e `eleitorado_declarado` vêm da raiz ESTADO: saber
    disso e regra de dominio e nao deve vazar para o cliente.
    """

    totais_por_tipo: TotaisTerritorioEleitoral
    eleitorado_operacional: Optional[int] = None
    eleitorado_declarado: Optional[int] = None
    diferenca_eleitorado: Optional[int] = None
    auditoria_data_referencia: Optional[AuditoriaDataReferencia] = None
    # Sem unicidade garantida de importacao por base: a UI usa a contagem para
    # decidir entre exibir a origem direto ou abrir a lista auditavel.
    total_importacoes: int = 0
    importacao_origem: Optional[ImportacaoBaseEleitoralResumo] = None


class ProjetoBaseEleitoralAtualResponse(BaseModel):
    """Base principal do projeto. `base=null` e estado normal, nao erro."""

    model_config = ConfigDict(extra="forbid")

    projeto_id: int
    principal: bool = False
    base: Optional[BaseEleitoralDetalheCompleto] = None


class TerritorioEleitoralPage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: List[TerritorioEleitoralListItem]
    total: int
    limit: int
    offset: int




# --- Composicao eleitoral do Setor ------------------------------------------
# Nenhum request aceita company_id nem base_eleitoral_id: ambos derivam do
# caminho projeto -> pesquisa -> setor e da base principal do projeto.


class SetorTerritoriosRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Substituicao integral: o conjunto enviado passa a ser a composicao.
    # Lista vazia limpa. Ids repetidos sao deduplicados, nao rejeitados.
    territorio_eleitoral_ids: List[int] = Field(default_factory=list)


class SetorTerritorioItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    nome: str
    # Nome se repete entre municipios ("Centro" existe em 16): quem identifica
    # e o id, e a UI precisa do municipio para desambiguar.
    municipio_id: Optional[int] = None
    eleitorado_apto: Optional[int] = None


# --- Universo eleitoral do Setor --------------------------------------------
# Derivado em leitura da composicao atual + Base principal atual. Nunca
# persistido: guardar a soma criaria um numero que envelhece sozinho.


class StatusUniversoEleitoralSetor(StrEnum):
    """Estado do universo. A UI decide pelo codigo, nunca por texto livre.

    Ausencia NUNCA vira zero: zero eleitores e um resultado, "nao da para
    calcular" e outra coisa.
    """

    DISPONIVEL = "DISPONIVEL"
    # Setor sem nenhuma unidade vinculada: falta configurar, nao ha universo.
    SEM_COMPOSICAO_ELEITORAL = "SEM_COMPOSICAO_ELEITORAL"
    # Ao menos uma unidade pertence a Base anterior a principal atual.
    COMPOSICAO_BASE_DESATUALIZADA = "COMPOSICAO_BASE_DESATUALIZADA"
    BASE_ELEITORAL_NAO_CONFIGURADA = "BASE_ELEITORAL_NAO_CONFIGURADA"
    BASE_ELEITORAL_NAO_VALIDADA = "BASE_ELEITORAL_NAO_VALIDADA"
    # eleitorado_apto e nullable no schema: base futura pode trazer unidade sem
    # o valor, e somar tratando NULL como 0 inventaria um universo menor.
    ELEITORADO_TERRITORIO_INDISPONIVEL = "ELEITORADO_TERRITORIO_INDISPONIVEL"


class UniversoEleitoralSetorResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    setor_id: int
    status: StatusUniversoEleitoralSetor
    # Espelha `status` quando indisponivel e e None em DISPONIVEL: a UI que so
    # sabe tratar ausencia le um campo, a que ramifica por estado le o outro.
    motivo_indisponibilidade: Optional[StatusUniversoEleitoralSetor] = None
    # Base principal ATUAL do projeto, nao a base dos vinculos. None quando o
    # projeto nao tem base principal.
    base_eleitoral_id: Optional[int] = None
    base_eleitoral_nome: Optional[str] = None
    # Vinculos efetivamente persistidos, mesmo quando o universo esta
    # indisponivel: distingue "nao configurado" de "configurado e desatualizado".
    quantidade_territorios: int
    # None sempre que o universo nao pode ser calculado. Nunca 0 por ausencia.
    eleitorado_apto: Optional[int] = None


# --- Gestao de Liderancas (Fase 6A) -----------------------------------------
# Nenhum request aceita company_id: o tenant vem do JWT.


class PosicionamentoLideranca(StrEnum):
    """Campo politico da lideranca.

    BASE       = vinculada ao grupo politico analisado
    OPOSICAO   = pertencente ao campo adversario
    INDEFINIDA = ainda sem classificacao

    E atributo, nao entidade: BASE e OPOSICAO compartilham CRUD, cota e
    territorios. Valor fora do dominio e 422, nunca coercao silenciosa.
    """

    BASE = "BASE"
    OPOSICAO = "OPOSICAO"
    INDEFINIDA = "INDEFINIDA"


class MotivoIndisponibilidadeLideranca(StrEnum):
    SEM_COTA = "SEM_COTA"
    SEM_TERRITORIO_ELEITORAL = "SEM_TERRITORIO_ELEITORAL"
    BASE_ELEITORAL_NAO_VALIDADA = "BASE_ELEITORAL_NAO_VALIDADA"
    PARAMETROS_ELEITORAIS_AUSENTES = "PARAMETROS_ELEITORAIS_AUSENTES"
    SEM_RESPOSTAS_VALIDAS = "SEM_RESPOSTAS_VALIDAS"
    PERGUNTA_ALVO_INVALIDA = "PERGUNTA_ALVO_INVALIDA"


class EscopoAmostralLideranca(StrEnum):
    SETOR = "SETOR"
    PESQUISA = "PESQUISA"


class StatusGapPlus(StrEnum):
    GAP = "GAP"
    PLUS = "PLUS"
    META_ATINGIDA = "META_ATINGIDA"


class PontoGeoJSON(BaseModel):
    """Ponto no contrato geografico vigente (ADR-004).

    `coordinates` segue a ordem GeoJSON: [longitude, latitude]. E o mesmo
    formato em que os Setores chegam ao mapa, evitando dois contratos
    geograficos na mesma tela.
    """

    model_config = ConfigDict(extra="forbid")

    type: Literal["Point"]
    coordinates: List[float] = Field(min_length=2, max_length=2)

    @field_validator("coordinates")
    @classmethod
    def validar_coordenadas(cls, value: List[float]) -> List[float]:
        longitude, latitude = value
        # NaN/Infinity passam por qualquer comparacao de faixa.
        if not math.isfinite(longitude) or not math.isfinite(latitude):
            raise ValueError("coordinates deve conter numeros finitos")
        if not -180 <= longitude <= 180:
            raise ValueError("longitude deve estar entre -180 e 180")
        if not -90 <= latitude <= 90:
            raise ValueError("latitude deve estar entre -90 e 90")
        return value


class LiderancaPoliticaCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    nome: str = Field(min_length=1)
    # Omitido = INDEFINIDA. Cadastro nunca presume que a lideranca e aliada.
    posicionamento: PosicionamentoLideranca = PosicionamentoLideranca.INDEFINIDA

    @field_validator("nome")
    @classmethod
    def exigir_nome(cls, value: str) -> str:
        texto = value.strip()
        if not texto:
            raise ValueError("nome e obrigatorio")
        return texto


class LiderancaPoliticaUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    nome: Optional[str] = Field(default=None, min_length=1)
    ativo: Optional[bool] = None
    # Campo ausente mantem a posicao; null explicito remove.
    localizacao: Optional[PontoGeoJSON] = None
    # Ausente = nao altera. Nao aceita null: posicionamento sempre tem valor,
    # e "sem classificacao" se escreve INDEFINIDA.
    posicionamento: Optional[PosicionamentoLideranca] = None

    @field_validator("nome")
    @classmethod
    def validar_nome(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        texto = value.strip()
        if not texto:
            raise ValueError("nome nao pode ser vazio")
        return texto


class LiderancaTerritorioItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    nome: str
    eleitorado_apto: Optional[int] = None


class LiderancaSetorItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    nome: str


class LiderancaPesquisaConfigResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    pesquisa_id: int
    setor_id: Optional[int] = None
    # Cota em VOTOS VALIDOS; NULL significa nao configurada, diferente de zero.
    cota_votos_validos: Optional[int] = None


class LiderancaPoliticaResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    nome: str
    ativo: bool
    posicionamento: PosicionamentoLideranca
    criado_em: datetime
    atualizado_em: datetime
    # NULL e estado valido: lideranca sem ponto no mapa.
    localizacao: Optional[PontoGeoJSON] = None
    territorios: List[LiderancaTerritorioItem] = Field(default_factory=list)
    configs: List[LiderancaPesquisaConfigResponse] = Field(default_factory=list)


class LiderancaConfigRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    setor_id: Optional[int] = None
    cota_votos_validos: Optional[int] = Field(default=None, ge=0)


class LiderancaTerritoriosRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    territorio_ids: List[int] = Field(default_factory=list)

    @field_validator("territorio_ids")
    @classmethod
    def sem_duplicados(cls, value: List[int]) -> List[int]:
        if len(value) != len(set(value)):
            raise ValueError("territorio_ids nao pode conter duplicados")
        return value


class LiderancaAnaliseAlvo(BaseModel):
    """Resposta cujo desempenho eleitoral esta sendo medido."""

    model_config = ConfigDict(extra="forbid")

    pergunta_id: int
    valores: List[str] = Field(min_length=1)

    @field_validator("valores")
    @classmethod
    def normalizar(cls, valores: List[str]) -> List[str]:
        limpos = [item.strip() for item in valores]
        if any(not item for item in limpos):
            raise ValueError("valores nao pode conter itens vazios")
        if len(limpos) != len(set(limpos)):
            raise ValueError("valores nao pode conter duplicados")
        return limpos


class LiderancaAnaliseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pesquisa_id: int
    alvo: LiderancaAnaliseAlvo
    # Recortes diagnosticos: OR entre valores da mesma pergunta, AND entre perguntas.
    filtros_respostas: List[CruzamentoFiltroResposta] = Field(default_factory=list)
    lideranca_ids: Optional[List[int]] = None

    @model_validator(mode="after")
    def validar(self):
        ids = [item.pergunta_id for item in self.filtros_respostas]
        if len(ids) != len(set(ids)):
            raise ValueError("filtros_respostas nao pode repetir pergunta_id")
        if self.alvo.pergunta_id in ids:
            raise ValueError("a pergunta alvo nao pode ser usada como filtro adicional")
        return self


class LiderancaUniversoEleitoral(BaseModel):
    eleitorado_apto: Optional[int] = None
    votos_validos_projetados: Optional[int] = None


class LiderancaResultadoPrincipal(BaseModel):
    escopo_amostral: EscopoAmostralLideranca
    total_entrevistas: int
    base_valida: int
    respostas_alvo: int
    taxa_alvo: Optional[float] = None
    votos_projetados_alvo: Optional[int] = None
    gap_plus: Optional[int] = None
    status: Optional[StatusGapPlus] = None
    atingimento_percentual: Optional[float] = None


class LiderancaRecorteFiltrado(BaseModel):
    """Somente taxas: sem calibracao populacional do segmento nao ha projecao."""

    total_entrevistas: int
    base_valida: int
    respostas_alvo: int
    taxa_alvo: Optional[float] = None


class StatusCoberturaEleitoral(StrEnum):
    DISPONIVEL = "DISPONIVEL"
    INDISPONIVEL = "INDISPONIVEL"


class MotivoCoberturaEleitoral(StrEnum):
    """Motivos proprios da cobertura + os herdados do universo do Setor.

    Quando o problema e do denominador, o motivo do Setor e PROPAGADO em vez de
    ganhar um sinonimo: a causa e a mesma e a UI ja sabe explica-la.
    """

    # Proprios da lideranca
    SEM_SETOR_REFERENCIA = "SEM_SETOR_REFERENCIA"
    SEM_TERRITORIO_ELEITORAL_LIDERANCA = "SEM_TERRITORIO_ELEITORAL_LIDERANCA"
    TERRITORIO_LIDERANCA_BASE_DESATUALIZADA = "TERRITORIO_LIDERANCA_BASE_DESATUALIZADA"
    ELEITORADO_TERRITORIO_LIDERANCA_INDISPONIVEL = (
        "ELEITORADO_TERRITORIO_LIDERANCA_INDISPONIVEL"
    )
    UNIVERSO_ELEITORAL_ZERO = "UNIVERSO_ELEITORAL_ZERO"
    # Herdados do universo do Setor (StatusUniversoEleitoralSetor)
    SEM_COMPOSICAO_ELEITORAL = "SEM_COMPOSICAO_ELEITORAL"
    COMPOSICAO_BASE_DESATUALIZADA = "COMPOSICAO_BASE_DESATUALIZADA"
    BASE_ELEITORAL_NAO_CONFIGURADA = "BASE_ELEITORAL_NAO_CONFIGURADA"
    BASE_ELEITORAL_NAO_VALIDADA = "BASE_ELEITORAL_NAO_VALIDADA"
    ELEITORADO_TERRITORIO_INDISPONIVEL = "ELEITORADO_TERRITORIO_INDISPONIVEL"


class CoberturaEleitoralLideranca(BaseModel):
    """Quanto do universo do SETOR os bairros da lideranca cobrem.

    Somente a intersecao entra: bairro da lideranca fora do setor nao e erro,
    apenas nao pertence aquele denominador. Por isso a cobertura nunca passa de
    100% -- e nao ha clamp escondendo nada.
    """

    model_config = ConfigDict(from_attributes=True)

    status: StatusCoberturaEleitoral
    motivo_indisponibilidade: Optional[MotivoCoberturaEleitoral] = None
    setor_id: Optional[int] = None
    base_eleitoral_id: Optional[int] = None
    universo_eleitoral_setor: Optional[int] = None
    quantidade_territorios_lideranca: int = 0
    # None quando indisponivel; 0 e cobertura real de zero bairros.
    quantidade_territorios_cobertos: Optional[int] = None
    eleitorado_coberto: Optional[int] = None
    cobertura_percentual: Optional[float] = None


class LiderancaAnaliseItem(BaseModel):
    id: int
    nome: str
    # Acompanha a analise para que o mapa distinga BASE de OPOSICAO sem uma
    # segunda chamada. Nao participa de nenhum calculo desta fase.
    posicionamento: PosicionamentoLideranca = PosicionamentoLideranca.INDEFINIDA
    setor: Optional[LiderancaSetorItem] = None
    territorios: List[LiderancaTerritorioItem] = Field(default_factory=list)
    cota_votos_validos: Optional[int] = None
    # Aditivo (Fase 3B.1): cobertura territorial da lideranca no setor.
    cobertura_eleitoral: Optional[CoberturaEleitoralLideranca] = None
    universo_eleitoral: LiderancaUniversoEleitoral
    resultado_principal: LiderancaResultadoPrincipal
    recorte_filtrado: Optional[LiderancaRecorteFiltrado] = None
    indisponibilidade: Optional[MotivoIndisponibilidadeLideranca] = None


class LiderancaAnaliseResponse(BaseModel):
    projeto_id: int
    pesquisa_id: int
    alvo: LiderancaAnaliseAlvo
    filtros_respostas: List[CruzamentoFiltroResposta] = Field(default_factory=list)
    liderancas: List[LiderancaAnaliseItem] = Field(default_factory=list)


# --- Atualização de referências ---
# Garante que os schemas que se referenciam mutuamente sejam resolvidos
Projeto.model_rebuild()
Usuario.model_rebuild()
Pesquisa.model_rebuild()


# --- Tentativa de Campo (PROMPT 03) -------------------------------------------
# Abordagem operacional; nao confundir com Coleta (entrevista concluida).

class TentativaResultado(StrEnum):
    EM_ANDAMENTO = "EM_ANDAMENTO"
    RECUSA = "RECUSA"
    NAO_ELEGIVEL = "NAO_ELEGIVEL"
    DESISTENCIA = "DESISTENCIA"
    INCOMPLETA = "INCOMPLETA"
    PROBLEMA_TECNICO = "PROBLEMA_TECNICO"
    OUTRO = "OUTRO"
    CONCLUIDA = "CONCLUIDA"


class TentativaLocalizacao(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)
    accuracy: Optional[float] = Field(default=None, ge=0)
    capturada_em: Optional[datetime] = None

    @field_validator("lat", "lng")
    @classmethod
    def _finito(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("coordenada invalida")
        return value


class TentativaCampoCreate(BaseModel):
    # `company_id`/`agente_id` NAO existem aqui de proposito: chaves extras do
    # payload sao ignoradas pelo Pydantic e nunca chegam ao banco.
    client_uuid: UUID
    setor_id: Optional[int] = Field(default=None, gt=0)
    iniciada_em: datetime
    encerrada_em: Optional[datetime] = None
    localizacao: TentativaLocalizacao
    resultado: TentativaResultado
    motivo: Optional[str] = Field(default=None, max_length=60)
    observacao: Optional[str] = Field(default=None, max_length=1000)
    # Vinculo com a Coleta ja sincronizada (ordem: coleta antes da tentativa).
    coleta_client_uuid: Optional[UUID] = None

    @field_validator("motivo")
    @classmethod
    def _motivo_codigo(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        value = value.strip()
        if not value:
            return None
        if not re.fullmatch(r"[A-Z0-9_]{2,60}", value):
            raise ValueError("motivo deve ser um codigo (ex.: NAO_QUIS_PARTICIPAR)")
        return value

    @model_validator(mode="after")
    def _coerencia(self):
        if self.resultado == TentativaResultado.EM_ANDAMENTO:
            raise ValueError("tentativa EM_ANDAMENTO nao pode ser sincronizada")
        if self.encerrada_em is None:
            raise ValueError("encerrada_em e obrigatoria para tentativa encerrada")
        if self.encerrada_em < self.iniciada_em:
            raise ValueError("encerrada_em anterior a iniciada_em")
        if self.coleta_client_uuid is not None and self.resultado not in (
            TentativaResultado.CONCLUIDA,
            TentativaResultado.DESISTENCIA,
            TentativaResultado.INCOMPLETA,
        ):
            raise ValueError(
                "coleta_client_uuid so faz sentido em CONCLUIDA/DESISTENCIA/INCOMPLETA"
            )
        return self


class TentativaCampoRead(BaseModel):
    id: int
    client_uuid: UUID
    pesquisa_id: int
    setor_id: Optional[int] = None
    agente_id: int
    iniciada_em: datetime
    encerrada_em: Optional[datetime] = None
    resultado: TentativaResultado
    motivo: Optional[str] = None
    coleta_id: Optional[int] = None

    model_config = ConfigDict(from_attributes=True)


# --- Cotas de Perfil (PROMPT 05) ----------------------------------------------
# Amostral e ORIENTATIVA: nunca bloqueia abordagem, entrevista ou sync.

class SexoCota(StrEnum):
    MASCULINO = "MASCULINO"
    FEMININO = "FEMININO"


class ModoIdadeCota(StrEnum):
    NUMERICA = "NUMERICA"
    CATEGORICA = "CATEGORICA"


class PrioridadePerfil(StrEnum):
    EQUILIBRADO = "EQUILIBRADO"
    BAIXO = "BAIXO"
    MEDIO = "MEDIO"
    ALTO = "ALTO"


class CotaPerfilItem(BaseModel):
    sexo: SexoCota
    faixa_etaria: str = Field(min_length=1, max_length=40)
    idade_min: Optional[int] = Field(default=None, ge=0, le=130)
    idade_max: Optional[int] = Field(default=None, ge=0, le=130)
    idade_valores: Optional[List[str]] = None
    meta: int = Field(ge=0)

    @model_validator(mode="after")
    def _faixa(self):
        if self.idade_min is not None and self.idade_max is not None and self.idade_min > self.idade_max:
            raise ValueError("idade_min nao pode ser maior que idade_max")
        if self.idade_valores is not None:
            limpos = [v.strip() for v in self.idade_valores if v and v.strip()]
            if not limpos:
                raise ValueError("idade_valores nao pode ser vazio")
            self.idade_valores = limpos
        return self


class CotaPerfilTerritorio(BaseModel):
    territorio_id: int = Field(gt=0)
    cotas: List[CotaPerfilItem] = Field(min_length=1)


class PlanoCotaPerfilRequest(BaseModel):
    pergunta_sexo_id: int = Field(gt=0)
    pergunta_idade_id: int = Field(gt=0)
    modo_idade: ModoIdadeCota = ModoIdadeCota.NUMERICA
    # Valores REAIS da pergunta de sexo que significam cada categoria.
    sexo_valores: Dict[SexoCota, List[str]]
    territorios: List[CotaPerfilTerritorio] = Field(min_length=1)
    ativo: bool = True

    @model_validator(mode="after")
    def _coerencia(self):
        if self.pergunta_sexo_id == self.pergunta_idade_id:
            raise ValueError("pergunta de sexo e de idade devem ser diferentes")
        for sexo in (SexoCota.MASCULINO, SexoCota.FEMININO):
            valores = [v.strip() for v in self.sexo_valores.get(sexo, []) if v and v.strip()]
            if not valores:
                raise ValueError(f"sexo_valores precisa mapear {sexo.value}")
            self.sexo_valores[sexo] = valores
        todos = [v.casefold() for vs in self.sexo_valores.values() for v in vs]
        if len(todos) != len(set(todos)):
            raise ValueError("um mesmo valor nao pode mapear dois sexos")
        vistos = set()
        for territorio in self.territorios:
            if territorio.territorio_id in vistos:
                raise ValueError("territorio repetido no plano")
            vistos.add(territorio.territorio_id)
            for cota in territorio.cotas:
                if self.modo_idade == ModoIdadeCota.NUMERICA and cota.idade_min is None:
                    raise ValueError("modo NUMERICA exige idade_min em cada cota")
                if self.modo_idade == ModoIdadeCota.CATEGORICA and not cota.idade_valores:
                    raise ValueError("modo CATEGORICA exige idade_valores em cada cota")
        return self


class CotaPerfilRead(BaseModel):
    id: int
    territorio_id: int
    territorio_nome: Optional[str] = None
    sexo: SexoCota
    faixa_etaria: str
    idade_min: Optional[int] = None
    idade_max: Optional[int] = None
    idade_valores: Optional[List[str]] = None
    meta: int


class SetorOperacionalCota(BaseModel):
    id: int
    nome: str
    meta: int


class DiagnosticoTerritorioCota(BaseModel):
    territorio_id: int
    territorio_nome: Optional[str] = None
    total_cotas_perfil: int
    # ADR-035: soma das metas dos setores OPERACIONAIS referenciados ao municipio.
    meta_territorial: Optional[int] = None
    diferenca: Optional[int] = None
    setores_operacionais: List[SetorOperacionalCota] = Field(default_factory=list)


class ContextoTerritorialCota(BaseModel):
    """Municipios da Base principal com os setores operacionais que os alimentam."""
    territorio_id: int
    territorio_nome: str
    setores_operacionais: List[SetorOperacionalCota]
    meta_territorial: int


class PlanoCotaPerfilRead(BaseModel):
    id: int
    pesquisa_id: int
    ativo: bool
    pergunta_sexo_id: int
    pergunta_idade_id: int
    modo_idade: ModoIdadeCota
    sexo_valores: Dict[str, List[str]]
    cotas: List[CotaPerfilRead]
    diagnostico: List[DiagnosticoTerritorioCota]


class PrioridadePerfilItem(BaseModel):
    territorio_id: int
    territorio_nome: Optional[str] = None
    sexo: SexoCota
    sexo_rotulo: str
    faixa_etaria: str
    meta: int
    realizado: int
    restante: int
    percentual_atingimento: float
    percentual_territorio: float
    desvio_pp: float
    prioridade: PrioridadePerfil


class ProgressoCotaPerfilTerritorio(BaseModel):
    territorio_id: int
    territorio_nome: Optional[str] = None
    meta_total: int
    realizado_total: int
    percentual_territorio: float
    fase_inicial: bool
    status: str  # FASE_INICIAL | EQUILIBRADO | PRIORIDADES
    celulas: List[PrioridadePerfilItem]


class ProgressoCotaPerfilRead(BaseModel):
    pesquisa_id: int
    plano_ativo: bool
    snapshot_em: datetime
    nao_classificadas: int
    motivos_nao_classificadas: Dict[str, int]
    territorios: List[ProgressoCotaPerfilTerritorio]


# --- Cobertura territorial de campo (PROMPT 06) -------------------------------
# Atividade CONHECIDA (snapshot), orientativa. Sem dados pessoais.

class EventoCoberturaCampo(BaseModel):
    tipo: Literal["COLETA", "TENTATIVA"]
    server_id: int
    setor_id: int
    lat: float
    lng: float
    accuracy: Optional[float] = None
    ocorrido_em: Optional[datetime] = None
    resultado: Optional[TentativaResultado] = None


class CoberturaCampoRead(BaseModel):
    pesquisa_id: int
    snapshot_em: datetime
    distancia_recomendada_entre_abordagens_metros: int
    distancia_configurada: bool
    setor_ids: List[int]
    eventos: List[EventoCoberturaCampo]


class ConfiguracaoCampoRequest(BaseModel):
    # null limpa a configuracao (volta ao default centralizado do servico).
    distancia_recomendada_entre_abordagens_metros: Optional[int] = Field(default=None, gt=0, le=5000)


class ConfiguracaoCampoRead(BaseModel):
    pesquisa_id: int
    distancia_recomendada_entre_abordagens_metros: int
    distancia_configurada: bool


# --- Painel de Controle de Campo (PROMPT 07) ----------------------------------
# Supervisao/coordenacao. Snapshot do servidor; nunca tempo real.

class ControleCampoSetor(BaseModel):
    setor_id: int
    setor_nome: str
    finalidade: Optional[str] = None
    municipio_id: Optional[int] = None
    municipio_nome: Optional[str] = None
    meta: int
    realizado: int
    restante: int
    excedente: int
    percentual_atingimento: Optional[float] = None
    status_cota: str
    limite_atencao_realizado: Optional[int] = None
    agentes_atribuidos_total: int
    snapshot_ate_coleta_id: Optional[int] = None


class ControleCampoResumo(BaseModel):
    meta_territorial: int
    realizado_territorial: int
    entrevistas_concluidas: int
    coletas_com_tentativa: int
    coletas_sem_tentativa: int
    tentativas_encerradas: int
    tentativas_em_andamento: int
    tentativas_concluidas: int
    recusas: int
    nao_elegiveis: int
    desistencias: int
    incompletas: int
    problemas_tecnicos: int
    outros: int
    taxa_conclusao_tentativas: Optional[float] = None
    nota_taxa: str


class ControleCampoResumoTerritorial(BaseModel):
    abertos: int
    atencao: int
    encerrados: int
    sem_cota: int
    com_excedente: int


class ControleCampoAlerta(BaseModel):
    tipo: str
    total: int
    mensagem: str


class ControleCampoOpcao(BaseModel):
    id: int
    nome: Optional[str] = None


class ControleCampoOpcoes(BaseModel):
    municipios: List[ControleCampoOpcao]
    setores: List[ControleCampoOpcao]
    agentes: List[ControleCampoOpcao]


class ControleCampoFiltros(BaseModel):
    municipio_id: Optional[int] = None
    setor_ids: List[int]
    agente_ids: List[int]
    data_inicio: Optional[datetime] = None
    data_fim: Optional[datetime] = None
    resultado: Optional[str] = None
    nota: str


class ControleCampoPerfilTerritorio(BaseModel):
    territorio_id: int
    territorio_nome: Optional[str] = None
    meta_total: int
    realizado_total: int
    percentual_territorio: float
    fase_inicial: bool
    status: str
    celulas: List[PrioridadePerfilItem]


class ControleCampoCotasPerfil(BaseModel):
    plano_ativo: bool
    territorios: List[ControleCampoPerfilTerritorio]
    nao_classificadas: int
    motivos_nao_classificadas: Dict[str, int]
    snapshot_em: Optional[datetime] = None


class ControleCampoTentativaResultado(BaseModel):
    resultado: str
    total: int


class EventoCoberturaGerencial(EventoCoberturaCampo):
    # Identificacao do agente: permitida ao coordenador do MESMO tenant.
    # Nunca email/telefone/CPF; nunca respostas ou dados do entrevistado.
    agente_id: Optional[int] = None
    agente_nome: Optional[str] = None


class ControleCampoAtividade(BaseModel):
    distancia_recomendada_entre_abordagens_metros: int
    distancia_configurada: bool
    eventos: List[EventoCoberturaGerencial]


class ControleCampoRead(BaseModel):
    pesquisa_id: int
    snapshot_em: datetime
    filtros: ControleCampoFiltros
    opcoes: ControleCampoOpcoes
    resumo: ControleCampoResumo
    resumo_territorial: ControleCampoResumoTerritorial
    alertas: List[ControleCampoAlerta]
    setores: List[ControleCampoSetor]
    cotas_perfil: ControleCampoCotasPerfil
    tentativas_por_resultado: List[ControleCampoTentativaResultado]
    atividade_campo: ControleCampoAtividade


class CoberturaCampoGerencialRead(BaseModel):
    pesquisa_id: int
    snapshot_em: datetime
    setores: List[ControleCampoSetor]
    distancia_recomendada_entre_abordagens_metros: int
    distancia_configurada: bool
    eventos: List[EventoCoberturaGerencial]


# --- ACL multiempresa/multiprojeto (ADR-024) ---------------------------------
# `company_id` do usuario continua no contrato como EMPRESA PRINCIPAL/default.
# Os campos abaixo sao ADITIVOS: nenhum consumidor antigo quebra.


class UsuarioEmpresaAcessoItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    company_id: int
    company_nome: Optional[str] = None
    acesso_todos_projetos: bool = True
    ativo: bool = True
    principal: bool = False
    projeto_ids: List[int] = Field(default_factory=list)


class UsuarioAcessosResponse(BaseModel):
    usuario_id: int
    empresa_principal_id: Optional[int] = None
    empresas: List[UsuarioEmpresaAcessoItem] = Field(default_factory=list)


class UsuarioEmpresaAcessoInput(BaseModel):
    # `extra="forbid"`: payload administrativo nao aceita campo desconhecido.
    model_config = ConfigDict(extra="forbid")

    company_id: int = Field(gt=0)
    acesso_todos_projetos: bool = False
    projeto_ids: List[int] = Field(default_factory=list)


class UsuarioAcessosRequest(BaseModel):
    """ACL completa do usuario: o PUT substitui, nao acumula."""

    model_config = ConfigDict(extra="forbid")

    empresa_principal_id: Optional[int] = Field(default=None, gt=0)
    empresas: List[UsuarioEmpresaAcessoInput] = Field(default_factory=list)


class UsuarioResumoAcessos(BaseModel):
    """Resumo para a listagem administrativa (evita N+1 na tabela)."""

    usuario_id: int
    empresas: List[str] = Field(default_factory=list)
    total_empresas: int = 0
    total_projetos_explicitos: int = 0
    acesso_total_em_alguma_empresa: bool = False

# --- Ativacao de conta por convite -------------------------------------------
# O token viaja apenas no link do e-mail; o banco guarda so o hash.


class AtivacaoTokenStatus(BaseModel):
    """Resposta publica da validacao do link.

    Sem `senha_hash`, `token_hash`, ids internos ou dado de terceiros: `email`
    e `nome` so aparecem quando o token e valido -- e para quem ja tem o link.
    """

    status: str
    valido: bool
    email: Optional[EmailStr] = None
    nome: Optional[str] = None
    expira_em: Optional[datetime] = None


class AtivacaoContaRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str = Field(min_length=1)
    senha: str
    # Confirmacao e opcional no contrato: quando vem, o Backend confere -- nao
    # delega ao cliente uma validacao que ele pode pular.
    confirmacao_senha: Optional[str] = None


class AtivacaoContaResponse(BaseModel):
    ativado: bool
    email: EmailStr
    mensagem: str


class ReenvioAtivacaoResponse(BaseModel):
    # `enviado` reflete a entrega do e-mail; o convite existe de qualquer forma,
    # e por isso `activation_url` vem sempre.
    enviado: bool
    email: EmailStr
    expira_em: datetime
    activation_url: str

# --- Link de convite devolvido ao painel administrativo ----------------------
# TRANSITORIO: `activation_url` embute o token puro e vale como credencial de
# ativacao ate ser usado. So aparece na resposta de quem acabou de criar/renovar
# o convite -- nunca em listagem, nunca em GET, nunca no banco.


class ConviteAtivacao(BaseModel):
    activation_url: str
    expira_em: datetime
    email_enviado: bool


class UsuarioCriadoResponse(Usuario):
    """Resposta da CRIACAO de usuario.

    Schema proprio de proposito: `GET /usuarios/` continua respondendo
    `schemas.Usuario`, sem qualquer campo de convite. `convite` fica `null`
    quando a criacao veio com senha administrativa -- nao se inventa link para
    um convite que nao existe.
    """

    convite: Optional[ConviteAtivacao] = None


# --- Auditoria (ADR-039) -----------------------------------------------------
class AuditEventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    occurred_at: datetime
    event_type: str
    severity: str
    user_id: Optional[int] = None
    company_id: Optional[int] = None
    project_id: Optional[int] = None
    attempted_email: Optional[str] = None
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None
    http_method: Optional[str] = None
    path: Optional[str] = None
    status_code: Optional[int] = None
    request_id: Optional[str] = None
    details: Optional[dict] = None


class AuditEventPage(BaseModel):
    """Mesmo formato de pagina ja usado em TerritorioEleitoralPage."""
    model_config = ConfigDict(extra="forbid")

    items: List[AuditEventRead]
    total: int
    limit: int
    offset: int


# --- painel de seguranca (ADR-041) --------------------------------------------

class AuditPeriodo(BaseModel):
    data_inicio: datetime
    data_fim: datetime


class AuditTotais(BaseModel):
    total_eventos: int
    login_failed: int          # LOGIN_FAILED + ACCOUNT_INACTIVE_LOGIN
    access_denied: int         # RBAC_DENIED + ACCESS_DENIED (cross-tenant NAO entra aqui)
    cross_tenant: int          # CROSS_TENANT_ACCESS_ATTEMPT
    project_access: int        # PROJECT_ACCESS (ja deduplicado na origem)
    notification_failed: int   # PROJECT_ACCESS_NOTIFICATION_FAILED (SUPPRESSED nao e falha)
    high: int                  # severity = HIGH


class AuditTopIp(BaseModel):
    ip_address: str
    total: int
    login_failed: int
    access_denied: int
    cross_tenant: int
    last_event_at: Optional[datetime] = None


class AuditTopAccount(BaseModel):
    attempted_email: str
    total: int
    last_event_at: Optional[datetime] = None


class AuditTimelinePoint(BaseModel):
    periodo: str               # inicio do bucket, ISO sem offset (UTC)
    login_failed: int
    access_denied: int
    cross_tenant: int


class AuditSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    periodo: AuditPeriodo
    granularidade: str         # "hora" (<= 48h) | "dia"
    totais: AuditTotais
    top_ips: List[AuditTopIp]
    top_attempted_accounts: List[AuditTopAccount]
    timeline: List[AuditTimelinePoint]
