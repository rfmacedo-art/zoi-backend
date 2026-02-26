"""
=============================================================================
ZOI SENTINEL v5.0 - Zero Database Architecture
Claude AI (Anthropic) Integration + Intelligent Fallback
=============================================================================

Fluxo de pesquisa:
1. Cliente pede produto → verifica cache
2. Se não tem cache → retorna dados de referência + dispara pesquisa Claude
3. Claude pesquisa na web (MAPA, ANVISA, EUR-Lex, RASFF) via web_search tool
4. Resultado fica em cache para próximas requisições
5. Cliente pode forçar refresh via /refresh endpoint

Integração Claude API:
- SDK anthropic (AsyncAnthropic)
- Modelo: claude-sonnet-4-20250514 (configurável via CLAUDE_MODEL)
- Tool web_search nativa — Claude navega fontes reais e retorna JSON estruturado
- Chamada síncrona única — sem polling, sem timeout de 5 min
=============================================================================
"""

import os
import io
import json
import asyncio
import logging
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List

import httpx
import anthropic
from fastapi import FastAPI, HTTPException, Request, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, JSONResponse, Response

# ============================================================================
# LOGGING
# ============================================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - ZOI_SENTINEL_V4 - %(levelname)s - %(message)s"
)
logger = logging.getLogger("ZOI_SENTINEL_V4")

# ============================================================================
# APP
# ============================================================================
app = FastAPI(
    title="ZOI Sentinel v5.0 - Trade Advisory",
    description="Zero Database Architecture - Real-time Claude AI Compliance Research",
    version="5.0.0"
)

# ============================================================================
# CORS (middleware)
# ============================================================================
LOVABLE_PROJECT_ID = os.environ.get(
    "LOVABLE_PROJECT_ID",
    "c3f2427f-f2dc-48b6-a9da-a99a6d34fdff"
)

ALLOWED_ORIGINS = [
    f"https://{LOVABLE_PROJECT_ID}.lovableproject.com",
    f"https://preview--{LOVABLE_PROJECT_ID}.lovableproject.com",
    "https://zoi-trade-navigator.lovable.app",
    "https://id-preview--c3f2427f-f2dc-48b6-a9da-a99a6d34fdff.lovable.app",
    "https://zoi-sentinel-nav.lovable.app",
    "http://localhost:5173",
    "http://localhost:3000",
    "http://localhost:8080",
]

extra_origins = os.environ.get("EXTRA_CORS_ORIGINS", "")
if extra_origins:
    ALLOWED_ORIGINS.extend([o.strip() for o in extra_origins.split(",") if o.strip()])

class BareOptionsMiddleware:
    def __init__(self, app_instance):
        self.app_instance = app_instance

    async def __call__(self, scope, receive, send):
        if scope.get("type") == "http" and scope.get("method") == "OPTIONS":
            headers = {k.decode(): v.decode() for k, v in scope.get("headers", [])}
            if "access-control-request-method" not in headers:
                origin = headers.get("origin", "")
                allow_origin = origin if origin in ALLOWED_ORIGINS else ALLOWED_ORIGINS[0]
                allow_headers = headers.get("access-control-request-headers", "*")
                response = Response(
                    status_code=200,
                    headers={
                        "Access-Control-Allow-Origin": allow_origin,
                        "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, OPTIONS, PATCH",
                        "Access-Control-Allow-Headers": allow_headers,
                        "Access-Control-Allow-Credentials": "true",
                    },
                )
                await response(scope, receive, send)
                return
        await self.app_instance(scope, receive, send)


app.add_middleware(BareOptionsMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Disposition", "X-ZOI-Version"],
    max_age=3600,
)


# ============================================================================
# CONFIGURATION
# ============================================================================
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-20250514")

# Cache TTL
CACHE_TTL_HOURS = int(os.environ.get("CACHE_TTL_HOURS", "24"))

# Timeout para pesquisa Claude (segundos) — bem menor que Manus
CLAUDE_RESEARCH_TIMEOUT = int(os.environ.get("CLAUDE_RESEARCH_TIMEOUT", "90"))


# ============================================================================
# REGULATORY TRUTH LAYER
# Valida e corrige dados retornados pelo Manus antes de servir ao frontend.
# Esta camada garante que substâncias banidas na UE nunca apareçam como
# "Conforme", independente do que o Manus ou o cache retornarem.
# Fonte verificada: EUR-Lex, EFSA, Reg. (CE) 396/2005 — Fev/2026
# ============================================================================

EU_BANNED_SUBSTANCES = {
    "carbendazim":   {"limit": "0.01 mg/kg", "status": "BANIDO", "regulation": "Reg. (CE) 396/2005", "note": "Banido na UE — mutagênico e tóxico para reprodução."},
    "imidacloprid":  {"limit": "0.01 mg/kg", "status": "BANIDO", "regulation": "Reg. Impl. (UE) 2018/783", "note": "Banido na UE desde 2018 — neonicotinoide."},
    "thiamethoxam":  {"limit": "0.01 mg/kg", "status": "BANIDO", "regulation": "Reg. Impl. (UE) 2018/785", "note": "Banido na UE desde 2018 — neonicotinoide."},
    "clothianidin":  {"limit": "0.01 mg/kg", "status": "BANIDO", "regulation": "Reg. Impl. (UE) 2018/784", "note": "Banido na UE desde 2018 — neonicotinoide."},
    "thiacloprid":   {"limit": "0.01 mg/kg", "status": "BANIDO", "regulation": "Reg. (UE) 2020/23",   "note": "Aprovação não renovada — banido desde Fev/2020."},
}

def apply_regulatory_truth(data: Dict) -> Dict:
    """
    Pós-processamento obrigatório em TODOS os dados antes de servir ao frontend.
    Corrige erros de alucinação do Manus sobre substâncias banidas na UE.
    """
    if not isinstance(data, dict):
        return data

    mrl = data.get("max_residue_limits", {})
    if not isinstance(mrl, dict):
        return data

    has_banned = False
    corrected = False

    for substance_key, substance_data in mrl.items():
        normalized = substance_key.lower().replace("-", "").replace("_", "").replace(" ", "")
        for banned_key, banned_truth in EU_BANNED_SUBSTANCES.items():
            if banned_key.replace("-", "") in normalized or normalized in banned_key.replace("-", ""):
                if isinstance(substance_data, dict):
                    original_status = substance_data.get("status", "")
                    if original_status != "BANIDO":
                        logger.warning(
                            f"⚠️ REGULATORY CORRECTION: '{substance_key}' estava como '{original_status}' "
                            f"mas é BANIDO na UE. Corrigindo automaticamente."
                        )
                        corrected = True
                    mrl[substance_key] = {**substance_data, **banned_truth}
                else:
                    mrl[substance_key] = banned_truth
                    corrected = True
                has_banned = True
                break

    if has_banned:
        # Recalcular taxa de conformidade
        total = len(mrl)
        banned_count = sum(1 for v in mrl.values() if isinstance(v, dict) and v.get("status") == "BANIDO")
        conformity_pct = int(((total - banned_count) / total) * 100) if total > 0 else 100

        # Status não pode ser APPROVED se há substâncias banidas
        current_status = data.get("status", "")
        if banned_count > 0 and "APPROVED" in current_status.upper():
            data["status"] = "REQUIRES ATTENTION"
            logger.warning(f"⚠️ STATUS CORRECTION: produto tinha '{current_status}' mas tem {banned_count} substância(s) BANIDA(s). Corrigido para 'REQUIRES ATTENTION'.")

        # Risk score não pode ser alto se há substâncias banidas
        if banned_count > 0:
            max_allowed_score = 79
            if data.get("risk_score", 0) > max_allowed_score:
                data["risk_score"] = max_allowed_score

        data["lmr_conformity_pct"] = conformity_pct
        data["lmr_banned_count"] = banned_count

    if corrected:
        data["regulatory_correction_applied"] = True

    data["max_residue_limits"] = mrl
    return data



PRODUCT_CACHE: Dict[str, Dict[str, Any]] = {}
CLAUDE_RESEARCH_TASKS: Dict[str, Dict[str, Any]] = {}  # track ongoing Claude research per product


def get_cached(slug: str) -> Optional[Dict]:
    if slug in PRODUCT_CACHE:
        cached = PRODUCT_CACHE[slug]
        try:
            cached_time = datetime.fromisoformat(cached.get("last_updated", "2000-01-01"))
            if datetime.now() - cached_time < timedelta(hours=CACHE_TTL_HOURS):
                return cached
        except Exception:
            pass
        del PRODUCT_CACHE[slug]
    return None


def set_cached(slug: str, data: Dict):
    data["last_updated"] = datetime.now().isoformat()
    PRODUCT_CACHE[slug] = data


# ============================================================================
# CLAUDE AI - INTEGRAÇÃO ANTHROPIC
# ============================================================================

def build_compliance_prompt(product_name: str) -> str:
    """
    Prompt otimizado para o Claude pesquisar compliance de exportação via web_search.
    Instrui o Claude a consultar fontes oficiais e retornar JSON estruturado.
    """
    return f"""Você é um especialista em compliance de comércio exterior Brasil ↔ Itália/UE.

Pesquise na web os requisitos regulatórios atuais para exportação/importação de "{product_name}" na rota Brasil ↔ Itália.

Consulte obrigatoriamente: mapa.gov.br, anvisa.gov.br, receita.fazenda.gov.br (NCM), eur-lex.europa.eu, ec.europa.eu/food/plant/pesticides/eu-pesticides-database, RASFF (rasff.eu).

REGRAS CRÍTICAS — NUNCA IGNORAR:
1. Carbendazim, Imidacloprid, Thiamethoxam, Clothianidin, Thiacloprid estão BANIDOS na UE. Se presentes, status = "BANIDO", limit = "0.01 mg/kg". NUNCA classifique como "Conforme".
2. NCM deve corresponder à forma comercial exata (polpa congelada ≠ fruta fresca ≠ suco concentrado).
3. Se produto tem substâncias banidas, status NUNCA pode ser "APPROVED" — usar "REQUIRES ATTENTION".

Retorne APENAS o JSON abaixo, sem texto adicional, sem markdown, sem backticks:
{{
    "ncm_code": "código NCM correto para a forma exportada",
    "product_name": "{product_name}",
    "product_name_it": "nome em italiano",
    "product_name_en": "nome em inglês",
    "category": "categoria do produto",
    "risk_score": 0-100,
    "risk_level": "LOW/MEDIUM/HIGH",
    "status": "ZOI APPROVED/REQUIRES ATTENTION/BLOCKED",
    "trade_route": {{"origin": "BR ou IT", "destination": "IT ou BR", "origin_name": "Brasil ou Itália", "destination_name": "Itália ou Brasil"}},
    "certificates_required": [{{"name": "nome", "issuer": "emissor", "mandatory": true}}],
    "eu_regulations": [{{"code": "Reg. ...", "title": "título", "status": "active"}}],
    "brazilian_requirements": ["requisito 1", "requisito 2"],
    "max_residue_limits": {{
        "substancia": {{
            "limit": "X mg/kg",
            "status": "BANIDO ou CONFORME",
            "regulation": "Regulamento aplicável",
            "note": "explicação"
        }}
    }},
    "tariff_info": {{"eu_tariff": "X%", "notes": "informações tarifárias"}},
    "alerts": ["alerta importante 1"],
    "sources_consulted": ["url1", "url2"]
}}"""


def _extract_text_from_blocks(content_blocks) -> str:
    """Extrai todo o texto dos blocos de conteúdo de uma resposta Claude."""
    text = ""
    for block in content_blocks:
        if hasattr(block, "text") and block.text:
            text += block.text
    return text


def _parse_compliance_json(text_content: str) -> Optional[Dict]:
    """Tenta parsear JSON de compliance do texto retornado pelo Claude."""
    import re

    # 1. JSON direto (ideal — sem markdown)
    try:
        data = json.loads(text_content.strip())
        if isinstance(data, dict) and "ncm_code" in data:
            return data
    except json.JSONDecodeError:
        pass

    # 2. Bloco ```json ... ```
    match = re.search(r'```(?:json)?\s*([\s\S]*?)\s*```', text_content)
    if match:
        try:
            data = json.loads(match.group(1).strip())
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            pass

    # 3. Objeto JSON inline no texto (fallback)
    match = re.search(r'(\{[\s\S]*?"ncm_code"[\s\S]*?\})\s*$', text_content)
    if match:
        try:
            data = json.loads(match.group(1))
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            pass

    return None


