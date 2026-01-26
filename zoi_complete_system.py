"""
ZOI Sentinel - Trade Advisory System with AI Data Sovereignty
Version 3.0 - Dyad AI as Primary Data Source
Data Sovereignty: Dyad AI is the single source of truth for all compliance data
"""

import re
import os
import json
import time
import enum
import logging
import smtplib
import requests
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Union
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from io import BytesIO

from bs4 import BeautifulSoup
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, Boolean, JSON, ForeignKey, Text, Enum as SQLEnum
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
from fastapi import FastAPI, HTTPException, Depends, BackgroundTasks, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from fastapi.responses import StreamingResponse, Response
from pydantic import BaseModel, EmailStr
from jose import JWTError, jwt
from passlib.context import CryptContext

# ==================================================================================
# CONFIGURAÇÃO DE LOGGING
# ==================================================================================

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger("ZOI_SENTINEL")

# ==================================================================================
# DYAD AI COMPLIANCE NAVIGATOR - SOBERANIA DE DADOS
# ==================================================================================

class DyadComplianceNavigator:
    """
    🧠 CÉREBRO DO SISTEMA ZOI SENTINEL - DATA SOVEREIGNTY
    
    Esta classe é a ÚNICA fonte de verdade para dados de compliance.
    Todos os dados retornados pela Dyad sobrescrevem dados antigos no banco.
    """
    
    def __init__(self):
        self.api_key = os.environ.get('DYAD_API_KEY')
        self.api_url = os.environ.get('DYAD_API_URL', 'https://api.dyad.sh/v1/agents/run')
        
        if not self.api_key:
            logger.warning("⚠️ AVISO: DYAD_API_KEY não configurada! Data Sovereignty comprometida.")
        else:
            logger.info(f"✅ Dyad API inicializada: {self.api_url}")
    
    def get_compliance_intelligence(self, ncm_code: str, product_name: str, target_market: str = "EU") -> Optional[Dict[str, Any]]:
        """
        🎯 MÉTODO PRINCIPAL DE SOBERANIA DE DADOS
        
        Busca inteligência de compliance via IA da Dyad.
        Este é o método que define a verdade dos dados no sistema.
        
        Args:
            ncm_code: Código NCM do produto (OBRIGATÓRIO)
            product_name: Nome do produto em português
            target_market: Mercado de destino (default: "EU")
        
        Returns:
            Dict completo com estrutura padronizada ou None em caso de erro
        """
        
        if not self.api_key:
            logger.error("❌ CRÍTICO: Dyad API não configurada. Data Sovereignty impossível.")
            return None
        
        if not ncm_code or ncm_code.strip() == "":
            logger.error(f"❌ ERRO CRÍTICO: NCM vazio para produto '{product_name}'. Validação falhou.")
            return None
        
        try:
            logger.info(f"\n{'='*80}")
            logger.info(f"🧠 DYAD AI - SOBERANIA DE DADOS INICIADA")
            logger.info(f"Produto: {product_name}")
            logger.info(f"NCM: {ncm_code}")
            logger.info(f"Mercado: {target_market}")
            logger.info(f"{'='*80}")
            
            # Prompt otimizado para estrutura de dados consistente
            prompt = f"""
Você é o sistema de inteligência de compliance comercial ZOI Sentinel.

PRODUTO: {product_name}
NCM: {ncm_code}
MERCADO DESTINO: {target_market}

MISSÃO:
Compile os dados mais recentes e precisos sobre requisitos de exportação deste produto do Brasil para a União Europeia.

DADOS OBRIGATÓRIOS:
1. **risk_score**: Calcule um score de risco de 0-100 (quanto maior, melhor)
2. **risk_status**: Classifique como "green" (>80), "yellow" (60-80) ou "red" (<60)
3. **risk_factors**: Lista de fatores de risco específicos
4. **compliance_alerts**: Lista de alertas e exigências regulatórias
5. **technical_specs**: Especificações técnicas e certificações necessárias
6. **lmr_data**: Limites Máximos de Resíduos com substâncias e valores
7. **rasff_alerts**: Contagem de alertas sanitários (6 meses e 12 meses)
8. **certifications**: Certificações obrigatórias (booleanos)
9. **barriers**: Barreiras não-tarifárias
10. **recommendations**: Recomendações práticas

FORMATO DE RESPOSTA (JSON ESTRITO):
{{
  "risk_score": 75.5,
  "risk_status": "yellow",
  "risk_factors": [
    "Fator 1: Descrição detalhada do risco",
    "Fator 2: Descrição detalhada do risco"
  ],
  "compliance_alerts": [
    "Alerta 1: Requisito regulatório específico",
    "Alerta 2: Requisito regulatório específico"
  ],
  "technical_specs": [
    "Especificação 1: Detalhe técnico completo",
    "Especificação 2: Detalhe técnico completo"
  ],
  "lmr_data": [
    {{"substance": "Nome da substância", "eu_limit_mg_kg": 0.5, "br_limit_mg_kg": 1.0, "source": "Regulamento EU 396/2005"}}
  ],
  "rasff_alerts": {{
    "last_6_months": 2,
    "last_12_months": 5,
    "common_issues": ["Problema comum 1", "Problema comum 2"]
  }},
  "certifications": {{
    "phytosanitary": true,
    "health": false,
    "origin": true,
    "additional": ["Certificado X", "Certificado Y"]
  }},
  "barriers": [
    "Barreira 1: Descrição completa da restrição",
    "Barreira 2: Descrição completa da restrição"
  ],
  "recommendations": [
    "Recomendação 1: Ação concreta e específica",
    "Recomendação 2: Ação concreta e específica"
  ],
  "data_quality": {{
    "confidence_level": "high",
    "sources": ["Fonte oficial 1", "Fonte oficial 2"],
    "last_verified": "2026-01-26"
  }}
}}

REGRAS CRÍTICAS:
- Retorne APENAS JSON válido, sem texto antes ou depois
- Todos os arrays devem conter strings descritivas e completas (não apenas palavras-chave)
- risk_factors, compliance_alerts e technical_specs são OBRIGATÓRIOS e devem ser detalhados
- Se não houver dados para um campo, use array vazio []
- Valores numéricos devem ser precisos e baseados em dados reais
- Cada item de lista deve ser uma frase completa e informativa
"""
            
            payload = {
                "instructions": prompt,
                "max_steps": 15,
                "timeout_seconds": 150
            }
            
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json"
            }
            
            logger.info(f"📡 Enviando requisição para Dyad API...")
            start_time = time.time()
            
            response = requests.post(
                self.api_url,
                json=payload,
                headers=headers,
                timeout=160
            )
            
            elapsed_time = time.time() - start_time
            logger.info(f"⏱️ Tempo de resposta: {elapsed_time:.2f}s")
            
            if response.status_code == 200:
                result = response.json()
                logger.info(f"✅ Resposta recebida da Dyad API (Status 200)")
                
                output_text = result.get('output', '')
                
                compliance_data = self._extract_and_validate_json(output_text)
                
                if compliance_data:
                    logger.info(f"✅ SOBERANIA DE DADOS: Dados parseados e validados")
                    logger.info(f"📊 Risk Score: {compliance_data.get('risk_score', 'N/A')}")
                    logger.info(f"🚦 Risk Status: {compliance_data.get('risk_status', 'N/A')}")
                    logger.info(f"⚠️ Risk Factors: {len(compliance_data.get('risk_factors', []))} identificados")
                    logger.info(f"📋 Compliance Alerts: {len(compliance_data.get('compliance_alerts', []))} alertas")
                    logger.info(f"🔬 Technical Specs: {len(compliance_data.get('technical_specs', []))} especificações")
                    
                    # Adicionar metadados de controle
                    compliance_data['_metadata'] = {
                        'retrieved_at': datetime.utcnow().isoformat(),
                        'source': 'dyad_ai',
                        'api_response_time_seconds': elapsed_time,
                        'ncm_code': ncm_code,
                        'product_name': product_name
                    }
                    
                    return compliance_data
                else:
                    logger.error(f"❌ FALHA NA SOBERANIA: Não foi possível parsear JSON da resposta")
                    logger.debug(f"📄 Resposta bruta (primeiros 1000 chars): {output_text[:1000]}")
                    return None
                    
            else:
                logger.error(f"❌ Erro na API Dyad: Status {response.status_code}")
                logger.error(f"📄 Resposta: {response.text[:500]}")
                return None
                
        except requests.exceptions.Timeout:
            logger.error(f"⏱️ TIMEOUT: Dyad API não respondeu em 160s")
            return None
        except requests.exceptions.RequestException as e:
            logger.error(f"❌ Erro de conexão com Dyad API: {e}")
            return None
        except Exception as e:
            logger.error(f"❌ Erro inesperado na busca Dyad: {e}")
            import traceback
            traceback.print_exc()
            return None
    
    def _extract_and_validate_json(self, text: str) -> Optional[Dict[str, Any]]:
        """
        Extrai, parseia e VALIDA JSON da resposta da Dyad.
        Garante que a estrutura de dados esteja completa e consistente.
        """
        try:
            # Tentar parsear diretamente
            data = json.loads(text)
            return self._validate_structure(data)
        except json.JSONDecodeError:
            # Tentar encontrar JSON dentro do texto
            import re
            json_pattern = r'\{(?:[^{}]|(?:\{(?:[^{}]|(?:\{[^{}]*\}))*\}))*\}'
            matches = re.findall(json_pattern, text, re.DOTALL)
            
            for match in matches:
                try:
                    data = json.loads(match)
                    validated = self._validate_structure(data)
                    if validated:
                        return validated
                except json.JSONDecodeError:
                    continue
            
            logger.error("❌ Nenhum JSON válido encontrado na resposta")
            return None
    
    def _validate_structure(self, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Valida e normaliza a estrutura de dados da Dyad.
        Garante que todos os campos obrigatórios existam.
        """
        required_fields = ['risk_score', 'risk_status', 'risk_factors', 'compliance_alerts', 'technical_specs']
        
        # Verificar campos obrigatórios
        for field in required_fields:
            if field not in data:
                logger.warning(f"⚠️ Campo obrigatório ausente: {field}")
                return None
        
        # Normalizar listas (converter strings longas em listas)
        list_fields = ['risk_factors', 'compliance_alerts', 'technical_specs', 'barriers', 'recommendations']
        for field in list_fields:
            if field in data:
                if isinstance(data[field], str):
                    # Converter string para lista
                    data[field] = [item.strip() for item in data[field].split('\n') if item.strip()]
                elif not isinstance(data[field], list):
                    data[field] = []
        
        # Validar risk_status
        if data['risk_status'] not in ['green', 'yellow', 'red']:
            logger.warning(f"⚠️ risk_status inválido: {data['risk_status']}, ajustando para 'yellow'")
            data['risk_status'] = 'yellow'
        
        # Garantir valores padrão
        data.setdefault('lmr_data', [])
        data.setdefault('rasff_alerts', {'last_6_months': 0, 'last_12_months': 0, 'common_issues': []})
        data.setdefault('certifications', {'phytosanitary': True, 'health': False, 'origin': True, 'additional': []})
        data.setdefault('barriers', [])
        data.setdefault('recommendations', [])
        
        logger.info("✅ Estrutura de dados validada e normalizada")
        return data


# ==================================================================================
# MODELOS DO BANCO DE DADOS
# ==================================================================================

Base = declarative_base()


class TradeDirectionDB(enum.Enum):
    EXPORT = "export"
    IMPORT = "import"


class ProductStateDB(str, enum.Enum):
    ambient = "ambient"
    frozen = "frozen"
    chilled = "chilled"


class RiskStatusDB(enum.Enum):
    GREEN = "green"
    YELLOW = "yellow"
    RED = "red"


class Product(Base):
    __tablename__ = 'products'
    
    id = Column(Integer, primary_key=True)
    key = Column(String(100), unique=True, nullable=False)
    name_pt = Column(String(200), nullable=False)
    name_it = Column(String(200), nullable=False)
    name_en = Column(String(200))
    
    ncm_code = Column(String(8), nullable=False)
    hs_code = Column(String(6), nullable=False)
    taric_code = Column(String(10))
    
    direction = Column(SQLEnum(TradeDirectionDB), nullable=False)
    state = Column(SQLEnum(ProductStateDB), nullable=False)
    category = Column(String(50))
    
    shelf_life_days = Column(Integer)
    transport_days_avg = Column(Integer)
    temperature_min_c = Column(Float)
    temperature_max_c = Column(Float)
    
    requires_phytosanitary_cert = Column(Boolean, default=True)
    requires_health_cert = Column(Boolean, default=False)
    requires_origin_cert = Column(Boolean, default=True)
    
    # ⭐ NOVOS CAMPOS PARA SOBERANIA DE DADOS DYAD
    risk_factors = Column(JSON)  # Lista de fatores de risco da IA
    compliance_alerts = Column(JSON)  # Alertas de compliance da IA
    technical_specs = Column(JSON)  # Especificações técnicas da IA
    dyad_last_sync = Column(DateTime)  # Última sincronização com Dyad
    dyad_data_quality = Column(JSON)  # Metadados de qualidade dos dados
    
    critical_substances = Column(JSON)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    risk_assessments = relationship("RiskAssessment", back_populates="product")
    lmr_data = relationship("LMRData", back_populates="product")


class LMRData(Base):
    __tablename__ = 'lmr_data'
    
    id = Column(Integer, primary_key=True)
    product_id = Column(Integer, ForeignKey('products.id'), nullable=False)
    
    substance = Column(String(200), nullable=False)
    source_lmr = Column(Float)
    dest_lmr = Column(Float)
    detection_rate = Column(Float)
    
    source_authority = Column(String(50))
    regulatory_source = Column(String(500))  # URL ou referência do regulamento
    last_updated = Column(DateTime, default=datetime.utcnow)
    
    product = relationship("Product", back_populates="lmr_data")


class RiskAssessment(Base):
    __tablename__ = 'risk_assessments'
    
    id = Column(Integer, primary_key=True)
    product_id = Column(Integer, ForeignKey('products.id'), nullable=False)
    
    final_score = Column(Float, nullable=False)
    status = Column(SQLEnum(RiskStatusDB), nullable=False)
    
    rasff_score = Column(Float)
    lmr_score = Column(Float)
    phyto_score = Column(Float)
    logistic_score = Column(Float)
    penalty = Column(Float)
    
    rasff_alerts_6m = Column(Integer, default=0)
    rasff_alerts_12m = Column(Integer, default=0)
    
    recommendations = Column(JSON)
    calculation_timestamp = Column(DateTime, default=datetime.utcnow)
    
    # ⭐ Campos para rastreabilidade da fonte de dados
    data_source = Column(String(50))  # 'dyad_ai' ou 'manual'
    dyad_metadata = Column(JSON)  # Metadados da busca Dyad
    
    product = relationship("Product", back_populates="risk_assessments")


class NotificationLog(Base):
    __tablename__ = 'notification_logs'
    
    id = Column(Integer, primary_key=True)
    user_email = Column(String(200), nullable=False)
    product_key = Column(String(100), nullable=False)
    risk_score = Column(Float)
    notification_type = Column(String(50))
    sent_at = Column(DateTime, default=datetime.utcnow)
    success = Column(Boolean, default=True)
    error_message = Column(String(500))


class User(Base):
    __tablename__ = 'users'
    
    id = Column(Integer, primary_key=True)
    email = Column(String(200), unique=True, nullable=False)
    hashed_password = Column(String(200), nullable=False)
    full_name = Column(String(200))
    company = Column(String(200))
    is_admin = Column(Boolean, default=False)
    is_active = Column(Boolean, default=True)
    
    notification_threshold = Column(Float, default=65.0)
    email_notifications = Column(Boolean, default=True)
    
    created_at = Column(DateTime, default=datetime.utcnow)


# Configuração do banco de dados com pool otimizado
DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://zoi_user:IN3LI5N6OshhlVIDetxmCXhX01es3nK8@dpg-d5pkoeer433s73ddm970-a/zoi_db")

if DATABASE_URL and DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,  # ✅ Previne conexões perdidas
    pool_recycle=3600,
    pool_size=5,
    max_overflow=10
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


# ==================================================================================
# CALCULADORA DE RISCO COM DADOS DA DYAD
# ==================================================================================

class EnhancedRiskCalculator:
    """
    Calculadora que PRIORIZA dados da Dyad AI.
    """
    
    def calculate_from_dyad(self, dyad_data: Dict[str, Any]) -> dict:
        """
        Calcula risco usando APENAS dados da Dyad (soberania total).
        """
        risk_score = dyad_data.get('risk_score', 75.0)
        risk_status = dyad_data.get('risk_status', 'yellow')
        
        status_labels = {
            'green': 'Baixo Risco',
            'yellow': 'Risco Moderado',
            'red': 'Alto Risco'
        }
        
        rasff_alerts = dyad_data.get('rasff_alerts', {})
        
        return {
            "score": risk_score,
            "status": risk_status,
            "status_label": status_labels.get(risk_status, 'Risco Moderado'),
            "components": {
                "Sanitário": risk_score * 0.35,
                "Fitossanitário": risk_score * 0.30,
                "Logístico": risk_score * 0.20,
                "Documental": risk_score * 0.15
            },
            "recommendations": dyad_data.get('recommendations', []),
            "alerts": {
                "rasff_6m": rasff_alerts.get('last_6_months', 0),
                "rasff_12m": rasff_alerts.get('last_12_months', 0),
                "historical_rejections": 0
            },
            "risk_factors": dyad_data.get('risk_factors', []),
            "compliance_alerts": dyad_data.get('compliance_alerts', []),
            "technical_specs": dyad_data.get('technical_specs', []),
            "data_source": "dyad_ai"
        }


# ==================================================================================
# GERADOR DE PDF
# ==================================================================================

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_LEFT


class ZOISentinelReportGenerator:
    """
    Gerador de PDF que reflete EXATAMENTE os dados mostrados no aplicativo.
    Garante sincronização total entre PDF e interface.
    """
    
    def _format_list_field(self, data: Union[List[str], str, None]) -> List[str]:
        """
        Formata campos que podem ser lista ou string para lista consistente.
        """
        if not data:
            return []
        
        if isinstance(data, list):
            return [str(item) for item in data if item]
        
        if isinstance(data, str):
            # Se for string longa, quebrar por linhas ou pontos
            if '\n' in data:
                return [line.strip() for line in data.split('\n') if line.strip()]
            elif len(data) > 200:
                sentences = re.split(r'[.!?]+', data)
                return [s.strip() for s in sentences if s.strip()]
            else:
                return [data]
        
        return []
    
    def generate_risk_pdf(self, product_data: dict, risk_data: dict, dyad_data: Optional[dict] = None) -> BytesIO:
        """
        Gera PDF usando os mesmos dados exibidos no aplicativo.
        """
        buffer = BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=A4, topMargin=15*mm, bottomMargin=15*mm)
        styles = getSampleStyleSheet()
        story = []
        
        title_style = ParagraphStyle(
            'CustomTitle',
            parent=styles['Heading1'],
            fontSize=18,
            textColor=colors.HexColor('#1a365d'),
            spaceAfter=12,
            alignment=TA_CENTER,
            fontName='Helvetica-Bold'
        )
        
        section_style = ParagraphStyle(
            'SectionTitle',
            parent=styles['Heading2'],
            fontSize=14,
            textColor=colors.HexColor('#2d3748'),
            spaceAfter=8,
            spaceBefore=12,
            fontName='Helvetica-Bold'
        )
        
        # Cabeçalho
        story.append(Paragraph("🛡️ ZOI SENTINEL", title_style))
        story.append(Paragraph("Relatório de Inteligência de Compliance", styles['Heading3']))
        
        # Indicador de fonte de dados
        data_source = risk_data.get('data_source', 'unknown')
        if data_source == 'dyad_ai':
            source_text = "📡 <b>Dados em Tempo Real - Dyad AI</b>"
            source_color = colors.HexColor('#38a169')
        else:
            source_text = "📂 <b>Dados do Banco de Dados (Fallback)</b>"
            source_color = colors.HexColor('#d69e2e')
        
        source_style = ParagraphStyle(
            'SourceStyle',
            parent=styles['Normal'],
            fontSize=10,
            textColor=source_color,
            alignment=TA_CENTER,
            fontName='Helvetica-Bold'
        )
        story.append(Paragraph(source_text, source_style))
        story.append(Spacer(1, 8*mm))
        
        # Informações do produto
        story.append(Paragraph("INFORMAÇÕES DO PRODUTO", section_style))
        
        product_info = [
            ['Campo', 'Valor'],
            ['Produto', product_data.get('name_pt', 'N/A')],
            ['Nome Italiano', product_data.get('name_it', 'N/A')],
            ['Código NCM', product_data.get('ncm_code', 'N/A')],
            ['Código HS', product_data.get('hs_code', 'N/A')],
            ['Direção', product_data.get('direction', 'N/A').upper()],
            ['Estado', product_data.get('state', 'N/A').capitalize()],
        ]
        
        product_table = Table(product_info, colWidths=[60*mm, 120*mm])
        product_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#e2e8f0')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.HexColor('#1a365d')),
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 10),
            ('FONTSIZE', (0, 1), (-1, -1), 9),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 8),
            ('TOPPADDING', (0, 1), (-1, -1), 6),
            ('BOTTOMPADDING', (0, 1), (-1, -1), 6),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
        ]))
        
        story.append(product_table)
        story.append(Spacer(1, 8*mm))
        
        # Score de risco
        story.append(Paragraph("AVALIAÇÃO DE RISCO", section_style))
        
        score = risk_data.get('score', 0)
        status = risk_data.get('status', 'yellow')
        
        status_colors = {
            'green': colors.HexColor('#38a169'),
            'yellow': colors.HexColor('#d69e2e'),
            'red': colors.HexColor('#e53e3e')
        }
        
        risk_info = [
            ['Métrica', 'Valor'],
            ['Score Final', f"{score:.1f}/100"],
            ['Status', risk_data.get('status_label', 'N/A')],
        ]
        
        risk_table = Table(risk_info, colWidths=[60*mm, 120*mm])
        risk_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#e2e8f0')),
            ('BACKGROUND', (0, 2), (-1, 2), status_colors.get(status, colors.yellow)),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.HexColor('#1a365d')),
            ('TEXTCOLOR', (0, 2), (-1, 2), colors.white),
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTNAME', (0, 2), (1, 2), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 10),
            ('TOPPADDING', (0, 0), (-1, -1), 6),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
        ]))
        
        story.append(risk_table)
        story.append(Spacer(1, 8*mm))
        
        # ⭐ Fatores de Risco (OBRIGATÓRIO - Soberania de Dados)
        risk_factors = self._format_list_field(risk_data.get('risk_factors', []))
        if risk_factors:
            story.append(Paragraph("⚠️ FATORES DE RISCO", section_style))
            
            for i, factor in enumerate(risk_factors[:10], 1):
                factor_para = Paragraph(f"<b>{i}.</b> {factor}", styles['Normal'])
                story.append(factor_para)
                story.append(Spacer(1, 2*mm))
            
            story.append(Spacer(1, 5*mm))
        
        # ⭐ Alertas de Compliance (OBRIGATÓRIO - Soberania de Dados)
        compliance_alerts = self._format_list_field(risk_data.get('compliance_alerts', []))
        if compliance_alerts:
            story.append(Paragraph("📋 ALERTAS DE COMPLIANCE", section_style))
            
            for i, alert in enumerate(compliance_alerts[:10], 1):
                alert_para = Paragraph(f"<b>{i}.</b> {alert}", styles['Normal'])
                story.append(alert_para)
                story.append(Spacer(1, 2*mm))
            
            story.append(Spacer(1, 5*mm))
        
        # ⭐ Especificações Técnicas (OBRIGATÓRIO - Soberania de Dados)
        technical_specs = self._format_list_field(risk_data.get('technical_specs', []))
        if technical_specs:
            story.append(Paragraph("🔬 ESPECIFICAÇÕES TÉCNICAS", section_style))
            
            for i, spec in enumerate(technical_specs[:10], 1):
                spec_para = Paragraph(f"<b>{i}.</b> {spec}", styles['Normal'])
                story.append(spec_para)
                story.append(Spacer(1, 2*mm))
            
            story.append(Spacer(1, 5*mm))
        
        # Componentes
        components = risk_data.get('components', {})
        if components:
            story.append(Paragraph("COMPONENTES DA AVALIAÇÃO", section_style))
            
            component_data = [['Componente', 'Score']]
            for comp_name, comp_score in components.items():
                component_data.append([comp_name, f"{comp_score:.1f}"])
            
            component_table = Table(component_data, colWidths=[90*mm, 90*mm])
            component_table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#e2e8f0')),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.HexColor('#1a365d')),
                ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, -1), 9),
                ('TOPPADDING', (0, 0), (-1, -1), 6),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
                ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
            ]))
            
            story.append(component_table)
            story.append(Spacer(1, 8*mm))
        
        # Dados LMR da Dyad
        if dyad_data and 'lmr_data' in dyad_data and dyad_data['lmr_data']:
            story.append(Paragraph("🧪 LIMITES MÁXIMOS DE RESÍDUOS (LMR)", section_style))
            
            lmr_table_data = [['Substância', 'Limite UE (mg/kg)', 'Limite BR (mg/kg)']]
            for lmr in dyad_data['lmr_data'][:8]:
                lmr_table_data.append([
                    lmr.get('substance', 'N/A'),
                    str(lmr.get('eu_limit_mg_kg', 'N/A')),
                    str(lmr.get('br_limit_mg_kg', 'N/A'))
                ])
            
            lmr_table = Table(lmr_table_data, colWidths=[80*mm, 50*mm, 50*mm])
            lmr_table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#e2e8f0')),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.HexColor('#1a365d')),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, -1), 8),
                ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                ('TOPPADDING', (0, 0), (-1, -1), 5),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
                ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
            ]))
            
            story.append(lmr_table)
            story.append(Spacer(1, 8*mm))
        
        # Recomendações
        recommendations = self._format_list_field(risk_data.get('recommendations', []))
        if recommendations:
            story.append(Paragraph("💡 RECOMENDAÇÕES", section_style))
            
            for i, rec in enumerate(recommendations[:8], 1):
                rec_para = Paragraph(f"<b>{i}.</b> {rec}", styles['Normal'])
                story.append(rec_para)
                story.append(Spacer(1, 2*mm))
            
            story.append(Spacer(1, 5*mm))
        
        # Rodapé
        story.append(Spacer(1, 10*mm))
        footer_style = ParagraphStyle(
            'Footer',
            parent=styles['Normal'],
            fontSize=8,
            textColor=colors.grey,
            alignment=TA_CENTER
        )
        
        timestamp = datetime.now().strftime('%d/%m/%Y às %H:%M:%S')
        story.append(Paragraph(
            f"Relatório gerado em {timestamp} | ZOI Sentinel © 2026",
            footer_style
        ))
        
        doc.build(story)
        buffer.seek(0)
        return buffer


# ==================================================================================
# FASTAPI
# ==================================================================================

app = FastAPI(title="ZOI Sentinel API", version="3.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

SECRET_KEY = os.environ.get("SECRET_KEY", "zoi_sentinel_secret_2026")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 10080

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")


# ==================================================================================
# PYDANTIC MODELS
# ==================================================================================

class TradeDirection(str, enum.Enum):
    EXPORT = "export"
    IMPORT = "import"


class ProductState(str, enum.Enum):
    ambient = "ambient"
    frozen = "frozen"
    chilled = "chilled"


class RiskStatus(str, enum.Enum):
    GREEN = "green"
    YELLOW = "yellow"
    RED = "red"


class ProductBase(BaseModel):
    key: str
    name_pt: str
    name_it: str
    name_en: Optional[str] = None
    ncm_code: str
    hs_code: str
    taric_code: Optional[str] = None
    direction: TradeDirection
    state: ProductState
    category: Optional[str] = None
    shelf_life_days: Optional[int] = None
    transport_days_avg: Optional[int] = None
    temperature_min_c: Optional[float] = None
    temperature_max_c: Optional[float] = None
    requires_phytosanitary_cert: bool = True
    requires_health_cert: bool = False
    requires_origin_cert: bool = True
    critical_substances: Optional[List[str]] = None


class UserCreate(BaseModel):
    email: EmailStr
    password: str
    full_name: Optional[str] = None
    company: Optional[str] = None


class Token(BaseModel):
    access_token: str
    token_type: str


# ==================================================================================
# DEPENDÊNCIAS
# ==================================================================================

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=15)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt


# ==================================================================================
# 🎯 FUNÇÃO PRINCIPAL: GET_PRODUCT_ANALYSIS COM SOBERANIA DE DADOS
# ==================================================================================

def get_product_analysis(product_key: str, db: SessionLocal, force_refresh: bool = False) -> Dict[str, Any]:
    """
    🎯 FUNÇÃO CENTRAL DE SOBERANIA DE DADOS
    
    REGRAS:
    1. SEMPRE chama Dyad AI se houver NCM válido
    2. Salva dados da IA no banco IMEDIATAMENTE
    3. Sobrescreve dados antigos
    4. Retorna dados frescos da IA
    5. Fallback para banco apenas se Dyad falhar
    """
    
    logger.info(f"\n{'='*80}")
    logger.info(f"🎯 GET_PRODUCT_ANALYSIS - Produto: {product_key}")
    logger.info(f"{'='*80}")
    
    # 1. Buscar produto no banco
    product = db.query(Product).filter(Product.key == product_key).first()
    if not product:
        logger.error(f"❌ Produto {product_key} não encontrado no banco")
        raise HTTPException(status_code=404, detail="Product not found")
    
    logger.info(f"✅ Produto encontrado: {product.name_pt}")
    
    # 2. ⚠️ VALIDAÇÃO CRÍTICA DE NCM
    if not product.ncm_code or product.ncm_code.strip() == "":
        logger.error(f"❌ ERRO CRÍTICO: Lovable não enviou o NCM para o produto '{product.name_pt}' (key: {product_key})")
        logger.error(f"⚠️ AÇÃO NECESSÁRIA: Verificar integração com frontend Lovable")
        
        # Retornar erro estruturado
        return {
            "product": {
                "id": product.id,
                "key": product.key,
                "name_pt": product.name_pt,
                "name_it": product.name_it,
                "ncm_code": "ERRO: NCM NÃO FORNECIDO",
                "hs_code": product.hs_code,
                "direction": product.direction.value,
                "state": product.state.value
            },
            "risk_analysis": {
                "score": 50.0,
                "status": "yellow",
                "status_label": "ERRO: Análise Impossível (NCM ausente)",
                "risk_factors": ["ERRO CRÍTICO: NCM não fornecido pelo frontend"],
                "compliance_alerts": ["Impossível analisar compliance sem código NCM"],
                "technical_specs": [],
                "data_source": "error"
            },
            "error": "NCM não fornecido"
        }
    
    logger.info(f"✅ NCM válido: {product.ncm_code}")
    
    # 3. Verificar se precisa atualizar dados
    should_refresh = force_refresh
    
    if not force_refresh and product.dyad_last_sync:
        time_since_sync = datetime.utcnow() - product.dyad_last_sync
        should_refresh = time_since_sync > timedelta(hours=24)
        
        if not should_refresh:
            logger.info(f"📂 Dados recentes encontrados (última sync: {product.dyad_last_sync})")
    else:
        should_refresh = True
        logger.info(f"🔄 Primeira sincronização ou forçada")
    
    # 4. 🧠 BUSCAR DADOS NA DYAD AI (SOBERANIA)
    dyad_data = None
    if should_refresh:
        logger.info(f"🧠 Iniciando busca na Dyad AI (Data Sovereignty)")
        
        dyad = DyadComplianceNavigator()
        dyad_data = dyad.get_compliance_intelligence(
            ncm_code=product.ncm_code,
            product_name=product.name_pt,
            target_market="EU"
        )
        
        if dyad_data:
            logger.info(f"✅ SOBERANIA: Dados obtidos da Dyad AI")
            
            # 5. 💾 SALVAR DADOS NO BANCO IMEDIATAMENTE (SOBRESCREVER)
            try:
                logger.info(f"💾 Salvando dados da Dyad no banco (sobrescrevendo dados antigos)...")
                
                # ⭐ Atualizar campos do produto
                product.risk_factors = dyad_data.get('risk_factors', [])
                product.compliance_alerts = dyad_data.get('compliance_alerts', [])
                product.technical_specs = dyad_data.get('technical_specs', [])
                product.dyad_last_sync = datetime.utcnow()
                product.dyad_data_quality = dyad_data.get('data_quality', {})
                
                # Atualizar certificações
                certs = dyad_data.get('certifications', {})
                if certs:
                    product.requires_phytosanitary_cert = certs.get('phytosanitary', True)
                    product.requires_health_cert = certs.get('health', False)
                    product.requires_origin_cert = certs.get('origin', True)
                
                # Salvar/atualizar dados LMR
                lmr_list = dyad_data.get('lmr_data', [])
                if lmr_list:
                    logger.info(f"💾 Salvando {len(lmr_list)} substâncias LMR...")
                    
                    for lmr_item in lmr_list[:15]:
                        substance = lmr_item.get('substance', '').strip()
                        if not substance:
                            continue
                        
                        existing = db.query(LMRData).filter(
                            LMRData.product_id == product.id,
                            LMRData.substance == substance
                        ).first()
                        
                        eu_limit = lmr_item.get('eu_limit_mg_kg')
                        br_limit = lmr_item.get('br_limit_mg_kg')
                        
                        if existing:
                            if eu_limit is not None:
                                existing.dest_lmr = float(eu_limit)
                            if br_limit is not None:
                                existing.source_lmr = float(br_limit)
                            existing.source_authority = "Dyad AI"
                            existing.last_updated = datetime.utcnow()
                            logger.info(f"  ↻ Atualizado: {substance}")
                        else:
                            new_lmr = LMRData(
                                product_id=product.id,
                                substance=substance,
                                dest_lmr=float(eu_limit) if eu_limit is not None else None,
                                source_lmr=float(br_limit) if br_limit is not None else None,
                                source_authority="Dyad AI",
                                regulatory_source=lmr_item.get('source', '')
                            )
                            db.add(new_lmr)
                            logger.info(f"  + Criado: {substance}")
                
                # Criar novo Risk Assessment
                calc = EnhancedRiskCalculator()
                risk_result = calc.calculate_from_dyad(dyad_data)
                
                rasff = dyad_data.get('rasff_alerts', {})
                
                assessment = RiskAssessment(
                    product_id=product.id,
                    final_score=risk_result['score'],
                    status=RiskStatusDB(risk_result['status']),
                    rasff_score=risk_result['components']['Sanitário'],
                    lmr_score=risk_result['components']['Fitossanitário'],
                    phyto_score=risk_result['components']['Fitossanitário'],
                    logistic_score=risk_result['components']['Logístico'],
                    penalty=100 - risk_result['score'],
                    rasff_alerts_6m=rasff.get('last_6_months', 0),
                    rasff_alerts_12m=rasff.get('last_12_months', 0),
                    recommendations=risk_result['recommendations'],
                    data_source='dyad_ai',
                    dyad_metadata=dyad_data.get('_metadata', {})
                )
                db.add(assessment)
                
                # Commit tudo
                db.commit()
                db.refresh(product)
                
                logger.info(f"✅ SOBERANIA ESTABELECIDA: Todos os dados salvos no banco")
                logger.info(f"   - risk_factors: {len(product.risk_factors)} fatores")
                logger.info(f"   - compliance_alerts: {len(product.compliance_alerts)} alertas")
                logger.info(f"   - technical_specs: {len(product.technical_specs)} specs")
                logger.info(f"   - LMR data: {len(lmr_list)} substâncias")
                
            except Exception as e:
                logger.error(f"❌ Erro ao salvar dados da Dyad: {e}")
                db.rollback()
                import traceback
                traceback.print_exc()
        else:
            logger.warning(f"⚠️ Dyad não retornou dados, usando dados do banco como fallback")
    
    # 6. MONTAR RESPOSTA COM DADOS MAIS RECENTES DO BANCO
    logger.info(f"📦 Montando resposta com dados do banco...")
    
    latest_assessment = db.query(RiskAssessment).filter(
        RiskAssessment.product_id == product.id
    ).order_by(RiskAssessment.calculation_timestamp.desc()).first()
    
    if latest_assessment:
        risk_analysis = {
            "score": float(latest_assessment.final_score),
            "status": latest_assessment.status.value,
            "status_label": {
                'green': 'Baixo Risco',
                'yellow': 'Risco Moderado',
                'red': 'Alto Risco'
            }.get(latest_assessment.status.value, 'Risco Moderado'),
            "components": {
                "Sanitário": float(latest_assessment.rasff_score or 0),
                "Fitossanitário": float(latest_assessment.lmr_score or 0),
                "Logístico": float(latest_assessment.logistic_score or 0),
                "Documental": float(latest_assessment.penalty or 0)
            },
            "recommendations": latest_assessment.recommendations or [],
            "alerts": {
                "rasff_6m": latest_assessment.rasff_alerts_6m,
                "rasff_12m": latest_assessment.rasff_alerts_12m
            },
            "risk_factors": product.risk_factors or [],
            "compliance_alerts": product.compliance_alerts or [],
            "technical_specs": product.technical_specs or [],
            "data_source": latest_assessment.data_source or 'database',
            "last_updated": product.dyad_last_sync.isoformat() if product.dyad_last_sync else None
        }
    else:
        risk_analysis = {
            "score": 75.0,
            "status": "yellow",
            "status_label": "Risco Moderado",
            "components": {},
            "recommendations": [],
            "alerts": {"rasff_6m": 0, "rasff_12m": 0},
            "risk_factors": product.risk_factors or [],
            "compliance_alerts": product.compliance_alerts or [],
            "technical_specs": product.technical_specs or [],
            "data_source": "database",
            "last_updated": None
        }
    
    result = {
        "product": {
            "id": product.id,
            "key": product.key,
            "name_pt": product.name_pt,
            "name_it": product.name_it,
            "name_en": product.name_en,
            "ncm_code": product.ncm_code,
            "hs_code": product.hs_code,
            "direction": product.direction.value,
            "state": product.state.value,
            "category": product.category,
            "requires_phytosanitary_cert": product.requires_phytosanitary_cert,
            "requires_health_cert": product.requires_health_cert,
            "requires_origin_cert": product.requires_origin_cert
        },
        "risk_analysis": risk_analysis,
        "dyad_raw_data": dyad_data
    }
    
    logger.info(f"✅ Resposta montada com sucesso")
    logger.info(f"📊 Fonte de dados: {risk_analysis['data_source']}")
    logger.info(f"{'='*80}\n")
    
    return result


# ==================================================================================
# ROTAS
# ==================================================================================

@app.get("/")
def root():
    return {
        "service": "ZOI Sentinel API",
        "version": "3.0",
        "status": "operational",
        "features": [
            "🧠 AI Data Sovereignty (Dyad)",
            "📡 Real-time Compliance Intelligence",
            "🎯 Risk Assessment",
            "📄 PDF Export",
            "🔄 Auto-sync Database"
        ],
        "dyad_configured": bool(os.environ.get('DYAD_API_KEY')),
        "data_sovereignty": "Dyad AI is the primary source of truth"
    }


@app.get("/api/products")
def list_products(db: SessionLocal = Depends(get_db)):
    products = db.query(Product).all()
    return [
        {
            "id": p.id,
            "key": p.key,
            "name_pt": p.name_pt,
            "name_it": p.name_it,
            "ncm_code": p.ncm_code,
            "direction": p.direction.value,
            "state": p.state.value,
            "last_sync": p.dyad_last_sync.isoformat() if p.dyad_last_sync else None
        }
        for p in products
    ]


@app.get("/api/products/{product_key}")
def get_product(product_key: str, refresh: bool = False, db: SessionLocal = Depends(get_db)):
    """
    Retorna análise completa do produto com dados da Dyad AI.
    
    Query params:
        refresh: Se True, força nova busca na Dyad AI
    """
    return get_product_analysis(product_key, db, force_refresh=refresh)


# ==================================================================================
# 📄 ROTA: EXPORT PDF COM SINCRONIZAÇÃO TOTAL
# ==================================================================================

@app.get("/api/products/{product_key}/export-pdf")
def export_risk_pdf(product_key: str, db: SessionLocal = Depends(get_db)):
    """
    📄 GERA PDF COM DADOS IDÊNTICOS AO APLICATIVO
    
    Garante que o PDF mostre exatamente os mesmos dados que o usuário vê na tela.
    """
    logger.info(f"\n{'='*80}")
    logger.info(f"📄 GERAÇÃO DE PDF - Produto: {product_key}")
    logger.info(f"{'='*80}\n")
    
    # 1. Obter análise completa (que já busca Dyad se necessário)
    analysis = get_product_analysis(product_key, db, force_refresh=False)
    
    product_data = analysis['product']
    risk_data = analysis['risk_analysis']
    dyad_raw = analysis.get('dyad_raw_data')
    
    logger.info(f"📊 Gerando PDF com dados de: {risk_data['data_source']}")
    
    # 2. Gerar PDF
    generator = ZOISentinelReportGenerator()
    pdf_buffer = generator.generate_risk_pdf(product_data, risk_data, dyad_raw)
    
    logger.info(f"✅ PDF gerado com sucesso!")
    logger.info(f"{'='*80}\n")
    
    # 3. Retornar PDF
    return StreamingResponse(
        pdf_buffer,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f"attachment; filename=zoi_sentinel_{product_key}.pdf"
        }
    )


# ==================================================================================
# ROTAS DE ADMINISTRAÇÃO
# ==================================================================================

@app.post("/api/admin/products")
def create_product(product_data: dict, background_tasks: BackgroundTasks, db: SessionLocal = Depends(get_db)):
    """
    Cria novo produto e IMEDIATAMENTE busca dados na Dyad.
    """
    logger.info(f"\n📝 Criando novo produto: {product_data.get('name_pt', 'N/A')}")
    
    # Validação crítica de NCM
    ncm = product_data.get("ncm_code", "").strip()
    if not ncm:
        logger.error(f"❌ ERRO CRÍTICO: Tentativa de criar produto sem NCM!")
        raise HTTPException(status_code=400, detail="NCM code is required")
    
    try:
        new_p = Product(
            key=product_data["key"],
            name_pt=product_data["name_pt"],
            name_it=product_data.get("name_it", product_data["name_pt"]),
            ncm_code=ncm,
            hs_code=ncm[:6],
            direction=TradeDirectionDB(product_data["direction"]),
            state=ProductStateDB(product_data["state"]),
            requires_phytosanitary_cert=product_data.get("requires_phytosanitary_cert", True)
        )
        db.add(new_p)
        db.commit()
        db.refresh(new_p)
        
        logger.info(f"✅ Produto '{new_p.name_pt}' criado com ID {new_p.id}")
        logger.info(f"🧠 Iniciando busca Dyad em segundo plano...")
        
        # Disparar busca Dyad em background
        background_tasks.add_task(get_product_analysis, new_p.key, db, True)
        
        return {
            "status": "success",
            "message": f"Produto '{new_p.name_pt}' criado. Análise Dyad AI iniciada.",
            "product_key": new_p.key
        }
        
    except Exception as e:
        db.rollback()
        logger.error(f"❌ Erro ao criar produto: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/admin/products/{product_key}")
def delete_product(product_key: str, db: SessionLocal = Depends(get_db)):
    product = db.query(Product).filter(Product.key == product_key).first()
    if product:
        db.delete(product)
        db.commit()
        logger.info(f"✅ Produto {product_key} removido")
        return {"status": "success", "message": f"Produto {product_key} removido"}
    
    logger.warning(f"⚠️ Produto {product_key} não encontrado")
    raise HTTPException(status_code=404, detail="Produto não encontrado")


@app.post("/api/products/{product_key}/refresh")
def force_refresh(product_key: str, db: SessionLocal = Depends(get_db)):
    """
    Força atualização dos dados via Dyad AI.
    """
    logger.info(f"🔄 Forçando refresh para produto: {product_key}")
    return get_product_analysis(product_key, db, force_refresh=True)


# ==================================================================================
# AUTENTICAÇÃO
# ==================================================================================

@app.post("/api/users", status_code=status.HTTP_201_CREATED)
def create_user(user: UserCreate, db: SessionLocal = Depends(get_db)):
    existing_user = db.query(User).filter(User.email == user.email).first()
    if existing_user:
        raise HTTPException(status_code=400, detail="Email already registered")
    
    db_user = User(
        email=user.email,
        hashed_password=get_password_hash(user.password),
        full_name=user.full_name,
        company=user.company
    )
    
    db.add(db_user)
    db.commit()
    db.refresh(db_user)
    
    return {"message": "User created successfully", "email": db_user.email}


@app.post("/token", response_model=Token)
def login(form_data: OAuth2PasswordRequestForm = Depends(), db: SessionLocal = Depends(get_db)):
    user = db.query(User).filter(User.email == form_data.username).first()
    
    if not user or not verify_password(form_data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": user.email}, expires_delta=access_token_expires
    )
    
    return {"access_token": access_token, "token_type": "bearer"}


@app.get("/api/admin/stats")
def get_admin_stats(db: SessionLocal = Depends(get_db)):
    total_products = db.query(Product).count()
    total_assessments = db.query(RiskAssessment).count()
    total_users = db.query(User).count()
    
    recent_sync = datetime.utcnow() - timedelta(hours=24)
    synced_products = db.query(Product).filter(Product.dyad_last_sync >= recent_sync).count()
    
    green_count = db.query(RiskAssessment).filter(RiskAssessment.status == RiskStatusDB.GREEN).count()
    yellow_count = db.query(RiskAssessment).filter(RiskAssessment.status == RiskStatusDB.YELLOW).count()
    red_count = db.query(RiskAssessment).filter(RiskAssessment.status == RiskStatusDB.RED).count()
    
    return {
        "total_products": total_products,
        "total_assessments": total_assessments,
        "total_users": total_users,
        "synced_products_24h": synced_products,
        "status_distribution": {
            "green": green_count,
            "yellow": yellow_count,
            "red": red_count
        },
        "dyad_configured": bool(os.environ.get('DYAD_API_KEY'))
    }


# ==================================================================================
# INICIALIZAÇÃO
# ==================================================================================

if __name__ == "__main__":
    import uvicorn
    
    Base.metadata.create_all(bind=engine)
    
    port = int(os.environ.get("PORT", 8000))
    
    logger.info(f"\n{'='*80}")
    logger.info(f"🛡️ ZOI SENTINEL API v3.0 - INICIANDO")
    logger.info(f"{'='*80}")
    logger.info(f"🔌 Porta: {port}")
    logger.info(f"🧠 Dyad AI: {'✅ Configurado' if os.environ.get('DYAD_API_KEY') else '❌ Não configurado'}")
    logger.info(f"💾 Database: {DATABASE_URL.split('@')[1] if '@' in DATABASE_URL else 'Local'}")
    logger.info(f"📡 Data Sovereignty: ATIVADA")
    logger.info(f"{'='*80}\n")
    
    uvicorn.run(app, host="0.0.0.0", port=port)
