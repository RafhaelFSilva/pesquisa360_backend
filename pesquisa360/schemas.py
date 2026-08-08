# pesquisa360/schemas.py (versão final simplificada)

from pydantic import BaseModel, EmailStr, Field, field_validator, ConfigDict
from typing import Optional, List, Any, Union, Dict, Literal
from datetime import date, datetime
from enum import StrEnum
from uuid import UUID
import re
from urllib.parse import urlparse
#from geoalchemy2.elements import WKBElement # <-- NOVA IMPORTAÇÃO
#from shapely.wkb import loads # <-- NOVA IMPORTAÇÃO

from .question_types import normalize_question_type

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

# Schema de SAÍDA simplificado. A conversão da geolocalização será feita manualmente no endpoint.
class Coleta(BaseModel):
    id: int
    client_uuid: UUID
    pesquisa_id: int
    agente_id: int
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
        
class PerguntaBase(BaseModel):
    texto_pergunta: str
    tipo_pergunta: str
    ordem: int
    eh_obrigatoria: bool = True
    eh_resposta_espontanea: bool = False
    opcoes: Optional[List[OpcaoCreate]] = None # <--- Alterado para OpcaoCreate

    @field_validator("tipo_pergunta")
    @classmethod
    def normalize_tipo_pergunta(cls, value: str) -> str:
        return normalize_question_type(value)

class PerguntaCreate(PerguntaBase):
    ordem: Optional[int] = None

class PerguntaUpdate(BaseModel):
    texto_pergunta: Optional[str] = None
    tipo_pergunta: Optional[str] = None
    ordem: Optional[int] = None
    eh_obrigatoria: Optional[bool] = None
    eh_resposta_espontanea: Optional[bool] = None
    opcoes: Optional[List[Any]] = None
    ativo: Optional[bool] = None

    @field_validator("tipo_pergunta")
    @classmethod
    def normalize_tipo_pergunta(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        return normalize_question_type(value)

class PerguntaReordenarItem(BaseModel):
    id: int
    ordem: int = Field(..., ge=1)

class PerguntasReordenarPayload(BaseModel):
    perguntas: List[PerguntaReordenarItem]

class Pergunta(PerguntaBase):
    id: int
    pesquisa_id: int
    ativo: bool
    opcoes: List[Opcao] = [] # <--- Força o uso do schema com ID na leitura
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
    senha: str
    company_id: Optional[int] = None

class UsuarioAdminCreate(BaseModel):
    email: EmailStr
    nome: Optional[str] = None
    senha: str
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
    company_id: int
    perfil_nome: Optional[str] = None
    
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

class SetorBase(BaseModel):
    nome: str
    meta: int
    agente_id: Optional[int] = None
    tolerancia: int = 50
    finalidade: FinalidadeSetor = FinalidadeSetor.OPERACAO

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
    tolerancia_metros: Optional[int] = 50
    finalidade: FinalidadeSetor = FinalidadeSetor.OPERACAO
    geometria: Optional[List[dict]] = None
    poligono: Optional[List[dict]] = None

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
    tolerancia_metros: Optional[int] = None
    finalidade: Optional[FinalidadeSetor] = None
    geometria: Optional[List[dict]] = None
    poligono: Optional[List[dict]] = None

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
    coordenador: UsuarioParaProjeto
    pesquisas: List[PesquisaResumo] = [] # Usa resumo sem cerca_eletronica para evitar WKBElement
    class Config:
        from_attributes = True

class Token(BaseModel):
    access_token: str
    token_type: str

class TokenData(BaseModel):
    email: Optional[EmailStr] = None

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

# --- Atualização de referências ---
# Garante que os schemas que se referenciam mutuamente sejam resolvidos
Projeto.model_rebuild()
Usuario.model_rebuild()
Pesquisa.model_rebuild()