async def research_product_via_claude(product_slug: str, product_name: str) -> Optional[Dict]:
    """
    Pesquisa compliance via Claude API com loop agêntico para web_search.

    Fluxo correto da API Anthropic com tools:
    1. Enviamos prompt com tool web_search disponível
    2. Claude responde com stop_reason="tool_use" — contém blocos tool_use (chamadas de busca)
    3. Nós coletamos os blocos tool_use e enviamos de volta como tool_result (conteúdo = string vazia,
       pois web_search_20250305 é server-side — o resultado já está implícito no contexto)
    4. Claude responde com stop_reason="end_turn" e o texto final com o JSON
    5. Se após MAX_TURNS ainda não houver texto, cai no fallback sem web_search
    """
    if not ANTHROPIC_API_KEY:
        logger.warning("⚠️ ANTHROPIC_API_KEY não configurada")
        return None

    logger.info(f"🤖 CLAUDE RESEARCH START: {product_name}")
    CLAUDE_RESEARCH_TASKS[product_slug] = {
        "status": "running",
        "started_at": datetime.now().isoformat(),
    }

    try:
        client = anthropic.AsyncAnthropic(
            api_key=ANTHROPIC_API_KEY,
            timeout=CLAUDE_RESEARCH_TIMEOUT,
        )

        messages = [{"role": "user", "content": build_compliance_prompt(product_name)}]
        tools = [{"type": "web_search_20250305", "name": "web_search"}]
        MAX_TURNS = 8  # segurança contra loop infinito
        text_content = ""

        for turn in range(MAX_TURNS):
            logger.info(f"🔄 Claude turn {turn + 1}/{MAX_TURNS} para: {product_name}")

            response = await client.messages.create(
                model=CLAUDE_MODEL,
                max_tokens=4096,
                tools=tools,
                messages=messages,
            )

            logger.info(f"   stop_reason={response.stop_reason} | blocos={len(response.content)}")

            # Coletar texto desta resposta
            turn_text = _extract_text_from_blocks(response.content)
            if turn_text:
                text_content += turn_text
                logger.info(f"   texto acumulado: {len(text_content)} chars")

            # Se parou por end_turn — temos a resposta final
            if response.stop_reason == "end_turn":
                logger.info(f"✅ Claude finalizou em {turn + 1} turno(s)")
                break

            # Se parou por tool_use — precisamos continuar o loop
            if response.stop_reason == "tool_use":
                # Adicionar a resposta do assistente ao histórico
                messages.append({"role": "assistant", "content": response.content})

                # Construir tool_result para cada bloco tool_use
                tool_results = []
                for block in response.content:
                    if block.type == "tool_use":
                        logger.info(f"   🔍 web_search chamado: {getattr(block, 'input', {})}")
                        # web_search_20250305 é server-side: o resultado já foi processado
                        # internamente pela Anthropic. Enviamos tool_result vazio para continuar.
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": "",
                        })

                if tool_results:
                    messages.append({"role": "user", "content": tool_results})
                else:
                    # Nenhum tool_use encontrado mas stop_reason=tool_use — situação inesperada
                    logger.warning("   ⚠️ stop_reason=tool_use mas nenhum bloco tool_use encontrado")
                    break
                continue

            # Outro stop_reason (max_tokens, error...) — sair
            logger.warning(f"   ⚠️ stop_reason inesperado: {response.stop_reason}")
            break

        # ── Tentativa de parse do JSON ──────────────────────────────────────
        logger.info(f"📝 Texto total coletado: {len(text_content)} chars")
        logger.info(f"   Preview: {text_content[:300]}")

        compliance_data = _parse_compliance_json(text_content) if text_content.strip() else None

        # ── Fallback: chamar Claude SEM web_search usando só conhecimento interno ──
        if not compliance_data:
            logger.warning(f"⚠️ Loop com web_search não produziu JSON. Tentando fallback sem web_search...")
            fallback_response = await client.messages.create(
                model=CLAUDE_MODEL,
                max_tokens=4096,
                messages=[{
                    "role": "user",
                    "content": build_compliance_prompt(product_name) +
                        "\n\nIMPORTANTE: Use seu conhecimento interno de treinamento. Retorne APENAS o JSON, sem texto adicional."
                }],
            )
            fallback_text = _extract_text_from_blocks(fallback_response.content)
            logger.info(f"   Fallback texto: {len(fallback_text)} chars | preview: {fallback_text[:200]}")
            compliance_data = _parse_compliance_json(fallback_text)

        if not compliance_data or not isinstance(compliance_data, dict):
            logger.warning(f"⚠️ Claude não produziu JSON válido para: {product_name}")
            CLAUDE_RESEARCH_TASKS[product_slug]["status"] = "parse_error"
            return None

        # Enriquecer com metadados
        compliance_data["data_source"] = "claude_ai_realtime"
        compliance_data["claude_model"] = CLAUDE_MODEL
        compliance_data["needs_ai_update"] = False
        compliance_data["last_updated"] = datetime.now().isoformat()
        compliance_data.setdefault("trade_route", {
            "origin": "BR", "destination": "IT",
            "origin_name": "Brasil", "destination_name": "Itália"
        })

        # ⚡ VALIDAÇÃO REGULATÓRIA
        compliance_data = apply_regulatory_truth(compliance_data)

        CLAUDE_RESEARCH_TASKS[product_slug]["status"] = "completed"
        logger.info(f"✅ CLAUDE RESEARCH COMPLETE: {product_name}")
        return compliance_data

    except anthropic.APITimeoutError:
        logger.error(f"⏰ Claude timeout ({CLAUDE_RESEARCH_TIMEOUT}s) para: {product_name}")
        CLAUDE_RESEARCH_TASKS[product_slug]["status"] = "timeout"
        return None
    except anthropic.APIError as e:
        logger.error(f"❌ Claude API error para {product_name}: {e}")
        CLAUDE_RESEARCH_TASKS[product_slug]["status"] = "api_error"
        return None
    except Exception as e:
        logger.error(f"❌ Erro inesperado na pesquisa Claude para {product_name}: {e}", exc_info=True)
        CLAUDE_RESEARCH_TASKS[product_slug]["status"] = "error"
        return None


# ============================================================================
# BACKGROUND RESEARCH (não bloqueia a resposta ao cliente)
# ============================================================================

async def background_claude_research(product_slug: str, product_name: str):
    """
    Executa pesquisa Claude em background.
    O cliente recebe resposta imediata com dados de referência.
    Quando Claude completar (~20-40s), o cache é atualizado.
    """
    try:
        result = await research_product_via_claude(product_slug, product_name)
        if result:
            set_cached(product_slug, result)
            logger.info(f"🔄 Background Claude research cached: {product_slug}")
    except Exception as e:
        logger.error(f"❌ Background Claude research error for {product_slug}: {e}")


# ============================================================================
# KNOWLEDGE BASE (fallback quando Manus não disponível)
# ============================================================================

