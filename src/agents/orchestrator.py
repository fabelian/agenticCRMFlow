"""
Orchestrator Agent
LLM이 직접 하위 에이전트 호출 순서와 흐름을 결정하는 멀티에이전트 조율자
각 하위 에이전트를 tool로 등록하여 Claude가 자율적으로 파이프라인을 구성
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from agents.base_agent import BaseAgent
from agents.persona_agent import PersonaAgent
from agents.nba_agent import NBAAgent
from agents.activity_agent import ActivityAgent
from agents.qc_agent import QCAgent
from tools import data_tools as dt

MODEL = "claude-opus-4-6"

SYSTEM_PROMPT = """당신은 증권사 CRM 멀티에이전트 시스템의 오케스트레이터입니다.

역할:
- 사용자 요청을 분석하여 어떤 하위 에이전트를 어떤 순서로 실행할지 결정
- 각 에이전트의 결과를 확인하고 다음 단계를 판단
- 전체 파이프라인 완료 후 최종 종합 보고서를 작성

사용 가능한 에이전트 (tool):
1. run_persona_agent   → 고객 성향 분석 및 페르소나 생성
2. run_nba_agent       → Next Best Action 추천 생성
3. run_activity_agent  → Activity 일정 생성
4. run_qc_agent        → 전체 출력물 품질 검수

표준 실행 순서: Persona → NBA → Activity → QC
단, QC가 fail을 반환하면 해당 에이전트를 재실행할 수 있음

최종 보고서 형식:
- 고객 핵심 현황 (2~3문장)
- 즉시 실행 Top 3 액션
- QC 점수 및 판정
- 담당 영업에게 전달할 핵심 메시지"""

TOOLS = [
    {
        "name": "run_persona_agent",
        "description": "고객 페르소나 분석 에이전트를 실행합니다. 세일즈 노트와 액션플랜을 분석하여 구조화된 고객 성향 프로필을 생성·저장합니다.",
        "input_schema": {
            "type": "object",
            "properties": {
                "customer_id": {"type": "string", "description": "분석할 고객 ID"},
            },
            "required": ["customer_id"],
        },
    },
    {
        "name": "run_nba_agent",
        "description": "Next Best Action 추천 에이전트를 실행합니다. 저장된 페르소나를 기반으로 우선순위별 영업 행동을 추천·저장합니다. Persona Agent 실행 후 호출하세요.",
        "input_schema": {
            "type": "object",
            "properties": {
                "customer_id": {"type": "string"},
            },
            "required": ["customer_id"],
        },
    },
    {
        "name": "run_activity_agent",
        "description": "Activity 일정 관리 에이전트를 실행합니다. NBA 추천을 구체적인 실행 일정으로 변환·저장합니다. NBA Agent 실행 후 호출하세요.",
        "input_schema": {
            "type": "object",
            "properties": {
                "customer_id": {"type": "string"},
            },
            "required": ["customer_id"],
        },
    },
    {
        "name": "run_qc_agent",
        "description": "품질 검수 에이전트를 실행합니다. 모든 에이전트 출력물을 검수하고 Pass/Fail 판정과 점수를 반환합니다. 마지막 단계에서 호출하세요.",
        "input_schema": {
            "type": "object",
            "properties": {
                "customer_id": {"type": "string"},
            },
            "required": ["customer_id"],
        },
    },
    {
        "name": "get_customer_info",
        "description": "고객 기본 정보를 조회합니다. 분석 시작 전 고객 존재 여부 확인에 사용합니다.",
        "input_schema": {
            "type": "object",
            "properties": {
                "customer_id": {"type": "string"},
            },
            "required": ["customer_id"],
        },
    },
]

DIVIDER = "=" * 60


class OrchestratorAgent(BaseAgent):
    def __init__(self, model: str = None, provider: str = "anthropic"):
        super().__init__(
            name="Orchestrator",
            model=model or MODEL,
            system_prompt=SYSTEM_PROMPT,
            tools=TOOLS,
            provider=provider,
        )
        self._sub_model = model or MODEL
        self._sub_provider = provider
        # 하위 에이전트 레지스트리 (지연 초기화)
        self._agents: dict = {}

    def _get_agent(self, name: str):
        if name not in self._agents:
            mapping = {
                "persona": PersonaAgent,
                "nba": NBAAgent,
                "activity": ActivityAgent,
                "qc": QCAgent,
            }
            self._agents[name] = mapping[name](
                model=self._sub_model,
                provider=self._sub_provider,
            )
        return self._agents[name]

    def execute_tool(self, tool_name: str, tool_input: dict) -> dict:
        customer_id = tool_input["customer_id"]

        if tool_name == "get_customer_info":
            customer = dt.get_customer(customer_id)
            if not customer:
                return {"error": f"고객 ID '{customer_id}'를 찾을 수 없습니다."}
            return customer

        agent_map = {
            "run_persona_agent": "persona",
            "run_nba_agent": "nba",
            "run_activity_agent": "activity",
            "run_qc_agent": "qc",
        }

        if tool_name in agent_map:
            label = tool_name.replace("run_", "").replace("_agent", "").upper()
            print(f"\n{DIVIDER}")
            print(f"  서브에이전트 실행: {label} Agent → 고객 {customer_id}")
            print(DIVIDER)
            agent = self._get_agent(agent_map[tool_name])
            result_text = agent.run(customer_id)
            print(f"{DIVIDER}")
            return {
                "status": "completed",
                "agent": tool_name,
                "customer_id": customer_id,
                "summary": result_text[:500] if result_text else "(결과 없음)",
            }

        return {"error": f"알 수 없는 도구: {tool_name}"}

    def run(self, customer_id: str, task: str | None = None) -> str:
        if task is None:
            task = f"고객 ID {customer_id}에 대한 전체 CRM 분석을 수행하고 최종 보고서를 작성해주세요."

        prompt = f"""{task}

분석 대상 고객 ID: {customer_id}

지침:
1. 먼저 get_customer_info로 고객 정보를 확인하세요
2. 표준 순서(Persona → NBA → Activity → QC)로 에이전트를 실행하세요
3. 각 에이전트 완료 후 결과를 확인하고 다음 단계 진행 여부를 판단하세요
4. QC 결과가 fail이면 문제 에이전트를 재실행하세요 (최대 1회)
5. 모든 에이전트 완료 후 담당 영업자를 위한 최종 종합 보고서를 작성하세요"""

        print(f"\n{'#'*60}")
        print(f"  ORCHESTRATOR 시작")
        print(f"  고객: {customer_id} | 작업: {task[:50]}...")
        print(f"{'#'*60}\n")

        result = super().run(prompt)

        # 최종 결과를 파일로도 저장
        output_dir = Path(__file__).parent.parent.parent / "output"
        output_dir.mkdir(exist_ok=True)
        from datetime import datetime
        ts = datetime.now().strftime("%Y%m%d_%H%M")
        out_path = output_dir / f"orchestrator_{customer_id}_{ts}.md"
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(f"# CRM 멀티에이전트 분석 결과\n")
            f.write(f"고객 ID: {customer_id} | 생성: {ts}\n\n")
            f.write(result)
        print(f"\n  최종 보고서 저장: {out_path}")
        return result
