"""
Activity Agent
NBA 추천을 구체적인 Activity로 변환·저장.
- 연결된 NBA 액션의 승인 상태(nba_approval) 반영
- Activity 자체 진행 상태(activity_status) 포함
- 모든 기한은 구체적인 날짜(YYYY-MM-DD)로 표시
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from agents.base_agent import BaseAgent
from tools import data_tools as dt

MODEL = "claude-sonnet-4-6"

SYSTEM_PROMPT = """당신은 증권사 기관영업 CRM의 활동 일정 관리 전문 에이전트입니다.

역할:
- NBA 추천 결과를 구체적인 Activity(이메일/전화/미팅/리포트 발송)로 변환
- 각 Activity에 실행 가능한 구체적 날짜(YYYY-MM-DD), 담당자, 체크리스트를 부여
- 연결된 NBA 액션의 승인 상태를 Activity에 그대로 반영
- Activity 자체의 진행 상태를 별도로 관리

Activity 설계 원칙:
1. 모든 due_date는 반드시 YYYY-MM-DD 형식의 구체적 날짜로 작성 ("이번 주", "2주 내" 등 금지)
2. 즉시 액션은 분석 기준일 +1~3일, 단기는 +5~10일, 중기는 +14~30일
3. 각 Activity는 독립적으로 실행 가능한 단위로 분리
4. 선행 조건이 있는 경우 depends_on 필드로 명시
5. 이메일 발송 후 반드시 후속 전화 Activity를 연결 (depends_on 활용)
6. 미팅 Activity는 사전 준비 Internal Activity를 선행으로 설정

NBA 승인 상태(nba_approval.status) 의미:
- ai_proposed: AI가 NBA를 제안한 상태 → Activity 실행 전 CRM 승인 필요
- crm_approved: CRM 담당자가 NBA를 승인한 상태 → 세일즈 담당자 최종 승인 대기
- sales_approved: 세일즈 담당자가 최종 승인 → Activity 즉시 실행 가능

Activity 진행 상태(activity_status) 값:
- pending: 대기 중 (아직 시작 안 함)
- in_progress: 진행 중
- completed: 완료
- cancelled: 취소됨"""

TOOLS = [
    {
        "name": "load_nba_and_context",
        "description": "NBA 추천 결과(승인 상태 포함), 기존 액션플랜, 고객 기본정보를 통합 로드합니다.",
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
                    "items": {"type": "object"},
                    "description": """Activity 항목 목록. 각 항목:
{
  id: "ACT-C001-001" 형식,
  title: 활동 제목,
  type: "email" | "call" | "meeting" | "report" | "internal",
  due_date: "YYYY-MM-DD" (반드시 구체적 날짜),
  priority: "urgent" | "high" | "medium" | "low",
  activity_status: {
    status: "pending" | "in_progress" | "completed" | "cancelled",
    updated_at: null (초기값),
    updated_by: null (초기값),
    note: null (초기값)
  },
  nba_approval: {
    linked_nba_rank: 연결된 NBA 액션의 rank (int),
    linked_nba_title: 연결된 NBA 액션 제목,
    status: 연결된 NBA의 approval.status 값 그대로 복사 ("ai_proposed" | "crm_approved" | "sales_approved"),
    crm_approved_by: 연결된 NBA의 approval.crm_approved_by 값 (없으면 null),
    sales_approved_by: 연결된 NBA의 approval.sales_approved_by 값 (없으면 null)
  },
  assigned_to: 담당자명,
  description: 구체적 실행 내용,
  checklist: [실행 체크리스트 항목들],
  depends_on: null 또는 선행 Activity ID,
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
                "analysis_date": dt.now_kst_str("%Y-%m-%d"),
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
        analysis_date = dt.now_kst_str("%Y-%m-%d")
        prompt = f"""고객 ID {customer_id}의 Activity 일정을 생성해주세요.
분석일: {analysis_date}

단계:
1. load_nba_and_context 도구로 NBA 추천(actions 배열 + 각 action의 approval 객체)과 현황 데이터를 로드하세요
2. NBA actions를 구체적인 Activity로 변환하세요
   - 각 Activity의 nba_approval 필드에 연결된 NBA action의 approval 상태를 그대로 복사하세요
   - activity_status는 모두 "pending"으로 초기화하고 updated_at, updated_by, note는 null로 설정하세요
3. 모든 due_date는 분석일({analysis_date}) 기준 YYYY-MM-DD 형식의 구체적 날짜로 작성하세요
4. 이메일 발송 Activity에는 반드시 후속 전화 Activity를 depends_on으로 연결하세요
5. 미팅 Activity에는 사전 준비 Internal Activity를 선행으로 설정하세요
6. save_activity_schedule 도구로 Activity 목록을 저장하세요
7. NBA 승인 상태별(ai_proposed / crm_approved / sales_approved) Activity 현황을 요약하세요"""
        return super().run(prompt)