REFERENCE_DATA = {
    "soja_grao": {
        "ncm_code": "1201.90.00",
        "product_name": "Soja em Grãos",
        "product_name_it": "Semi di Soia",
        "product_name_en": "Soybeans",
        "category": "Grãos e Cereais",
        "risk_score": 100,
        "risk_level": "LOW",
        "status": "ZOI APPROVED",
        "trade_route": {"origin": "BR", "destination": "IT", "origin_name": "Brasil", "destination_name": "Itália"},
        "certificates_required": [
            {"name": "Certificado Fitossanitário", "issuer": "MAPA", "mandatory": True},
            {"name": "Certificado de Origem", "issuer": "Câmara de Comércio", "mandatory": True},
            {"name": "Bill of Lading", "issuer": "Transportadora", "mandatory": True},
            {"name": "Commercial Invoice", "issuer": "Exportador", "mandatory": True},
            {"name": "Packing List", "issuer": "Exportador", "mandatory": True},
            {"name": "Certificado de Fumigação", "issuer": "Empresa certificada", "mandatory": False},
        ],
        "eu_regulations": [
            {"code": "Reg. (CE) 178/2002", "title": "Segurança alimentar geral", "status": "active"},
            {"code": "Reg. (CE) 1881/2006", "title": "Limites de contaminantes em alimentos", "status": "active"},
            {"code": "Reg. (UE) 2023/915", "title": "Limites de micotoxinas", "status": "active"},
            {"code": "Reg. (CE) 1829/2003", "title": "Alimentos geneticamente modificados", "status": "active"},
        ],
        "brazilian_requirements": [
            "Registro no MAPA como exportador de grãos",
            "Certificado fitossanitário emitido pelo SDA/MAPA",
            "Análise de resíduos de pesticidas (LMR conforme Codex)",
            "Análise de micotoxinas (aflatoxinas B1, B2, G1, G2)",
            "Declaração de OGM/não-OGM conforme Reg. 1829/2003",
        ],
        "max_residue_limits": {
            "aflatoxinas_total": {"limit": "4 µg/kg", "regulation": "Reg. 1881/2006"},
            "aflatoxina_b1": {"limit": "2 µg/kg", "regulation": "Reg. 1881/2006"},
            "glifosato": {"limit": "20 mg/kg", "regulation": "Reg. 396/2005"},
        },
        "tariff_info": {"eu_tariff": "0%", "notes": "Tarifa zero para soja em grãos"},
        "alerts": [],
        "risk_factors": {
            "documentation": {"score": 100, "level": "LOW"},
            "regulatory": {"score": 95, "level": "LOW"},
            "logistics": {"score": 90, "level": "LOW"},
            "market_access": {"score": 100, "level": "LOW"},
        },
    },
    "acai": {
        "ncm_code": "0811.90.10",  # Polpa congelada - CORRETO. 0810.90.00 seria fruta fresca.
        "product_name": "Açaí (Polpa/Fruto)",
        "product_name_it": "Açaí (Polpa/Frutto)",
        "product_name_en": "Açaí Berry (Pulp/Fruit)",
        "category": "Frutas Tropicais",
        "risk_score": 85,
        "risk_level": "LOW",
        "status": "ZOI APPROVED",
        "trade_route": {"origin": "BR", "destination": "IT", "origin_name": "Brasil", "destination_name": "Itália"},
        "certificates_required": [
            {"name": "Certificado Fitossanitário", "issuer": "MAPA", "mandatory": True},
            {"name": "Certificado Sanitário", "issuer": "ANVISA/SIF", "mandatory": True},
            {"name": "Certificado de Origem", "issuer": "Câmara de Comércio", "mandatory": True},
            {"name": "Laudo Microbiológico", "issuer": "Laboratório acreditado", "mandatory": True},
            {"name": "Análise de Resíduos", "issuer": "Laboratório acreditado", "mandatory": True},
        ],
        "eu_regulations": [
            {"code": "Reg. (CE) 178/2002", "title": "Segurança alimentar geral", "status": "active"},
            {"code": "Reg. (CE) 396/2005", "title": "Limites máximos de resíduos de pesticidas", "status": "active"},
            {"code": "Reg. (UE) 1169/2011", "title": "Rotulagem de alimentos", "status": "active"},
            {"code": "Reg. (CE) 852/2004", "title": "Higiene dos gêneros alimentícios", "status": "active"},
            {"code": "Reg. (UE) 2019/1793", "title": "Controles reforçados na entrada de alimentos de países terceiros", "status": "active"},
        ],
        "brazilian_requirements": [
            "Registro no MAPA/SIF",
            "Boas Práticas de Fabricação (BPF/GMP)",
            "Certificado fitossanitário",
            "Controle de cadeia fria (-18°C para polpa congelada)",
            "APPCC/HACCP implementado",
        ],
        "max_residue_limits": {
            # SUBSTÂNCIAS BANIDAS NA UE — MRL = 0.01 mg/kg (tolerância zero)
            # Fonte: Reg. Impl. (UE) 2018/783, 2018/784, 2018/785 + Reg. 396/2005
            "carbendazim": {
                "limit": "0.01 mg/kg",
                "status": "BANIDO",
                "regulation": "Reg. (CE) 396/2005",
                "note": "Banido na UE — mutagênico e tóxico para reprodução. Tolerância zero."
            },
            "imidacloprid": {
                "limit": "0.01 mg/kg",
                "status": "BANIDO",
                "regulation": "Reg. Impl. (UE) 2018/783",
                "note": "Banido na UE desde 2018 — neonicotinoide neurotóxico para abelhas."
            },
            "thiamethoxam": {
                "limit": "0.01 mg/kg",
                "status": "BANIDO",
                "regulation": "Reg. Impl. (UE) 2018/785",
                "note": "Banido na UE desde 2018 — neonicotinoide neurotóxico para abelhas."
            },
            "clothianidin": {
                "limit": "0.01 mg/kg",
                "status": "BANIDO",
                "regulation": "Reg. Impl. (UE) 2018/784",
                "note": "Banido na UE desde 2018 — neonicotinoide neurotóxico para abelhas."
            },
            "glyphosate": {
                "limit": "0.1 mg/kg",
                "status": "CONFORME",
                "regulation": "Reg. (CE) 396/2005",
                "note": "MRL 0.1 mg/kg para frutas — posição NCM 08."
            },
            "cypermethrin": {
                "limit": "0.05 mg/kg",
                "status": "CONFORME",
                "regulation": "Reg. (CE) 396/2005",
                "note": "MRL 0.05 mg/kg para frutas — posição NCM 08."
            },
        },
        "tariff_info": {"eu_tariff": "8.8%", "notes": "Tarifa para frutas tropicais"},
        "alerts": ["⚠️ Atenção à cadeia fria - açaí é altamente perecível"],
        "risk_factors": {
            "documentation": {"score": 85, "level": "LOW"},
            "regulatory": {"score": 80, "level": "MEDIUM"},
            "logistics": {"score": 75, "level": "MEDIUM"},
            "market_access": {"score": 90, "level": "LOW"},
        },
    },
    "cafe": {
        "ncm_code": "0901.11.00",
        "product_name": "Café Verde (Grãos não torrados)",
        "product_name_it": "Caffè Verde",
        "product_name_en": "Green Coffee Beans",
        "category": "Bebidas",
        "risk_score": 95,
        "risk_level": "LOW",
        "status": "ZOI APPROVED",
        "trade_route": {"origin": "BR", "destination": "IT", "origin_name": "Brasil", "destination_name": "Itália"},
        "certificates_required": [
            {"name": "Certificado Fitossanitário", "issuer": "MAPA", "mandatory": True},
            {"name": "Certificado de Origem", "issuer": "Câmara de Comércio", "mandatory": True},
            {"name": "ICO Certificate of Origin", "issuer": "CECAFÉ", "mandatory": True},
        ],
        "eu_regulations": [
            {"code": "Reg. (CE) 178/2002", "title": "Segurança alimentar geral", "status": "active"},
            {"code": "Reg. (CE) 1881/2006", "title": "Limites de contaminantes", "status": "active"},
            {"code": "Reg. (UE) 2023/1115", "title": "EUDR - Regulamento anti-desmatamento", "status": "active"},
        ],
        "brazilian_requirements": [
            "Registro no CECAFÉ",
            "Classificação oficial do café",
            "Due Diligence EUDR - rastreabilidade até a fazenda",
        ],
        "max_residue_limits": {
            "ocratoxina_a": {"limit": "5 µg/kg (torrado)", "regulation": "Reg. 1881/2006"},
        },
        "tariff_info": {"eu_tariff": "0%", "notes": "Café verde com tarifa zero na UE"},
        "alerts": ["🌿 EUDR: obrigatória due diligence anti-desmatamento"],
        "risk_factors": {
            "documentation": {"score": 95, "level": "LOW"},
            "regulatory": {"score": 90, "level": "LOW"},
            "logistics": {"score": 95, "level": "LOW"},
            "market_access": {"score": 100, "level": "LOW"},
        },
    },

    # =========================================================================
    # BRASIL → ITÁLIA — FRESCOS E CONGELADOS
    # Fonte: MDIC/ComexStat 2023-2024, MAPA AgroStat, Reg. (CE) 396/2005
    # =========================================================================

    "carne_bovina_congelada": {
        "ncm_code": "0202.30.00",
        "product_name": "Carne Bovina Congelada (desossada)",
        "product_name_it": "Carne Bovina Congelata (disossata)",
        "product_name_en": "Frozen Boneless Beef",
        "category": "Carnes e Proteínas Animais",
        "risk_score": 80,
        "risk_level": "MEDIUM",
        "status": "ZOI APPROVED",
        "trade_route": {"origin": "BR", "destination": "IT", "origin_name": "Brasil", "destination_name": "Itália"},
        "certificates_required": [
            {"name": "Certificado Sanitário Internacional (CSI)", "issuer": "MAPA/SIF", "mandatory": True},
            {"name": "Certificado Fitossanitário", "issuer": "MAPA", "mandatory": True},
            {"name": "Certificado de Habilitação do Estabelecimento", "issuer": "MAPA", "mandatory": True},
            {"name": "Certificado de Origem", "issuer": "Câmara de Comércio", "mandatory": True},
            {"name": "Análise de Resíduos (hormônios e antibióticos)", "issuer": "Laboratório acreditado", "mandatory": True},
        ],
        "eu_regulations": [
            {"code": "Reg. (CE) 178/2002", "title": "Segurança alimentar geral", "status": "active"},
            {"code": "Reg. (CE) 852/2004", "title": "Higiene dos gêneros alimentícios", "status": "active"},
            {"code": "Reg. (CE) 853/2004", "title": "Regras de higiene para produtos de origem animal", "status": "active"},
            {"code": "Reg. (CE) 854/2004", "title": "Controlo oficial de produtos de origem animal", "status": "active"},
            {"code": "Reg. (UE) 2019/627", "title": "Controlo oficial de produtos de origem animal", "status": "active"},
            {"code": "Reg. (CE) 396/2005", "title": "LMR de pesticidas", "status": "active"},
        ],
        "brazilian_requirements": [
            "Estabelecimento habilitado pelo MAPA para exportação à UE (lista positiva)",
            "SIF ativo — inspeção federal obrigatória",
            "APPCC/HACCP implementado",
            "Cadeia fria mantida ≤ -18°C",
            "Rastreabilidade individual do animal (SISBOV recomendado)",
            "Proibição de hormônios de crescimento (Diretiva CE 96/22)",
        ],
        "max_residue_limits": {
            "ivermectin": {"limit": "0.01 mg/kg", "status": "CONFORME", "regulation": "Reg. (CE) 396/2005", "note": "MRL músculo bovino."},
            "oxytetracycline": {"limit": "0.1 mg/kg", "status": "CONFORME", "regulation": "Reg. (CE) 37/2010", "note": "MRL músculo bovino."},
            "estradiol_17b": {"limit": "0.0005 mg/kg", "status": "BANIDO", "regulation": "Diretiva CE 96/22", "note": "Hormônio banido na UE para promoção de crescimento."},
        },
        "tariff_info": {"eu_tariff": "12.8% + €3.04/100kg", "notes": "Cota TRQ para carne bovina. Quota Hilton 0% para carne de alta qualidade."},
        "alerts": [
            "⚠️ Estabelecimento deve constar na lista positiva MAPA/UE — verificar antes de embarcar",
            "🌡️ Cadeia fria ininterrupta ≤ -18°C obrigatória",
            "🚫 Uso de hormônios de crescimento veda exportação à UE",
        ],
        "risk_factors": {
            "documentation": {"score": 75, "level": "MEDIUM"},
            "regulatory": {"score": 80, "level": "MEDIUM"},
            "logistics": {"score": 85, "level": "LOW"},
            "market_access": {"score": 80, "level": "MEDIUM"},
        },
    },

    "frango_congelado": {
        "ncm_code": "0207.14.00",
        "product_name": "Carne de Frango Congelada (pedaços e miudezas)",
        "product_name_it": "Pollo Congelato (pezzi e frattaglie)",
        "product_name_en": "Frozen Chicken (cuts and offal)",
        "category": "Carnes e Proteínas Animais",
        "risk_score": 82,
        "risk_level": "LOW",
        "status": "ZOI APPROVED",
        "trade_route": {"origin": "BR", "destination": "IT", "origin_name": "Brasil", "destination_name": "Itália"},
        "certificates_required": [
            {"name": "Certificado Sanitário Internacional (CSI)", "issuer": "MAPA/SIF", "mandatory": True},
            {"name": "Certificado de Habilitação do Estabelecimento", "issuer": "MAPA", "mandatory": True},
            {"name": "Certificado de Origem", "issuer": "Câmara de Comércio", "mandatory": True},
            {"name": "Laudo de Newcastle e Influenza Aviária", "issuer": "MAPA/SDA", "mandatory": True},
            {"name": "Análise de Salmonella e Campylobacter", "issuer": "Laboratório acreditado", "mandatory": True},
        ],
        "eu_regulations": [
            {"code": "Reg. (CE) 178/2002", "title": "Segurança alimentar geral", "status": "active"},
            {"code": "Reg. (CE) 853/2004", "title": "Higiene para produtos de origem animal", "status": "active"},
            {"code": "Reg. (CE) 2160/2003", "title": "Controlo da Salmonella e zoonoses", "status": "active"},
            {"code": "Reg. (CE) 396/2005", "title": "LMR de pesticidas", "status": "active"},
            {"code": "Reg. (UE) 2019/1793", "title": "Controles reforçados na entrada de alimentos de países terceiros", "status": "active"},
        ],
        "brazilian_requirements": [
            "Estabelecimento habilitado pelo MAPA para exportação à UE",
            "SIF ativo",
            "APPCC/HACCP implementado",
            "Vigilância obrigatória para Influenza Aviária e Newcastle",
            "Cadeia fria ≤ -18°C",
            "Plano de controle de Salmonella aprovado",
        ],
        "max_residue_limits": {
            "enrofloxacin": {"limit": "0.1 mg/kg", "status": "CONFORME", "regulation": "Reg. (CE) 396/2005", "note": "MRL músculo de aves."},
            "chloramphenicol": {"limit": "0.0003 mg/kg", "status": "BANIDO", "regulation": "Reg. (CE) 1950/2006", "note": "Antibiótico banido na UE — tolerância zero."},
            "nitrofurans": {"limit": "0.001 mg/kg", "status": "BANIDO", "regulation": "Reg. (CE) 1950/2006", "note": "Banido na UE — cancerígeno."},
        },
        "tariff_info": {"eu_tariff": "6.4-18.7%", "notes": "Varia por corte. Coxa/sobrecoxa: 15.4%. Peito: 26.2%. Cotas TRQ disponíveis."},
        "alerts": [
            "⚠️ Sujeito a controles reforçados na entrada UE — Reg. 2019/1793",
            "🦠 Análise de Salmonella obrigatória em cada lote exportado",
            "🚫 Chloramphenicol e nitrofuranos: tolerância zero na UE",
        ],
        "risk_factors": {
            "documentation": {"score": 80, "level": "MEDIUM"},
            "regulatory": {"score": 85, "level": "LOW"},
            "logistics": {"score": 85, "level": "LOW"},
            "market_access": {"score": 80, "level": "MEDIUM"},
        },
    },

    "camaro_congelado": {
        "ncm_code": "0306.17.00",
        "product_name": "Camarão Congelado (sem casca ou com casca)",
        "product_name_it": "Gamberetti Congelati",
        "product_name_en": "Frozen Shrimp",
        "category": "Frutos do Mar e Pescados",
        "risk_score": 72,
        "risk_level": "MEDIUM",
        "status": "REQUIRES ATTENTION",
        "trade_route": {"origin": "BR", "destination": "IT", "origin_name": "Brasil", "destination_name": "Itália"},
        "certificates_required": [
            {"name": "Certificado Sanitário para Produtos da Pesca", "issuer": "MAPA/SIF", "mandatory": True},
            {"name": "Certificado de Habilitação do Estabelecimento", "issuer": "MAPA", "mandatory": True},
            {"name": "Certificado de Origem", "issuer": "Câmara de Comércio", "mandatory": True},
            {"name": "Análise microbiológica e de resíduos", "issuer": "Laboratório acreditado", "mandatory": True},
        ],
        "eu_regulations": [
            {"code": "Reg. (CE) 178/2002", "title": "Segurança alimentar geral", "status": "active"},
            {"code": "Reg. (CE) 853/2004", "title": "Higiene para produtos de origem animal", "status": "active"},
            {"code": "Reg. (CE) 854/2004", "title": "Controlo oficial de produtos de origem animal", "status": "active"},
            {"code": "Reg. (UE) 2019/1793", "title": "Controles reforçados — lista de países/produtos", "status": "active"},
            {"code": "Reg. (CE) 396/2005", "title": "LMR de pesticidas e medicamentos veterinários", "status": "active"},
        ],
        "brazilian_requirements": [
            "Estabelecimento habilitado MAPA para exportação à UE",
            "SIF ativo",
            "APPCC/HACCP implementado",
            "Plano de controle de resíduos veterinários (PNCRC)",
            "Cadeia fria ≤ -18°C",
        ],
        "max_residue_limits": {
            "oxytetracycline": {"limit": "0.1 mg/kg", "status": "CONFORME", "regulation": "Reg. (CE) 396/2005", "note": "MRL crustáceos."},
            "chloramphenicol": {"limit": "0.0003 mg/kg", "status": "BANIDO", "regulation": "Reg. (CE) 1950/2006", "note": "Tolerância zero. Causa frequentes rejeições RASFF em camarão brasileiro."},
            "nitrofurans": {"limit": "0.001 mg/kg", "status": "BANIDO", "regulation": "Reg. (CE) 1950/2006", "note": "Banido. Detectado frequentemente em RASFF — monitorar fornecedores."},
        },
        "tariff_info": {"eu_tariff": "12%", "notes": "Camarão congelado sem casca. Possível redução via GSP."},
        "alerts": [
            "🔴 Camarão brasileiro figura com frequência no RASFF por Chloramphenicol — exigir laudos de todos os lotes",
            "⚠️ Sujeito a controles reforçados na fronteira UE — Reg. 2019/1793",
            "🌡️ Cadeia fria ≤ -18°C obrigatória durante todo o transporte",
        ],
        "risk_factors": {
            "documentation": {"score": 75, "level": "MEDIUM"},
            "regulatory": {"score": 65, "level": "HIGH"},
            "logistics": {"score": 80, "level": "MEDIUM"},
            "market_access": {"score": 75, "level": "MEDIUM"},
        },
    },

    "suco_laranja_congelado": {
        "ncm_code": "2009.12.00",
        "product_name": "Suco de Laranja Congelado (FCOJ — não fermentado)",
        "product_name_it": "Succo di Arancia Congelato (FCOJ)",
        "product_name_en": "Frozen Concentrated Orange Juice (FCOJ)",
        "category": "Sucos e Polpas",
        "risk_score": 90,
        "risk_level": "LOW",
        "status": "ZOI APPROVED",
        "trade_route": {"origin": "BR", "destination": "IT", "origin_name": "Brasil", "destination_name": "Itália"},
        "certificates_required": [
            {"name": "Certificado Sanitário", "issuer": "MAPA/ANVISA", "mandatory": True},
            {"name": "Certificado de Origem", "issuer": "Câmara de Comércio", "mandatory": True},
            {"name": "Análise de resíduos de pesticidas", "issuer": "Laboratório acreditado", "mandatory": True},
            {"name": "Certificado de Concentração Brix", "issuer": "Laboratório acreditado", "mandatory": True},
        ],
        "eu_regulations": [
            {"code": "Reg. (CE) 178/2002", "title": "Segurança alimentar geral", "status": "active"},
            {"code": "Diretiva 2001/112/CE", "title": "Sucos de fruta e produtos similares", "status": "active"},
            {"code": "Reg. (CE) 396/2005", "title": "LMR de pesticidas", "status": "active"},
            {"code": "Reg. (UE) 1169/2011", "title": "Rotulagem de alimentos", "status": "active"},
        ],
        "brazilian_requirements": [
            "Registro no MAPA como exportador de sucos",
            "Análise de resíduos conforme PNCRC",
            "Brix mínimo 11.8° (FCOJ concentrado)",
            "Armazenagem e transporte ≤ -18°C",
        ],
        "max_residue_limits": {
            "imazalil": {"limit": "0.05 mg/kg", "status": "CONFORME", "regulation": "Reg. (CE) 396/2005", "note": "Fungicida pós-colheita permitido em sucos."},
            "carbendazim": {"limit": "0.01 mg/kg", "status": "BANIDO", "regulation": "Reg. (CE) 396/2005", "note": "Banido na UE — frequentemente detectado em FCOJ brasileiro no RASFF."},
            "thiabendazole": {"limit": "0.05 mg/kg", "status": "CONFORME", "regulation": "Reg. (CE) 396/2005", "note": "MRL para sucos de frutas cítricas."},
        },
        "tariff_info": {"eu_tariff": "7.9% + €20.6/100kg", "notes": "Brasil maior exportador mundial de FCOJ — alta demanda italiana."},
        "alerts": [
            "⚠️ Carbendazim frequentemente detectado em FCOJ no RASFF — exigir laudo específico",
            "📋 Exige declaração de conformidade com Diretiva 2001/112/CE",
        ],
        "risk_factors": {
            "documentation": {"score": 90, "level": "LOW"},
            "regulatory": {"score": 85, "level": "LOW"},
            "logistics": {"score": 95, "level": "LOW"},
            "market_access": {"score": 95, "level": "LOW"},
        },
    },

    "polpa_maracuja": {
        "ncm_code": "2008.99.00",
        "product_name": "Polpa de Maracujá Congelada",
        "product_name_it": "Polpa di Frutto della Passione Congelata",
        "product_name_en": "Frozen Passion Fruit Pulp",
        "category": "Frutas Tropicais",
        "risk_score": 88,
        "risk_level": "LOW",
        "status": "ZOI APPROVED",
        "trade_route": {"origin": "BR", "destination": "IT", "origin_name": "Brasil", "destination_name": "Itália"},
        "certificates_required": [
            {"name": "Certificado Fitossanitário", "issuer": "MAPA", "mandatory": True},
            {"name": "Certificado Sanitário", "issuer": "ANVISA/MAPA", "mandatory": True},
            {"name": "Certificado de Origem", "issuer": "Câmara de Comércio", "mandatory": True},
            {"name": "Análise de Resíduos", "issuer": "Laboratório acreditado", "mandatory": True},
        ],
        "eu_regulations": [
            {"code": "Reg. (CE) 178/2002", "title": "Segurança alimentar geral", "status": "active"},
            {"code": "Reg. (CE) 396/2005", "title": "LMR de pesticidas", "status": "active"},
            {"code": "Reg. (UE) 1169/2011", "title": "Rotulagem de alimentos", "status": "active"},
            {"code": "Reg. (CE) 852/2004", "title": "Higiene dos gêneros alimentícios", "status": "active"},
        ],
        "brazilian_requirements": [
            "Registro MAPA/SIF",
            "BPF/GMP implementado",
            "Cadeia fria -18°C",
            "APPCC/HACCP",
        ],
        "max_residue_limits": {
            "imidacloprid": {"limit": "0.01 mg/kg", "status": "BANIDO", "regulation": "Reg. Impl. (UE) 2018/783", "note": "Banido na UE desde 2018."},
            "thiamethoxam": {"limit": "0.01 mg/kg", "status": "BANIDO", "regulation": "Reg. Impl. (UE) 2018/785", "note": "Banido na UE desde 2018."},
            "lambda_cyhalothrin": {"limit": "0.05 mg/kg", "status": "CONFORME", "regulation": "Reg. (CE) 396/2005", "note": "MRL para frutas tropicais."},
        },
        "tariff_info": {"eu_tariff": "20% (preparações de fruta)", "notes": "NCM 2008 tem tarifa mais alta que polpa pura. Avaliar classificação correta com despachante."},
        "alerts": [
            "📌 Confirmar NCM com despachante: polpa pura s/ açúcar → 0811.90.90 (tarifa menor)",
            "🌡️ Manter cadeia fria -18°C sem interrupção",
        ],
        "risk_factors": {
            "documentation": {"score": 85, "level": "LOW"},
            "regulatory": {"score": 85, "level": "LOW"},
            "logistics": {"score": 80, "level": "MEDIUM"},
            "market_access": {"score": 90, "level": "LOW"},
        },
    },

    "manga_fresca": {
        "ncm_code": "0804.50.00",
        "product_name": "Manga Fresca (Tommy Atkins, Kent, Palmer)",
        "product_name_it": "Mango Fresco",
        "product_name_en": "Fresh Mango",
        "category": "Frutas Tropicais",
        "risk_score": 83,
        "risk_level": "LOW",
        "status": "ZOI APPROVED",
        "trade_route": {"origin": "BR", "destination": "IT", "origin_name": "Brasil", "destination_name": "Itália"},
        "certificates_required": [
            {"name": "Certificado Fitossanitário", "issuer": "MAPA/SDA", "mandatory": True},
            {"name": "Certificado de Origem", "issuer": "Câmara de Comércio", "mandatory": True},
            {"name": "Análise de Resíduos de Pesticidas", "issuer": "Laboratório acreditado", "mandatory": True},
            {"name": "Tratamento Quarentenário (se exigido)", "issuer": "MAPA", "mandatory": False},
        ],
        "eu_regulations": [
            {"code": "Reg. (CE) 178/2002", "title": "Segurança alimentar geral", "status": "active"},
            {"code": "Reg. (CE) 396/2005", "title": "LMR de pesticidas", "status": "active"},
            {"code": "Reg. (UE) 2019/1793", "title": "Controles reforçados na entrada de alimentos de países terceiros", "status": "active"},
            {"code": "Reg. (CE) 1148/2001", "title": "Normas de qualidade para frutas e hortícolas frescos", "status": "active"},
        ],
        "brazilian_requirements": [
            "Certificado fitossanitário emitido pelo SDA/MAPA via ePhyto",
            "Pomar registrado no MAPA",
            "Análise de resíduos — PNCRC",
            "Embalagem com rastreabilidade (produtor, lote, data)",
        ],
        "max_residue_limits": {
            "imidacloprid": {"limit": "0.01 mg/kg", "status": "BANIDO", "regulation": "Reg. Impl. (UE) 2018/783", "note": "Banido na UE — não usar no pomar."},
            "carbendazim": {"limit": "0.01 mg/kg", "status": "BANIDO", "regulation": "Reg. (CE) 396/2005", "note": "Banido na UE — frequente no RASFF para manga brasileira."},
            "prochloraz": {"limit": "0.05 mg/kg", "status": "CONFORME", "regulation": "Reg. (CE) 396/2005", "note": "Fungicida pós-colheita — respeitar carência."},
            "azoxystrobin": {"limit": "0.2 mg/kg", "status": "CONFORME", "regulation": "Reg. (CE) 396/2005", "note": "MRL para manga."},
        },
        "tariff_info": {"eu_tariff": "0% (Jun-Jul) / 3.2% (restante do ano)", "notes": "Tarifa sazonal favorável ao Brasil — safra Petrolina/Juazeiro alinhada."},
        "alerts": [
            "⚠️ Manga brasileira frequentemente notificada no RASFF por Carbendazim — laudo obrigatório",
            "📅 Atenção à sazonalidade tarifária: embarque Jun-Jul tem tarifa zero",
        ],
        "risk_factors": {
            "documentation": {"score": 85, "level": "LOW"},
            "regulatory": {"score": 75, "level": "MEDIUM"},
            "logistics": {"score": 80, "level": "MEDIUM"},
            "market_access": {"score": 90, "level": "LOW"},
        },
    },

    "file_tilapia_congelado": {
        "ncm_code": "0304.62.00",
        "product_name": "Filé de Tilápia Congelado",
        "product_name_it": "Filetto di Tilapia Congelato",
        "product_name_en": "Frozen Tilapia Fillet",
        "category": "Frutos do Mar e Pescados",
        "risk_score": 86,
        "risk_level": "LOW",
        "status": "ZOI APPROVED",
        "trade_route": {"origin": "BR", "destination": "IT", "origin_name": "Brasil", "destination_name": "Itália"},
        "certificates_required": [
            {"name": "Certificado Sanitário para Produtos da Pesca", "issuer": "MAPA/SIF", "mandatory": True},
            {"name": "Certificado de Habilitação do Estabelecimento", "issuer": "MAPA", "mandatory": True},
            {"name": "Certificado de Origem", "issuer": "Câmara de Comércio", "mandatory": True},
            {"name": "Análise microbiológica e de resíduos veterinários", "issuer": "Laboratório acreditado", "mandatory": True},
        ],
        "eu_regulations": [
            {"code": "Reg. (CE) 178/2002", "title": "Segurança alimentar geral", "status": "active"},
            {"code": "Reg. (CE) 853/2004", "title": "Higiene para produtos de origem animal", "status": "active"},
            {"code": "Reg. (CE) 854/2004", "title": "Controlo oficial de produtos de origem animal", "status": "active"},
            {"code": "Reg. (CE) 396/2005", "title": "LMR de medicamentos veterinários", "status": "active"},
        ],
        "brazilian_requirements": [
            "Estabelecimento habilitado MAPA para exportação à UE",
            "SIF ativo",
            "APPCC/HACCP",
            "Cadeia fria ≤ -18°C",
            "Plano de controle de resíduos veterinários (PNCRC/Aquicultura)",
        ],
        "max_residue_limits": {
            "oxytetracycline": {"limit": "0.1 mg/kg", "status": "CONFORME", "regulation": "Reg. (CE) 396/2005", "note": "MRL músculo de peixe."},
            "malachite_green": {"limit": "0.002 mg/kg", "status": "BANIDO", "regulation": "Reg. (CE) 1950/2006", "note": "Verde malaquita — banido. Detectado frequentemente em peixes de aquicultura."},
            "chloramphenicol": {"limit": "0.0003 mg/kg", "status": "BANIDO", "regulation": "Reg. (CE) 1950/2006", "note": "Tolerância zero na UE."},
        },
        "tariff_info": {"eu_tariff": "9%", "notes": "Brasil maior produtor de tilápia do mundo — forte competitividade no mercado italiano."},
        "alerts": [
            "🚫 Verde malaquita (malachite green) banido na UE — controlar tratamentos no viveiro",
            "🌡️ Cadeia fria ≤ -18°C durante todo transporte e armazenagem",
        ],
        "risk_factors": {
            "documentation": {"score": 85, "level": "LOW"},
            "regulatory": {"score": 80, "level": "MEDIUM"},
            "logistics": {"score": 90, "level": "LOW"},
            "market_access": {"score": 90, "level": "LOW"},
        },
    },

    "carne_suina_congelada": {
        "ncm_code": "0203.29.00",
        "product_name": "Carne Suína Congelada (desossada)",
        "product_name_it": "Carne di Maiale Congelata (disossata)",
        "product_name_en": "Frozen Boneless Pork",
        "category": "Carnes e Proteínas Animais",
        "risk_score": 78,
        "risk_level": "MEDIUM",
        "status": "ZOI APPROVED",
        "trade_route": {"origin": "BR", "destination": "IT", "origin_name": "Brasil", "destination_name": "Itália"},
        "certificates_required": [
            {"name": "Certificado Sanitário Internacional (CSI)", "issuer": "MAPA/SIF", "mandatory": True},
            {"name": "Certificado de Habilitação do Estabelecimento", "issuer": "MAPA", "mandatory": True},
            {"name": "Certificado de Febre Aftosa (zona livre)", "issuer": "MAPA/PANAFTOSA", "mandatory": True},
            {"name": "Certificado de Origem", "issuer": "Câmara de Comércio", "mandatory": True},
            {"name": "Análise de ractopamina (exigência UE)", "issuer": "Laboratório acreditado", "mandatory": True},
        ],
        "eu_regulations": [
            {"code": "Reg. (CE) 178/2002", "title": "Segurança alimentar geral", "status": "active"},
            {"code": "Reg. (CE) 853/2004", "title": "Higiene para produtos de origem animal", "status": "active"},
            {"code": "Diretiva CE 96/22", "title": "Proibição de substâncias hormonais e agonistas beta", "status": "active"},
            {"code": "Reg. (CE) 396/2005", "title": "LMR de medicamentos veterinários", "status": "active"},
        ],
        "brazilian_requirements": [
            "Estabelecimento habilitado MAPA para exportação à UE",
            "SIF ativo",
            "Zona livre de Febre Aftosa sem vacinação (ou com vacinação, conforme acordo)",
            "APPCC/HACCP",
            "Cadeia fria ≤ -18°C",
            "Proibição de ractopamina — não utilizada para exportação UE",
        ],
        "max_residue_limits": {
            "ractopamine": {"limit": "0.000 mg/kg", "status": "BANIDO", "regulation": "Diretiva CE 96/22", "note": "Beta-agonista banido na UE. Brasil mantém linha produtiva separada para UE."},
            "oxytetracycline": {"limit": "0.1 mg/kg", "status": "CONFORME", "regulation": "Reg. (CE) 396/2005", "note": "MRL músculo suíno."},
        },
        "tariff_info": {"eu_tariff": "12.9% + €46.7/100kg", "notes": "Cotas TRQ com tarifa reduzida disponíveis. Verificar disponibilidade de cota."},
        "alerts": [
            "🚫 Ractopamina proibida na UE — confirmar rastreabilidade de toda a cadeia de produção",
            "🐷 Exige certificado específico de zona livre de Febre Aftosa",
        ],
        "risk_factors": {
            "documentation": {"score": 75, "level": "MEDIUM"},
            "regulatory": {"score": 75, "level": "MEDIUM"},
            "logistics": {"score": 85, "level": "LOW"},
            "market_access": {"score": 78, "level": "MEDIUM"},
        },
    },

    "melao_fresco": {
        "ncm_code": "0807.19.00",
        "product_name": "Melão Fresco (Cantaloupe, Honeydew, Orange Flesh)",
        "product_name_it": "Melone Fresco",
        "product_name_en": "Fresh Melon",
        "category": "Frutas Tropicais",
        "risk_score": 85,
        "risk_level": "LOW",
        "status": "ZOI APPROVED",
        "trade_route": {"origin": "BR", "destination": "IT", "origin_name": "Brasil", "destination_name": "Itália"},
        "certificates_required": [
            {"name": "Certificado Fitossanitário", "issuer": "MAPA/SDA", "mandatory": True},
            {"name": "Certificado de Origem", "issuer": "Câmara de Comércio", "mandatory": True},
            {"name": "Análise de Resíduos de Pesticidas", "issuer": "Laboratório acreditado", "mandatory": True},
        ],
        "eu_regulations": [
            {"code": "Reg. (CE) 178/2002", "title": "Segurança alimentar geral", "status": "active"},
            {"code": "Reg. (CE) 396/2005", "title": "LMR de pesticidas", "status": "active"},
            {"code": "Reg. (UE) 2019/1793", "title": "Controles reforçados na entrada", "status": "active"},
            {"code": "Reg. (CE) 1148/2001", "title": "Normas de qualidade para frutas e hortícolas", "status": "active"},
        ],
        "brazilian_requirements": [
            "Pomar/área produtora registrada no MAPA",
            "Certificado fitossanitário via ePhyto",
            "Rastreabilidade de lote",
            "Análise de resíduos (PNCRC)",
        ],
        "max_residue_limits": {
            "imidacloprid": {"limit": "0.01 mg/kg", "status": "BANIDO", "regulation": "Reg. Impl. (UE) 2018/783", "note": "Banido. Melão do RN/CE frequentemente analisado no RASFF."},
            "chlorpyrifos": {"limit": "0.01 mg/kg", "status": "BANIDO", "regulation": "Reg. Impl. (UE) 2020/18", "note": "Banido na UE desde 2020."},
            "metalaxyl": {"limit": "0.05 mg/kg", "status": "CONFORME", "regulation": "Reg. (CE) 396/2005", "note": "MRL para cucurbitáceas."},
        },
        "tariff_info": {"eu_tariff": "7.7% (jan-mar) / 0% (abr-out)", "notes": "Tarifa zero na janela principal da safra brasileira (out-mar) = vantagem competitiva."},
        "alerts": [
            "📅 Safra do Vale do São Francisco (RN/CE): nov-mar — coincide com janela de tarifa zero UE",
            "⚠️ Chlorpyrifos banido na UE desde 2020 — retirar do protocolo de defensivos",
        ],
        "risk_factors": {
            "documentation": {"score": 88, "level": "LOW"},
            "regulatory": {"score": 82, "level": "LOW"},
            "logistics": {"score": 85, "level": "LOW"},
            "market_access": {"score": 90, "level": "LOW"},
        },
    },

    "uva_fresca_exportacao": {
        "ncm_code": "0806.10.00",
        "product_name": "Uva Fresca de Mesa (Brasil → Itália)",
        "product_name_it": "Uva da Tavola Fresca (Brasile → Italia)",
        "product_name_en": "Fresh Table Grapes (Brazil → Italy)",
        "category": "Frutas Tropicais",
        "risk_score": 80,
        "risk_level": "MEDIUM",
        "status": "ZOI APPROVED",
        "trade_route": {"origin": "BR", "destination": "IT", "origin_name": "Brasil", "destination_name": "Itália"},
        "certificates_required": [
            {"name": "Certificado Fitossanitário", "issuer": "MAPA/SDA", "mandatory": True},
            {"name": "Certificado de Origem", "issuer": "Câmara de Comércio", "mandatory": True},
            {"name": "Análise de Resíduos de Pesticidas", "issuer": "Laboratório acreditado", "mandatory": True},
        ],
        "eu_regulations": [
            {"code": "Reg. (CE) 178/2002", "title": "Segurança alimentar geral", "status": "active"},
            {"code": "Reg. (CE) 396/2005", "title": "LMR de pesticidas", "status": "active"},
            {"code": "Reg. (UE) 2019/1793", "title": "Controles reforçados na entrada", "status": "active"},
        ],
        "brazilian_requirements": [
            "Vinhedo/parreiral registrado no MAPA",
            "Certificado fitossanitário emitido via ePhyto",
            "Análise de Botrytis e Cryptosporella (pragas quarentenárias UE)",
            "Análise de resíduos por lote",
        ],
        "max_residue_limits": {
            "imidacloprid": {"limit": "0.01 mg/kg", "status": "BANIDO", "regulation": "Reg. Impl. (UE) 2018/783", "note": "Banido na UE."},
            "iprodione": {"limit": "0.01 mg/kg", "status": "BANIDO", "regulation": "Reg. (CE) 396/2005", "note": "Aprovação expirada na UE — MRL reduzido ao mínimo."},
            "azoxystrobin": {"limit": "2.0 mg/kg", "status": "CONFORME", "regulation": "Reg. (CE) 396/2005", "note": "MRL uva de mesa."},
            "fludioxonil": {"limit": "2.0 mg/kg", "status": "CONFORME", "regulation": "Reg. (CE) 396/2005", "note": "MRL uva de mesa."},
        },
        "tariff_info": {"eu_tariff": "0% (nov-jan) / 8-11.5% (restante)", "notes": "Safra Petrolina/Juazeiro (ago-mar) coincide com janela favorável."},
        "alerts": [
            "⚠️ Iprodione: MRL reduzido para 0.01 mg/kg na UE — substituir fungicida no protocolo",
            "🍇 Sujeito a controles reforçados por Reg. 2019/1793",
        ],
        "risk_factors": {
            "documentation": {"score": 82, "level": "LOW"},
            "regulatory": {"score": 75, "level": "MEDIUM"},
            "logistics": {"score": 83, "level": "LOW"},
            "market_access": {"score": 85, "level": "LOW"},
        },
    },

    # =========================================================================
    # ITÁLIA → BRASIL — FRESCOS E CONGELADOS
    # Fonte: Italian Trade Agency (ICE), Nomisma/Agrifood Monitor 2023-2024
    # Top produtos: maçã (+115.5%), tomate (+50.4%), pasta (+32.8%), mozzarella
    # =========================================================================

    "maca_italiana": {
        "ncm_code": "0808.10.10",
        "product_name": "Maçã Fresca Italiana (Golden, Fuji, Gala — importação)",
        "product_name_it": "Mele Fresche Italiane (Alto Adige, Trentino)",
        "product_name_en": "Fresh Italian Apples",
        "category": "Frutas Temperadas",
        "risk_score": 95,
        "risk_level": "LOW",
        "status": "ZOI APPROVED",
        "trade_route": {"origin": "IT", "destination": "BR", "origin_name": "Itália", "destination_name": "Brasil"},
        "certificates_required": [
            {"name": "Certificado Fitossanitário (país exportador)", "issuer": "MIPAAF / Servizio Fitosanitario", "mandatory": True},
            {"name": "Licença de Importação (LPCO) — MAPA", "issuer": "MAPA Brasil", "mandatory": True},
            {"name": "Certificado de Origem", "issuer": "Câmara de Comércio Italiana", "mandatory": True},
            {"name": "Análise de resíduos", "issuer": "Laboratório acreditado", "mandatory": True},
        ],
        "eu_regulations": [
            {"code": "Reg. (CE) 396/2005", "title": "LMR de pesticidas na origem", "status": "active"},
            {"code": "Reg. (UE) 2019/1793", "title": "Controles na saída de alimentos UE", "status": "active"},
        ],
        "brazilian_requirements": [
            "LPCO obrigatória — aprovação pelo MAPA antes do embarque",
            "Análise de pragas quarentenárias brasileiras (Cydia pomonella, Erwinia amylovora)",
            "Embalagem aprovada e rastreável",
            "Registro do importador no MAPA",
            "Inspeção na chegada pelo SDA/VIGIAGRO",
        ],
        "max_residue_limits": {
            "diphenylamine": {"limit": "0.1 mg/kg", "status": "CONFORME", "regulation": "Reg. (CE) 396/2005", "note": "Tratamento pós-colheita para maçã — limite aplicado."},
            "chlorpyrifos": {"limit": "0.01 mg/kg", "status": "BANIDO", "regulation": "Reg. Impl. (UE) 2020/18", "note": "Banido na UE e com MRL mínimo — maçã italiana não utiliza."},
        },
        "tariff_info": {"eu_tariff": "II Brasil: 10% TEC", "notes": "Maçã italiana cresceu +115.5% nas exportações à Brasil em 2023. Alta demanda nos meses de escassez da produção nacional."},
        "alerts": [
            "📋 LPCO do MAPA obrigatória — solicitar com antecedência mínima de 30 dias",
            "🍎 Maior crescimento de produto italiano para o Brasil em 2023 (+115.5%) — grande oportunidade",
        ],
        "risk_factors": {
            "documentation": {"score": 90, "level": "LOW"},
            "regulatory": {"score": 92, "level": "LOW"},
            "logistics": {"score": 88, "level": "LOW"},
            "market_access": {"score": 95, "level": "LOW"},
        },
    },

    "mozzarella_fresca": {
        "ncm_code": "0406.10.10",
        "product_name": "Mozzarella Fresca Italiana (importação)",
        "product_name_it": "Mozzarella Fresca (Mozzarella di Bufala Campana DOP)",
        "product_name_en": "Fresh Italian Mozzarella",
        "category": "Laticínios e Derivados",
        "risk_score": 88,
        "risk_level": "LOW",
        "status": "ZOI APPROVED",
        "trade_route": {"origin": "IT", "destination": "BR", "origin_name": "Itália", "destination_name": "Brasil"},
        "certificates_required": [
            {"name": "Certificado Sanitário (laticínio)", "issuer": "ASL (Azienda Sanitaria Locale) + MIPAAF", "mandatory": True},
            {"name": "Certificado de Origem DOP (se aplicável)", "issuer": "Consorzio Mozzarella di Bufala Campana", "mandatory": False},
            {"name": "Licença de Importação (LPCO) — MAPA", "issuer": "MAPA Brasil", "mandatory": True},
            {"name": "Certificado de Origem", "issuer": "Câmara de Comércio Italiana", "mandatory": True},
        ],
        "eu_regulations": [
            {"code": "Reg. (CE) 853/2004", "title": "Higiene para produtos de origem animal na origem", "status": "active"},
            {"code": "Reg. (UE) 1151/2012", "title": "Regimes de qualidade para produtos DOP/IGP", "status": "active"},
        ],
        "brazilian_requirements": [
            "LPCO obrigatória pelo MAPA — laticínios estrangeiros",
            "Estabelecimento italiano deve estar habilitado para exportação ao Brasil (lista MAPA)",
            "Registro do produto no MAPA como produto de origem animal importado",
            "Cadeia fria: 4-8°C (fresca) ou congelada para transporte",
            "Rotulagem em português obrigatória (RDC 429/2020 ANVISA)",
            "Registro ANVISA para comercialização no Brasil",
        ],
        "max_residue_limits": {
            "aflatoxin_m1": {"limit": "0.05 µg/kg", "status": "CONFORME", "regulation": "Reg. (CE) 1881/2006", "note": "LMR leite/laticínios — UE e Brasil alinhados."},
        },
        "tariff_info": {"eu_tariff": "II Brasil: 16% TEC para queijos", "notes": "Importação italiana de laticínios crescente no Brasil. Mozzarella di Bufala DOP tem alto valor agregado."},
        "alerts": [
            "📋 Estabelecimento italiano deve estar na lista positiva MAPA para exportação ao Brasil",
            "🏷️ Rotulagem em português obrigatória antes de comercializar",
            "❄️ Mozzarella fresca: cadeia fria 4-8°C; shelf life curto — logística reefer essencial",
        ],
        "risk_factors": {
            "documentation": {"score": 85, "level": "LOW"},
            "regulatory": {"score": 88, "level": "LOW"},
            "logistics": {"score": 75, "level": "MEDIUM"},
            "market_access": {"score": 88, "level": "LOW"},
        },
    },

    "parmigiano_reggiano": {
        "ncm_code": "0406.90.20",
        "product_name": "Parmigiano Reggiano / Grana Padano (importação)",
        "product_name_it": "Parmigiano Reggiano DOP / Grana Padano DOP",
        "product_name_en": "Parmigiano Reggiano / Grana Padano (import)",
        "category": "Laticínios e Derivados",
        "risk_score": 92,
        "risk_level": "LOW",
        "status": "ZOI APPROVED",
        "trade_route": {"origin": "IT", "destination": "BR", "origin_name": "Itália", "destination_name": "Brasil"},
        "certificates_required": [
            {"name": "Certificado Sanitário", "issuer": "ASL + MIPAAF", "mandatory": True},
            {"name": "Certificado DOP", "issuer": "Consorzio Parmigiano Reggiano / Consorzio Grana Padano", "mandatory": True},
            {"name": "LPCO — MAPA Brasil", "issuer": "MAPA Brasil", "mandatory": True},
            {"name": "Certificado de Origem", "issuer": "Câmara de Comércio Italiana", "mandatory": True},
        ],
        "eu_regulations": [
            {"code": "Reg. (UE) 1151/2012", "title": "Regimes de qualidade para produtos DOP", "status": "active"},
            {"code": "Reg. (CE) 853/2004", "title": "Higiene para produtos de origem animal", "status": "active"},
        ],
        "brazilian_requirements": [
            "LPCO obrigatória pelo MAPA",
            "Estabelecimento italiano habilitado para exportação ao Brasil",
            "Registro do produto no MAPA",
            "Rotulagem em português (RDC 429/2020)",
            "Registro ANVISA para comercialização",
            "Temperatura de transporte: ambiente controlado (8-15°C) para queijo curado",
        ],
        "max_residue_limits": {
            "aflatoxin_m1": {"limit": "0.05 µg/kg", "status": "CONFORME", "regulation": "Reg. (CE) 1881/2006", "note": "LMR leite/laticínios maturados."},
        },
        "tariff_info": {"eu_tariff": "II Brasil: 16% TEC", "notes": "Produto DOP com alto valor agregado. Demanda crescente nos segmentos premium e restauração italiana no Brasil."},
        "alerts": [
            "🏷️ Uso do nome DOP protegido — verificar conformidade de rotulagem",
            "📋 Cadastro no MAPA obrigatório antes do primeiro embarque",
        ],
        "risk_factors": {
            "documentation": {"score": 88, "level": "LOW"},
            "regulatory": {"score": 90, "level": "LOW"},
            "logistics": {"score": 90, "level": "LOW"},
            "market_access": {"score": 92, "level": "LOW"},
        },
    },

    "prosciutto_di_parma": {
        "ncm_code": "1601.00.90",
        "product_name": "Prosciutto di Parma / San Daniele (importação)",
        "product_name_it": "Prosciutto di Parma DOP / San Daniele DOP",
        "product_name_en": "Prosciutto di Parma / San Daniele (import)",
        "category": "Carnes e Frios Curados",
        "risk_score": 88,
        "risk_level": "LOW",
        "status": "ZOI APPROVED",
        "trade_route": {"origin": "IT", "destination": "BR", "origin_name": "Itália", "destination_name": "Brasil"},
        "certificates_required": [
            {"name": "Certificado Sanitário Internacional", "issuer": "MIPAAF / ASL", "mandatory": True},
            {"name": "Certificado DOP", "issuer": "Consorzio Prosciutto di Parma / San Daniele", "mandatory": True},
            {"name": "LPCO — MAPA Brasil", "issuer": "MAPA Brasil", "mandatory": True},
            {"name": "Certificado de Origem", "issuer": "Câmara de Comércio Italiana", "mandatory": True},
        ],
        "eu_regulations": [
            {"code": "Reg. (UE) 1151/2012", "title": "Regimes de qualidade DOP/IGP", "status": "active"},
            {"code": "Reg. (CE) 853/2004", "title": "Higiene para produtos de origem animal", "status": "active"},
            {"code": "Reg. (CE) 1333/2008", "title": "Aditivos alimentares — nitratos/nitritos", "status": "active"},
        ],
        "brazilian_requirements": [
            "LPCO obrigatória pelo MAPA",
            "Estabelecimento italiano habilitado para exportação ao Brasil",
            "Rotulagem em português — RDC 429/2020 + RDC 724/2022 (aditivos)",
            "Registro do produto no MAPA",
            "Verificar conformidade de nitratos e nitritos com limites ANVISA",
        ],
        "max_residue_limits": {
            "nitrites": {"limit": "150 mg/kg", "status": "CONFORME", "regulation": "Reg. (CE) 1333/2008", "note": "MRL UE para nitritos em presunto curado. ANVISA: verificar equivalência."},
        },
        "tariff_info": {"eu_tariff": "II Brasil: 10% TEC (embutidos)", "notes": "Itália é o maior fornecedor de presunto curado industrializado ao Brasil. Crescimento contínuo no canal alimentar e importadoras premium."},
        "alerts": [
            "🏷️ Denominação DOP protegida — não pode ser chamado de 'Prosciutto di Parma' sem certificação do Consórcio",
            "⚗️ Verificar limites de nitratos/nitritos com ANVISA antes de comercializar",
        ],
        "risk_factors": {
            "documentation": {"score": 88, "level": "LOW"},
            "regulatory": {"score": 88, "level": "LOW"},
            "logistics": {"score": 92, "level": "LOW"},
            "market_access": {"score": 90, "level": "LOW"},
        },
    },

    "tomate_pelado_italiano": {
        "ncm_code": "2002.10.00",
        "product_name": "Tomate Pelado Italiano em Conserva (importação)",
        "product_name_it": "Pomodori Pelati San Marzano / Pomodoro in Scatola",
        "product_name_en": "Italian Peeled Tomatoes in Cans",
        "category": "Hortícolas e Conservas",
        "risk_score": 95,
        "risk_level": "LOW",
        "status": "ZOI APPROVED",
        "trade_route": {"origin": "IT", "destination": "BR", "origin_name": "Itália", "destination_name": "Brasil"},
        "certificates_required": [
            {"name": "Certificado de Origem", "issuer": "Câmara de Comércio Italiana", "mandatory": True},
            {"name": "Nota Fiscal / Invoice Comercial", "issuer": "Exportador Italiano", "mandatory": True},
            {"name": "Análise laboratorial (optional, exigida pela ANVISA se DI suspensa)", "issuer": "Laboratório acreditado", "mandatory": False},
        ],
        "eu_regulations": [
            {"code": "Reg. (CE) 178/2002", "title": "Segurança alimentar geral", "status": "active"},
            {"code": "Reg. (CE) 1881/2006", "title": "Contaminantes — limites para produtos processados", "status": "active"},
        ],
        "brazilian_requirements": [
            "Registro ANVISA para alimentos processados importados",
            "Rotulagem em português obrigatória (RDC 429/2020)",
            "Verificação de aditivos alimentares conforme IN ANVISA 89/2021",
            "Importação sem LPCO do MAPA (produto processado — competência ANVISA)",
        ],
        "max_residue_limits": {
            "lycopene_natural": {"limit": "N/A", "status": "CONFORME", "regulation": "N/A", "note": "Composto natural do tomate — sem restrição."},
            "tin_inorganic": {"limit": "200 mg/kg", "status": "CONFORME", "regulation": "Reg. (CE) 1881/2006", "note": "Limite para produtos em embalagem metálica."},
        },
        "tariff_info": {"eu_tariff": "II Brasil: 14.4% TEC", "notes": "Tomate italiano (+50.4% exportações ao Brasil em 2023). San Marzano DOP tem alto diferencial de mercado."},
        "alerts": [
            "🏷️ Rotulagem em português obrigatória — importador responsável pela adequação",
            "📋 Registro ANVISA obrigatório antes de comercializar no Brasil",
        ],
        "risk_factors": {
            "documentation": {"score": 92, "level": "LOW"},
            "regulatory": {"score": 92, "level": "LOW"},
            "logistics": {"score": 98, "level": "LOW"},
            "market_access": {"score": 95, "level": "LOW"},
        },
    },

    "trufa_fresca": {
        "ncm_code": "0709.59.90",
        "product_name": "Trufa Fresca / Congelada Italiana (importação)",
        "product_name_it": "Tartufo Fresco / Congelato (Tartufo Nero di Norcia, Tartufo Bianco d'Alba)",
        "product_name_en": "Fresh / Frozen Italian Truffles",
        "category": "Cogumelos e Especiarias Premium",
        "risk_score": 90,
        "risk_level": "LOW",
        "status": "ZOI APPROVED",
        "trade_route": {"origin": "IT", "destination": "BR", "origin_name": "Itália", "destination_name": "Brasil"},
        "certificates_required": [
            {"name": "Certificado Fitossanitário", "issuer": "Servizio Fitosanitario Regionale (Itália)", "mandatory": True},
            {"name": "Certificado de Origem + espécie identificada", "issuer": "Câmara de Comércio Italiana", "mandatory": True},
            {"name": "LPCO — MAPA Brasil (produto vegetal)", "issuer": "MAPA Brasil", "mandatory": True},
            {"name": "Laudo de identificação taxonômica", "issuer": "Laboratório/especialista", "mandatory": True},
        ],
        "eu_regulations": [
            {"code": "Reg. (CE) 178/2002", "title": "Segurança alimentar geral", "status": "active"},
            {"code": "Reg. (CE) 396/2005", "title": "LMR de pesticidas", "status": "active"},
        ],
        "brazilian_requirements": [
            "LPCO do MAPA obrigatória — produto vegetal de origem animal (fungo)",
            "Laudo de identificação da espécie (Tuber melanosporum, T. magnatum, etc.)",
            "Inspeção do VIGIAGRO na chegada",
            "Cadeia fria: 2-4°C (fresca) ou ≤ -18°C (congelada)",
            "Shelf life muito curto para fresca — logística express aérea usualmente necessária",
        ],
        "max_residue_limits": {
            "heavy_metals_generic": {"limit": "< padrão ANVISA", "status": "CONFORME", "regulation": "Reg. (CE) 1881/2006", "note": "Trufas não costumam apresentar contaminantes — produto premium de alto controle."},
        },
        "tariff_info": {"eu_tariff": "II Brasil: 6% TEC (cogumelos/trufas)", "notes": "Mercado de luxo. Tartufo Bianco d'Alba pode atingir €5.000/kg — exportação de altíssimo valor por kg."},
        "alerts": [
            "✈️ Trufa fresca: vida útil 5-10 dias — transporte aéreo expresso essencial",
            "🔬 Laudo de identificação taxonômica obrigatório — risco de adulteração com espécies chinesas",
            "📋 LPCO MAPA: solicitar com antecedência",
        ],
        "risk_factors": {
            "documentation": {"score": 82, "level": "LOW"},
            "regulatory": {"score": 88, "level": "LOW"},
            "logistics": {"score": 65, "level": "HIGH"},
            "market_access": {"score": 92, "level": "LOW"},
        },
    },

    "kiwi_fresco_italiano": {
        "ncm_code": "0810.50.00",
        "product_name": "Kiwi Fresco Italiano (importação)",
        "product_name_it": "Kiwi Fresco (Hayward, Zespri Gold)",
        "product_name_en": "Fresh Italian Kiwifruit",
        "category": "Frutas Temperadas",
        "risk_score": 93,
        "risk_level": "LOW",
        "status": "ZOI APPROVED",
        "trade_route": {"origin": "IT", "destination": "BR", "origin_name": "Itália", "destination_name": "Brasil"},
        "certificates_required": [
            {"name": "Certificado Fitossanitário", "issuer": "Servizio Fitosanitario (Itália)", "mandatory": True},
            {"name": "LPCO — MAPA Brasil", "issuer": "MAPA Brasil", "mandatory": True},
            {"name": "Certificado de Origem", "issuer": "Câmara de Comércio Italiana", "mandatory": True},
        ],
        "eu_regulations": [
            {"code": "Reg. (CE) 396/2005", "title": "LMR de pesticidas", "status": "active"},
            {"code": "Reg. (CE) 178/2002", "title": "Segurança alimentar geral", "status": "active"},
        ],
        "brazilian_requirements": [
            "LPCO MAPA obrigatória",
            "Análise de Pseudomonas syringae pv. actinidiae (Psa — praga quarentenária A1 no Brasil)",
            "Inspeção VIGIAGRO na chegada",
            "Cadeia fria: 0-4°C",
        ],
        "max_residue_limits": {
            "acetamiprid": {"limit": "0.1 mg/kg", "status": "CONFORME", "regulation": "Reg. (CE) 396/2005", "note": "MRL kiwi."},
            "imidacloprid": {"limit": "0.01 mg/kg", "status": "BANIDO", "regulation": "Reg. Impl. (UE) 2018/783", "note": "Banido UE — produtores italianos não utilizam."},
        },
        "tariff_info": {"eu_tariff": "II Brasil: 10% TEC", "notes": "Itália é 2º maior produtor mundial de kiwi. Abastece o Brasil no período de escassez da produção nacional (mai-set)."},
        "alerts": [
            "🔬 Psa (Pseudomonas syringae pv. actinidiae) é praga quarentenária A1 no Brasil — inspeção rigorosa",
            "❄️ Cadeia fria 0-4°C obrigatória para manter qualidade e shelf life",
        ],
        "risk_factors": {
            "documentation": {"score": 90, "level": "LOW"},
            "regulatory": {"score": 90, "level": "LOW"},
            "logistics": {"score": 88, "level": "LOW"},
            "market_access": {"score": 94, "level": "LOW"},
        },
    },

    "pera_fresca_italiana": {
        "ncm_code": "0808.30.00",
        "product_name": "Pera Fresca Italiana (importação)",
        "product_name_it": "Pere Fresche (Abate Fetel, William, Conference)",
        "product_name_en": "Fresh Italian Pears",
        "category": "Frutas Temperadas",
        "risk_score": 93,
        "risk_level": "LOW",
        "status": "ZOI APPROVED",
        "trade_route": {"origin": "IT", "destination": "BR", "origin_name": "Itália", "destination_name": "Brasil"},
        "certificates_required": [
            {"name": "Certificado Fitossanitário", "issuer": "Servizio Fitosanitario (Itália)", "mandatory": True},
            {"name": "LPCO — MAPA Brasil", "issuer": "MAPA Brasil", "mandatory": True},
            {"name": "Certificado de Origem", "issuer": "Câmara de Comércio Italiana", "mandatory": True},
        ],
        "eu_regulations": [
            {"code": "Reg. (CE) 396/2005", "title": "LMR de pesticidas", "status": "active"},
            {"code": "Reg. (CE) 178/2002", "title": "Segurança alimentar geral", "status": "active"},
        ],
        "brazilian_requirements": [
            "LPCO MAPA obrigatória",
            "Análise de pragas quarentenárias (Cydia pomonella, Erwinia amylovora)",
            "Inspeção VIGIAGRO na chegada",
            "Cadeia fria: 0-4°C",
            "Categoria de qualidade conforme normas CEAGESP",
        ],
        "max_residue_limits": {
            "diphenylamine": {"limit": "0.1 mg/kg", "status": "CONFORME", "regulation": "Reg. (CE) 396/2005", "note": "Tratamento pós-colheita anti-escaldo."},
            "captan": {"limit": "3.0 mg/kg", "status": "CONFORME", "regulation": "Reg. (CE) 396/2005", "note": "MRL pera."},
        },
        "tariff_info": {"eu_tariff": "II Brasil: 10% TEC", "notes": "Emilia-Romagna é a principal região produtora. Abate Fetel, William e Conference dominam as importações brasileiras."},
        "alerts": [
            "📋 LPCO MAPA obrigatória — solicitar antes do embarque",
            "🔬 Erwinia amylovora (fogo bacteriano) é praga quarentenária A1 no Brasil",
        ],
        "risk_factors": {
            "documentation": {"score": 90, "level": "LOW"},
            "regulatory": {"score": 90, "level": "LOW"},
            "logistics": {"score": 88, "level": "LOW"},
            "market_access": {"score": 93, "level": "LOW"},
        },
    },
}

