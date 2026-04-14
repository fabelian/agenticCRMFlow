"""
Persona Agent
세일즈 노트와 과거 이력을 분석해 고객 성향 페르소나를 생성·저장
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from agents.base_agent import BaseAgent
from tools import data_tools as dt

MODEL = "claude-sonnet-4-6"

SYSTEM_PROMPT = """당신은 증권사 기관영업 CRM의 고객 페르소나 분석 전문 에이전트입니다.

역할:
- 세일즈 노트와 액션플랜 이력을 분석하여 고객의 투자 성향, 선호도, 행동 패턴을 파악
- 구조화된 페르소나 프로필을 생성하여 저장
- 영업 담당자가 고객 특성을 즉시 파악할 수 있도록 핵심만 추출

분석 시 주의사항:
- 고객이 명시적으로 표현한 선호/거부 사항을 정확히 기록
- 실제 거래로 연결된 행동 패턴을 특히 중시
- 경쟁사 대비 당사의 포지션을 파악하여 기록
- 페르소나는 JSON 구조체로 저장할 것"""

TOOLS = [
    {
        "name": "load_customer_raw_data",
        "description": "고객의 기본 프로필, 세일즈 노트 전체, 액션플랜 이력, 미완료 액션을 모두 로드합니다.",
        "input_schema": {
            "type": "object",
            "properties": {
                "customer_id": {"type": "string", "description": "고객 ID (예: C001)"}
            },
            "required": ["customer_id"],
        },
    },
    {
        "name": "save_persona",
        "description": "분석 완료된 고객 페르소나를 저장합니다.",
        "input_schema": {
            "type": "object",
            "properties": {
                "customer_id": {"type": "string"},
                "persona": {
                    "type": "object",
                    "description": """저장할 페르소나 객체. 다음 필드를 포함해야 합니다:
- company_name: 회사명
- tier: 고객 등급
- relationship_score: 관계 점수 (0~100)
- top_service_priorities: 중시하는 서비스 요소 Top3 (list)
- sector_interests: 관심 섹터 우선순위 (list of {sector, interest_level, rationale})
- avoid_topics: 언급 금지 주제 (list)
- decision_triggers: 실제 거래로 연결된 트리거 (list)
- communication_preferences: 선호 채널/방식 (dict)
- key_concerns: 현재 주요 우려사항 (list)
- competitive_context: 경쟁사 대비 당사 포지션 (dict)
- key_stakeholders: 핵심 의사결정자 (list)""",
                },
            },
            "required": ["customer_id", "persona"],
        },
    },
]


class PersonaAgent(BaseAgent):
    def __init__(self, model: str = None, provider: str = "anthropic"):
        super().__init__(
            name="PersonaAgent",
            model=model or MODEL,
            system_prompt=SYSTEM_PROMPT,
            tools=TOOLS,
            provider=provider,
        )

    def execute_tool(self, tool_name: str, tool_input: dict) -> dict:
        if tool_name == "load_customer_raw_data":
            customer_id = tool_input["customer_id"]
            return dt.build_raw_context(customer_id)

        if tool_name == "save_persona":
            customer_id = tool_input["customer_id"]
            persona = tool_input["persona"]
            dt.save_persona(customer_id, persona)
            return {"status": "saved", "customer_id": customer_id}

        return {"error": f"알 수 없는 도구: {tool_name}"}

    def run(self, customer_id: str) -> str:
        prompt = f"""고객 ID {customer_id}에 대한 페르소나 분석을 수행해주세요.

단계:
1. load_customer_raw_data 도구로 고객 데이터를 로드하세요
2. 세일즈 노트와 액션플랜을 분석하여 페르소나를 도출하세요
3. save_persona 도구로 결과를 저장하세요
4. 핵심 분석 결과를 요약하여 응답하세요

페르소나 품질 기준:
- 모든 필드를 구체적 근거와 함께 채울 것
- relationship_score는 최근 감정, 실거래 이력, 불만 사항을 종합하여 산정
- sector_interests는 실제 언급 빈도와 거래 연결 여부를 반영"""
        return super().run(prompt)
