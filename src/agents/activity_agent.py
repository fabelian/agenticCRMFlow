"""
Activity Agent
NBA 추천을 구체적인 달력 일정과 Activity로 변환·저장
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from agents.base_agent import BaseAgent
from tools import data_tools as dt

MODEL = "claude-sonnet-4-6"

SYSTEM_PROMPT = """당신은 증권사 기관영업 CRM의 활동 일정 관리 전문 에이전트입니다.

역할:
- NBA 추천 결과를 구체적인 Activity(이메일/전화/미팅/리포트 발송)로 변환
- 각 Activity에 실행 가능한 구체적 날짜, 담당자, 체크리스트를 부여
- 기존 미완료 액션과 충돌·중복 여부를 확인하여 최적 일정 생성

Activity 설계 원칙:
1. 즉시 액션은 분석 기준일 +1~3일 이내로 설정
2. 단기 액션은 +5~10일, 중기 액션은 +14~30일
3. 각 Activity는 독립적으로 실행 가능한 단위로 분리
4. 선행 조건이 있는 경우 depends_on 필드로 명시
5. 고객 응대 예절: 이메일 → 전화 확인 순서 유지"""

TOOLS = [
    {
        "name": "load_nba_and_context",
        "description": "NBA 추천 결과, 기존 액션플랜, 고객 기본정보를 통합 로드합니다.",
        "input_schema": {
            "type": "object",
            "properties": {
                "customer_id": {"type": "string"}
            },
            "required": ["customer_id"],
        },
    },
    {
        "name": "save_activity_schedule",
        "description": "생성된 Activity 일정 목록을 저장합니다.",
        "input_schema": {
            "type": "object",
            "properties": {
                "customer_id": {"type": "string"},
                "activities": {
                    "type": "array",
                    "description": """Activity 항목 목록. 각 항목:
{
  id: "ACT-C001-001" 형식,
  title: 활동 제목,
  type: "email" | "call" | "meeting" | "report" | "internal",
  due_date: "YYYY-MM-DD",
  priority: "urgent" | "high" | "medium" | "low",
  status: "pending",
  assigned_to: 담당자명,
  description: 구체적 실행 내용,
  checklist: [실행 체크리스트 항목들],
  depends_on: null 또는 선행 Activity ID,
  linked_nba_action: 연결된 NBA 액션 제목,
  expected_outcome: 기대 결과
}""",
                },
            },
            "required": ["customer_id", "activities"],
        },
    },
]


class ActivityAgent(BaseAgent):
    def __init__(self, model: str = None, provider: str = "anthropic"):
        super().__init__(
            name="ActivityAgent",
            model=model or MODEL,
            system_prompt=SYSTEM_PROMPT,
            tools=TOOLS,
            provider=provider,
        )

    def execute_tool(self, tool_name: str, tool_input: dict) -> dict:
        if tool_name == "load_nba_and_context":
            cid = tool_input["customer_id"]
            return {
                "customer": dt.get_customer(cid),
                "nba": dt.get_nba(cid),
                "existing_action_plans": dt.get_action_plans(cid),
                "pending_actions": dt.get_pending_actions(cid),
                "analysis_date": datetime.now().strftime("%Y-%m-%d"),
            }

        if tool_name == "save_activity_schedule":
            dt.save_activities(tool_input["customer_id"], tool_input["activities"])
            return {
                "status": "saved",
                "count": len(tool_input["activities"]),
                "customer_id": tool_input["customer_id"],
            }

        return {"error": f"알 수 없는 도구: {tool_name}"}

    def run(self, customer_id: str) -> str:
        prompt = f"""고객 ID {customer_id}의 Activity 일정을 생성해주세요.

단계:
1. load_nba_and_context 도구로 NBA 추천과 현황 데이터를 로드하세요
2. NBA의 immediate / short_term / medium_term 액션을 각각 구체적인 Activity로 변환하세요
3. 기존 미완료 액션과 중복되는 항목은 통합하거나 업데이트 표시하세요
4. save_activity_schedule 도구로 Activity 목록을 저장하세요
5. 생성된 일정 요약을 달력 형태로 설명하세요

Activity 변환 규칙:
- 이메일 발송 Activity는 반드시 후속 전화 Activity를 연결 (depends_on 활용)
- 미팅 Activity는 사전 준비 Internal Activity를 선행으로 설정
- 기한 초과 항목은 due_date를 오늘 기준 +1일로 재설정"""
        return super().run(prompt)