SLUG_ALIASES = {
    # Produtos existentes
    "soja": "soja_grao", "soja_graos": "soja_grao", "soybeans": "soja_grao",
    "açaí": "acai", "açai": "acai", "acai_polpa": "acai",
    "coffee": "cafe", "café": "cafe", "cafe_verde": "cafe",
    # Brasil → Itália — novos
    "carne_bovina": "carne_bovina_congelada", "beef": "carne_bovina_congelada", "bovina_congelada": "carne_bovina_congelada",
    "frango": "frango_congelado", "chicken": "frango_congelado", "frango_partes": "frango_congelado",
    "camaro": "camaro_congelado", "camarão": "camaro_congelado", "shrimp": "camaro_congelado",
    "suco_laranja": "suco_laranja_congelado", "fcoj": "suco_laranja_congelado", "orange_juice": "suco_laranja_congelado",
    "maracuja": "polpa_maracuja", "maracujá": "polpa_maracuja", "passion_fruit": "polpa_maracuja",
    "manga": "manga_fresca", "mango": "manga_fresca",
    "tilapia": "file_tilapia_congelado", "tilápia": "file_tilapia_congelado", "file_tilapia": "file_tilapia_congelado",
    "carne_suina": "carne_suina_congelada", "suina": "carne_suina_congelada", "pork": "carne_suina_congelada",
    "melao": "melao_fresco", "melão": "melao_fresco", "melon": "melao_fresco",
    "uva": "uva_fresca_exportacao", "grape": "uva_fresca_exportacao",
    # Itália → Brasil — novos
    "maca": "maca_italiana", "maçã": "maca_italiana", "apple": "maca_italiana", "mele": "maca_italiana",
    "mozzarella": "mozzarella_fresca", "mozarela": "mozzarella_fresca",
    "parmigiano": "parmigiano_reggiano", "grana_padano": "parmigiano_reggiano", "parmesao": "parmigiano_reggiano",
    "prosciutto": "prosciutto_di_parma", "presunto_italiano": "prosciutto_di_parma", "san_daniele": "prosciutto_di_parma",
    "tomate_pelado": "tomate_pelado_italiano", "pelati": "tomate_pelado_italiano",
    "trufa": "trufa_fresca", "tartufo": "trufa_fresca", "truffle": "trufa_fresca",
    "kiwi": "kiwi_fresco_italiano",
    "pera": "pera_fresca_italiana", "pear": "pera_fresca_italiana",
}


