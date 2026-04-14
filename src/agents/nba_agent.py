"""
NBA Agent (Next Best Action)
페르소나 + 미완료 액션 + 거래 이력을 기반으로 최적 영업 행동 추천
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from agents.base_agent import BaseAgent
from tools import data_tools as dt

MODEL = "claude-opus-4-6"

SYSTEM_PROMPT = """당신은 증권사 기관영업 전문 Next Best Action 추천 에이전트입니다.

역할:
- 고객 페르소나와 현황 데이터를 종합하여 최적의 영업 행동을 우선순위별로 추천
- 각 행동의 긴급도, 예상 효과, 구체적 실행 방법을 제시
- 고객 이탈 리스크와 거래 창출 기회를 동시에 고려

추천 원칙:
1. 기한 초과 미완료 액션은 즉시 처리 필수
2. 고객이 명시적으로 요청한 사항을 최우선으로
3. 실제 거래로 연결된 패턴(성공 패턴)을 반복 활용
4. 고객이 싫어하는 접근 방식은 철저히 회피
5. 각 액션은 SMART 기준(Specific·Measurable·Achievable·Relevant·Time-bound)으로 작성"""

TOOLS = [
    {
        "name": "load_persona_and_history",
        "description": "저장된 고객 페르소나, 세일즈 노트, 미완료 액션플랜, 거래 이력을 통합 로드합니다.",
        "input_schema": {
            "type": "object",
            "properties": {
                "customer_id": {"type": "string"}
            },
            "required": ["customer_id"],
        },
    },
    {
        "name": "save_nba_recommendations",
        "description": "생성된 NBA 추천 결과를 저장합니다.",
        "input_schema": {
            "type": "object",
            "properties": {
                "customer_id": {"type": "string"},
                "nba_data": {
                    "type": "object",
                    "description": """NBA 데이터 구조:
- summary: 전체 상황 요약 (string)
- risk_level: 고객 이탈 위험도 (high/medium/low)
- immediate_actions: 즉시 실행 액션 list (이번 주)
- short_term_actions: 단기 액션 list (2주 내)
- medium_term_actions: 중기 액션 list (1개월 내)
- avoid_actions: 절대 금지 행동 list
- expected_outcomes: 예상 성과 (3개월 기준)
각 action 항목: {title, rationale, how_to, expected_reaction, success_metric, urgency}""",
                },
            },
            "required": ["customer_id", "nba_data"],
        },
    },
]


class NBAAgent(BaseAgent):
    def __init__(self, model: str = None, provider: str = "anthropic"):
        super().__init__(
            name="NBAAgent",
            model=model or MODEL,
            system_prompt=SYSTEM_PROMPT,
            tools=TOOLS,
            provider=provider,
        )

    def execute_tool(self, tool_name: str, tool_input: dict) -> dict:
        if tool_name == "load_persona_and_history":
            cid = tool_input["customer_id"]
            ctx = dt.build_raw_context(cid)
            ctx["persona"] = dt.get_persona(cid)
            return ctx

        if tool_name == "save_nba_recommendations":
            dt.save_nba(tool_input["customer_id"], tool_input["nba_data"])
            return {"status": "saved", "customer_id": tool_input["customer_id"]}

        return {"error": f"알 수 없는 도구: {tool_name}"}

    def run(self, customer_id: str) -> str:
        prompt = f"""고객 ID {customer_id}의 Next Best Action을 분석해주세요.

단계:
1. load_persona_and_history 도구로 페르소나와 이력 데이터를 로드하세요
2. 미완료 액션 중 기한 초과 항목을 먼저 파악하세요
3. 페르소나의 decision_triggers(실거래 성공 패턴)를 활용한 액션을 설계하세요
4. save_nba_recommendations 도구로 결과를 저장하세요
5. 최우선 즉시 실행 액션과 그 이유를 명확히 설명하세요

핵심 판단 기준:
- 기한 초과 일수가 많을수록 즉시 처리 우선
- 고객이 명시적으로 요청한 사항 > 영업이 판단한 사항
- 과거 거래 체결로 이어진 패턴을 반드시 재활용
- avoid_topics는 어떤 이유로도 건드리지 않음"""
        return super().run(prompt)
