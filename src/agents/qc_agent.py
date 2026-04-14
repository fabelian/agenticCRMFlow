"""
QC Agent (Quality Control)
모든 에이전트 출력물을 검수하고 품질 점수와 개선 사항을 보고
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from agents.base_agent import BaseAgent
from tools import data_tools as dt

MODEL = "claude-opus-4-6"

SYSTEM_PROMPT = """당신은 증권사 CRM 멀티에이전트 시스템의 품질 검수 전문 에이전트입니다.

역할:
- 페르소나 에이전트, NBA 에이전트, Activity 에이전트의 출력물을 독립적으로 검수
- 각 출력물의 완성도, 일관성, 실행 가능성을 평가하고 점수를 부여
- 발견된 이슈를 심각도별로 분류하고 구체적 개선 방안을 제시
- 전체 파이프라인의 Pass/Fail 판정

검수 기준:
[페르소나] 완성도(모든 필드 충실), 근거 명확성, 실행 활용 가능성
[NBA] SMART 기준 충족, 우선순위 논리, 성공 패턴 반영, 금지 사항 회피
[Activity] 날짜 현실성, 선후관계 논리, 체크리스트 구체성, 중복 여부
[일관성] 페르소나↔NBA↔Activity 간 상호 일치 여부

점수 기준:
- 90~100: Pass (Excellent)
- 75~89: Pass (Good)
- 60~74: Conditional Pass (개선 권고)
- 60 미만: Fail (재처리 필요)"""

TOOLS = [
    {
        "name": "load_all_agent_outputs",
        "description": "모든 에이전트가 생성한 출력물(페르소나, NBA, Activity)과 원본 데이터를 통합 로드합니다.",
        "input_schema": {
            "type": "object",
            "properties": {
                "customer_id": {"type": "string"}
            },
            "required": ["customer_id"],
        },
    },
    {
        "name": "save_qc_report",
        "description": "품질 검수 결과 보고서를 저장합니다.",
        "input_schema": {
            "type": "object",
            "properties": {
                "customer_id": {"type": "string"},
                "report": {
                    "type": "object",
                    "description": """QC 보고서 구조:
{
  overall_score: 0~100 (int),
  verdict: "pass_excellent" | "pass_good" | "conditional_pass" | "fail",
  persona_review: {score, strengths: [], issues: [], recommendations: []},
  nba_review: {score, strengths: [], issues: [], recommendations: []},
  activity_review: {score, strengths: [], issues: [], recommendations: []},
  consistency_review: {score, issues: []},
  critical_issues: [{severity: "critical|major|minor", description, fix}],
  overall_summary: string,
  reprocess_required: bool
}""",
                },
            },
            "required": ["customer_id", "report"],
        },
    },
]


class QCAgent(BaseAgent):
    def __init__(self, model: str = None, provider: str = "anthropic"):
        super().__init__(
            name="QCAgent",
            model=model or MODEL,
            system_prompt=SYSTEM_PROMPT,
            tools=TOOLS,
            provider=provider,
        )

    def execute_tool(self, tool_name: str, tool_input: dict) -> dict:
        if tool_name == "load_all_agent_outputs":
            return dt.build_full_context(tool_input["customer_id"])

        if tool_name == "save_qc_report":
            dt.save_qc_report(tool_input["customer_id"], tool_input["report"])
            return {
                "status": "saved",
                "verdict": tool_input["report"].get("verdict"),
                "score": tool_input["report"].get("overall_score"),
            }

        return {"error": f"알 수 없는 도구: {tool_name}"}

    def run(self, customer_id: str) -> str:
        prompt = f"""고객 ID {customer_id}의 전체 CRM 에이전트 출력물을 품질 검수해주세요.

단계:
1. load_all_agent_outputs 도구로 모든 에이전트 결과를 로드하세요
2. 페르소나, NBA, Activity를 각각 독립적으로 평가하세요
3. 3개 출력물 간의 일관성을 교차 검증하세요
4. save_qc_report 도구로 검수 결과를 저장하세요
5. 판정 결과와 가장 중요한 개선 사항 3가지를 요약하세요

검수 시 특히 확인할 것:
- NBA의 avoid_actions와 Activity의 실행 내용이 충돌하지 않는가?
- Activity의 due_date가 NBA의 urgency와 일치하는가?
- 페르소나의 sector_interests가 NBA 추천에 반영되었는가?
- 모든 기한 초과 미완료 액션이 NBA/Activity에서 처리되었는가?"""
        return super().run(prompt)