def normalize_slug(slug: str) -> str:
    normalized = slug.lower().strip().replace("-", "_").replace(" ", "_")
    return SLUG_ALIASES.get(normalized, normalized)


def make_unknown_product_template(product_name: str) -> Dict:
    """Template para produto desconhecido - NUNCA retorna 404."""
    return {
        "ncm_code": "PESQUISA_EM_ANDAMENTO",
        "product_name": product_name,
        "product_name_it": product_name,
        "product_name_en": product_name,
        "category": "Classificação via IA em andamento",
        "risk_score": 50,
        "risk_level": "PENDING",
        "status": "RESEARCHING",
        "trade_route": {"origin": "BR", "destination": "IT", "origin_name": "Brasil", "destination_name": "Itália"},
        "certificates_required": [
            {"name": "Certificado Fitossanitário", "issuer": "MAPA", "mandatory": True},
            {"name": "Certificado de Origem", "issuer": "Câmara de Comércio", "mandatory": True},
        ],
        "eu_regulations": [
            {"code": "Reg. (CE) 178/2002", "title": "Segurança alimentar geral", "status": "active"},
        ],
        "brazilian_requirements": ["Verificar requisitos específicos no MAPA"],
        "max_residue_limits": {},
        "tariff_info": {"eu_tariff": "Verificar", "notes": "Consultar TARIC"},
        "alerts": [
            f"🔍 Pesquisa IA em andamento para '{product_name}'...",
            "Os dados serão atualizados automaticamente quando a pesquisa completar.",
            "Você também pode clicar 'Atualizar via IA' para verificar.",
        ],
        "risk_factors": {
            "documentation": {"score": 50, "level": "PENDING"},
            "regulatory": {"score": 50, "level": "PENDING"},
            "logistics": {"score": 50, "level": "PENDING"},
            "market_access": {"score": 50, "level": "PENDING"},
        },
        "data_source": "template_pending_research",
        "needs_ai_update": True,
    }


