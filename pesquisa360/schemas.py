# pesquisa360/schemas.py (versão final simplificada)

from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator, ConfigDict
from typing import Optional, List, Any, Union, Dict, Literal
from datetime import date, datetime
from enum import StrEnum
from uuid import UUID
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
    papel_analitico: Optional[PapelAnalitico] = None
    metadados_analiticos: Dict[str, Any] = Field(default_factory=dict)
    opcoes: Optional[List[OpcaoCreate]] = None # <--- Alterado para OpcaoCreate

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


class DivergenciaBaseEleitoralResponse(BaseModel):
    """Espelha o JSON auditado do lote; o formato varia por tipo de divergencia."""

    model_config = ConfigDict(extra="allow")

    tipo_divergencia: str
    importacao_id: int
    arquivo_origem: str
    resolvida: bool = False


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


# --- Atualização de referências ---
# Garante que os schemas que se referenciam mutuamente sejam resolvidos
Projeto.model_rebuild()
Usuario.model_rebuild()
Pesquisa.model_rebuild()