# ============================================================================
# CORE: GET PRODUCT DATA
# ============================================================================

async def get_product_data(
    product_slug: str,
    force_refresh: bool = False,
    background_tasks: Optional[BackgroundTasks] = None,
) -> Dict:
    """
    Obtém dados de compliance. Hierarquia:
    1. Cache (se válido e não forçando refresh)
    2. Claude AI em tempo real (pesquisa síncrona se refresh)
    3. Knowledge base + dispara Claude em background
    4. Template genérico + dispara Claude em background
    """
    slug = normalize_slug(product_slug)
    product_name = product_slug.replace("_", " ").replace("-", " ").title()

    # 1. Cache
    if not force_refresh:
        cached = get_cached(slug)
        if cached:
            cached["data_source_note"] = "Dados em cache"
            # ⚡ Revalida mesmo o cache — garante que dados antigos não sirvam erros
            return apply_regulatory_truth(cached)

    # 2. Se refresh forçado e Claude disponível, pesquisar SÍNCRONAMENTE
    if force_refresh and ANTHROPIC_API_KEY:
        logger.info(f"🔄 Forced refresh via Claude AI: {product_name}")
        claude_result = await research_product_via_claude(slug, product_name)
        if claude_result:
            set_cached(slug, claude_result)
            return claude_result

    # 3. Knowledge base (resposta imediata)
    if slug in REFERENCE_DATA:
        data = {**REFERENCE_DATA[slug]}
        data["data_source"] = "reference_knowledge"
        data["needs_ai_update"] = True
        data["last_updated"] = datetime.now().isoformat()
        data["data_source_note"] = "Dados de referência verificados. Clique 'Atualizar via IA' para pesquisa em tempo real."

        # ⚡ NÃO dispara Claude em background para produtos conhecidos.
        # Os dados do REFERENCE_DATA já são verificados e confiáveis.
        # Claude só é chamado quando o usuário clica explicitamente em "Atualizar via IA" (/refresh).
        # Isso evita custos desnecessários de tokens a cada expiração de cache.

        # ⚡ Validação regulatória antes de cachear e retornar
        data = apply_regulatory_truth(data)
        set_cached(slug, data)
        return data

    # 4. Produto DESCONHECIDO - template + Claude background
    # Para produtos fora do REFERENCE_DATA, Claude é necessário pois não temos dados.
    data = make_unknown_product_template(product_name)
    data["last_updated"] = datetime.now().isoformat()

    if ANTHROPIC_API_KEY and background_tasks:
        background_tasks.add_task(background_claude_research, slug, product_name)
        data["claude_research_status"] = "started_in_background"
        data["alerts"][0] = f"🔍 Pesquisa Claude IA iniciada para '{product_name}'..."
    elif not ANTHROPIC_API_KEY:
        data["alerts"].append("⚠️ Claude AI não configurado. Configure ANTHROPIC_API_KEY no Render.")

    return data


# ============================================================================
# PDF GENERATION
# ============================================================================

def generate_compliance_pdf(product: Dict) -> bytes:
    """Gera PDF de compliance profissional."""
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.units import cm
        from reportlab.lib.colors import HexColor
        from reportlab.pdfgen import canvas

        buffer = io.BytesIO()
        c = canvas.Canvas(buffer, pagesize=A4)
        w, h = A4

        GREEN = HexColor("#0F7A3F")
        DARK = HexColor("#1a1a2e")
        GRAY = HexColor("#666666")

        # Header
        c.setFillColor(GREEN)
        c.rect(0, h - 2.5*cm, w, 2.5*cm, fill=1)
        c.setFillColor(HexColor("#FFFFFF"))
        c.setFont("Helvetica-Bold", 22)
        c.drawString(2*cm, h - 1.7*cm, "ZOI Sentinel")
        c.setFont("Helvetica", 10)
        c.drawString(2*cm, h - 2.2*cm, "Trade Compliance Intelligence Report")
        c.drawRightString(w - 2*cm, h - 1.7*cm, datetime.now().strftime("%d/%m/%Y %H:%M"))

        # Product
        y = h - 4.5*cm
        c.setFillColor(DARK)
        c.setFont("Helvetica-Bold", 26)
        c.drawString(2*cm, y, product.get("product_name", "Produto"))

        y -= 1*cm
        c.setFont("Helvetica", 12)
        c.setFillColor(GRAY)
        c.drawString(2*cm, y, f"NCM: {product.get('ncm_code', 'N/A')}")
        route = product.get("trade_route", {})
        c.drawString(10*cm, y, f"Rota: {route.get('origin_name', 'BR')} → {route.get('destination_name', 'IT')}")

        y -= 0.8*cm
        score = product.get("risk_score", 50)
        c.setFillColor(GREEN)
        c.setFont("Helvetica-Bold", 14)
        c.drawString(2*cm, y, f"Risk Score: {score}/100")
        c.drawString(8*cm, y, f"Status: {product.get('status', 'N/A')}")

        # Sections helper
        def draw_section(y_pos, title, items, format_fn):
            if y_pos < 5*cm:
                c.showPage()
                y_pos = h - 3*cm
            y_pos -= 1.5*cm
            c.setFillColor(DARK)
            c.setFont("Helvetica-Bold", 14)
            c.drawString(2*cm, y_pos, title)
            y_pos -= 0.3*cm
            c.setStrokeColor(GREEN)
            c.setLineWidth(1.5)
            c.line(2*cm, y_pos, 8*cm, y_pos)
            y_pos -= 0.6*cm
            c.setFont("Helvetica", 10)
            c.setFillColor(DARK)
            for item in items:
                text = format_fn(item)
                if y_pos < 2.5*cm:
                    c.showPage()
                    y_pos = h - 3*cm
                c.drawString(2.5*cm, y_pos, f"• {text[:85]}")
                y_pos -= 0.5*cm
            return y_pos

        # Certificates
        certs = product.get("certificates_required", [])
        y = draw_section(y, "Certificados Necessários", certs,
            lambda x: f"{x['name']} ({x['issuer']})" if isinstance(x, dict) else str(x))

        # EU Regulations
        regs = product.get("eu_regulations", [])
        y = draw_section(y, "Regulamentos UE", regs,
            lambda x: f"{x['code']} - {x['title']}" if isinstance(x, dict) else str(x))

        # Brazilian Requirements
        br_reqs = product.get("brazilian_requirements", [])
        y = draw_section(y, "Requisitos Brasileiros", br_reqs, lambda x: str(x))

        # MRL
        mrl = product.get("max_residue_limits", {})
        if mrl:
            mrl_list = [{"name": k, "info": v} for k, v in mrl.items()]
            y = draw_section(y, "Limites Máximos de Resíduos", mrl_list,
                lambda x: f"{x['name'].replace('_',' ').title()}: {x['info'].get('limit','N/A') if isinstance(x['info'],dict) else x['info']}")

        # Alerts
        alerts = product.get("alerts", [])
        if alerts:
            y = draw_section(y, "Alertas", alerts, lambda x: str(x)[:85])

        # Footer
        source = product.get("data_source", "unknown")
        source_labels = {
            "claude_ai_realtime": "Pesquisa IA em Tempo Real (Claude AI — Anthropic)",
            "reference_knowledge": "Base de Referência ZOI",
            "cache": "Cache",
            "template_pending_research": "Template (pesquisa pendente)",
        }
        c.setFillColor(GRAY)
        c.setFont("Helvetica", 7)
        c.drawString(1.5*cm, 1*cm,
            f"ZOI Sentinel v5.0 | Gerado: {datetime.now().strftime('%d/%m/%Y %H:%M')} | "
            f"Fonte: {source_labels.get(source, source)}")
        c.drawRightString(w - 1.5*cm, 1*cm, "© ZOI Trade Advisory")

        c.save()
        return buffer.getvalue()

    except ImportError:
        from fpdf import FPDF
        pdf = FPDF()
        pdf.add_page()
        pdf.set_font("Helvetica", "B", 22)
        pdf.cell(0, 12, "ZOI Sentinel - Compliance Report", ln=True)
        pdf.set_font("Helvetica", "", 11)
        pdf.cell(0, 8, product.get("product_name", "Produto"), ln=True)
        pdf.cell(0, 7, f"NCM: {product.get('ncm_code', 'N/A')}", ln=True)
        pdf.cell(0, 7, f"Risk Score: {product.get('risk_score', 'N/A')}/100", ln=True)
        pdf.cell(0, 7, f"Status: {product.get('status', 'N/A')}", ln=True)
        return bytes(pdf.output())


# ============================================================================
# API ENDPOINTS
# ============================================================================

@app.get("/")
async def root():
    return {
        "service": "ZOI Sentinel v5.0",
        "architecture": "zero_database",
        "ai_engine": "claude_ai_anthropic",
        "claude_configured": bool(ANTHROPIC_API_KEY),
        "claude_model": CLAUDE_MODEL,
        "endpoints": {
            "get_product": "GET /api/products/{slug}",
            "export_pdf": "GET /api/products/{slug}/export-pdf",
            "refresh": "GET /api/products/{slug}/refresh",
            "list": "GET /api/products",
            "health": "GET /health",
            "research_status": "GET /api/research-status/{slug}",
        }
    }


@app.get("/health")
async def health():
    return {
        "status": "healthy",
        "version": "5.0.0",
        "architecture": "zero_database",
        "claude_ai": "configured" if ANTHROPIC_API_KEY else "NOT_CONFIGURED",
        "claude_model": CLAUDE_MODEL,
        "cache_size": len(PRODUCT_CACHE),
        "active_research": len([t for t in CLAUDE_RESEARCH_TASKS.values() if t.get("status") == "running"]),
        "known_products": len(REFERENCE_DATA),
        "timestamp": datetime.now().isoformat(),
    }


@app.get("/api/products")
async def list_products():
    products = []
    for slug, data in REFERENCE_DATA.items():
        products.append({
            "slug": slug,
            "name": data["product_name"],
            "ncm_code": data["ncm_code"],
            "category": data["category"],
            "risk_score": data["risk_score"],
            "status": data["status"],
            "trade_route": data.get("trade_route", {}),
        })
    return {
        "success": True,
        "products": products,
        "total": len(products),
        "note": "Qualquer produto pode ser pesquisado — não listados serão pesquisados via Claude AI.",
    }


@app.get("/api/products/{product_slug}")
async def get_product(product_slug: str, background_tasks: BackgroundTasks):
    """Retorna dados de compliance. Dispara Claude AI em background se necessário."""
    logger.info(f"📦 PRODUCT REQUEST: {product_slug}")
    product_data = await get_product_data(product_slug, background_tasks=background_tasks)
    return {
        "success": True,
        "product": product_data,
        "architecture": "zero_database_v5",
        "ai_engine": "claude_ai" if ANTHROPIC_API_KEY else "reference_only",
        "timestamp": datetime.now().isoformat(),
    }


@app.get("/api/products/{product_slug}/export-pdf")
async def export_pdf(product_slug: str, background_tasks: BackgroundTasks):
    """Gera PDF de compliance."""
    logger.info(f"📄 PDF GENERATION REQUEST: {product_slug}")
    product_data = await get_product_data(product_slug, background_tasks=background_tasks)

    try:
        pdf_bytes = generate_compliance_pdf(product_data)
        safe = product_slug.replace("/", "_").replace("\\", "_")
        filename = f"ZOI_Compliance_{safe}_{datetime.now().strftime('%Y%m%d_%H%M')}.pdf"
        return StreamingResponse(
            io.BytesIO(pdf_bytes),
            media_type="application/pdf",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "Access-Control-Expose-Headers": "Content-Disposition",
            }
        )
    except Exception as e:
        logger.error(f"❌ PDF error: {e}", exc_info=True)
        raise HTTPException(500, detail=f"Erro ao gerar PDF: {str(e)}")


@app.get("/api/products/{product_slug}/refresh")
async def refresh_product(product_slug: str):
    """
    Força pesquisa síncrona via Claude AI.
    Retorna dados atualizados em ~20-40s (vs 60-120s do Manus anterior).
    Chamado quando usuário clica 'Atualizar via IA'.
    """
    logger.info(f"🔄 REFRESH (sync Claude AI): {product_slug}")
    product_data = await get_product_data(product_slug, force_refresh=True)
    return {
        "success": True,
        "product": product_data,
        "refreshed": True,
        "source": product_data.get("data_source", "unknown"),
        "timestamp": datetime.now().isoformat(),
    }


@app.get("/api/research-status/{product_slug}")
async def research_status(product_slug: str):
    """Verifica o status de uma pesquisa Claude AI em andamento."""
    slug = normalize_slug(product_slug)

    task_info = CLAUDE_RESEARCH_TASKS.get(slug, {})
    cached = get_cached(slug)

    # Se tem cache com dados do Claude, pesquisa já completou
    if cached and cached.get("data_source") == "claude_ai_realtime":
        return {
            "slug": slug,
            "research_complete": True,
            "data_source": "claude_ai_realtime",
            "claude_model": cached.get("claude_model", CLAUDE_MODEL),
            "last_updated": cached.get("last_updated"),
        }

    return {
        "slug": slug,
        "research_complete": False,
        "claude_task": task_info,
        "has_cache": cached is not None,
        "cache_source": cached.get("data_source") if cached else None,
    }


# OPTIONS handler (safety net para CORS)
@app.options("/{rest_of_path:path}")
async def preflight(request: Request, rest_of_path: str):
    origin = request.headers.get("origin", "")
    return JSONResponse(
        content={"ok": True},
        headers={
            "Access-Control-Allow-Origin": origin if origin in ALLOWED_ORIGINS else ALLOWED_ORIGINS[0],
            "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, OPTIONS, PATCH",
            "Access-Control-Allow-Headers": "*",
            "Access-Control-Max-Age": "3600",
            "Access-Control-Allow-Credentials": "true",
        }
    )


# ============================================================================
# STARTUP
# ============================================================================

@app.on_event("startup")
async def startup():
    logger.info("=" * 70)
    logger.info("🚀 ZOI SENTINEL v5.0 - Zero Database + Claude AI (Anthropic)")
    logger.info(f"🤖 Claude AI: {'✅ CONFIGURED' if ANTHROPIC_API_KEY else '❌ NOT CONFIGURED'}")
    logger.info(f"🧠 Model: {CLAUDE_MODEL}")
    logger.info(f"📦 Reference products: {len(REFERENCE_DATA)}")
    logger.info(f"🌐 CORS origins: {len(ALLOWED_ORIGINS)}")
    if not ANTHROPIC_API_KEY:
        logger.warning("⚠️ Configure ANTHROPIC_API_KEY no Render para ativar pesquisa em tempo real!")
    logger.info("=" * 70)


# ============================================================================
# MAIN
# ============================================================================
if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
