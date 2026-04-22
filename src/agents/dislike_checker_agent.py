"""
DislikeCheckerAgent
선택된 세일즈 노트의 Action_Point가 해당 고객 페르소나의 explicit_dislikes에
해당하는지 판정하여 red_flag 결과를 반환한다.

- 입력: customer_id, explicit_dislikes(list[str]), notes(list[{note_id, action_point}])
- 출력: list[{note_id, is_red_flag, matched_dislike, reason}]
- 호출자는 고객별로 그룹화한 뒤 이 에이전트를 각 고객당 1회 실행하도록 설계 (호출 비용 최소화)
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from agents.base_agent import BaseAgent

MODEL = "claude-sonnet-4-6"

SYSTEM_PROMPT = """당신은 증권사 기관영업 CRM의 '레드 플래그 판정' 전문 에이전트입니다.

역할:
- 한 고객의 페르소나에 정리된 explicit_dislikes(명시적 불만/거부 항목) 리스트와
  영업 담당자의 세일즈 노트별 Action_Point를 비교하여,
  Action_Point가 해당 고객이 싫어한다고 명시한 패턴을 실제로 실행하려는 내용인지 판정합니다.

판정 원칙:
- 의미 기반(semantic) 매칭: 단어가 달라도 동일한 패턴을 가리키면 매칭으로 인정하세요.
  (예: explicit_dislike='단순 탑다운 분석' / Action_Point='매크로 변수 기반 섹터 배분 가이드' → 매칭)
- 근거가 모호하면 is_red_flag=false (보수적으로).
  애매한 경우 reason에 "모호함 — 보수적 판정" 정도로 간단히 기록.
- 하나의 Action_Point가 여러 dislike에 걸치면, 가장 명확히 매칭되는 하나의 항목만 matched_dislike에 기록.
- matched_dislike 값은 입력받은 explicit_dislikes 리스트의 항목 문자열을 그대로 복사해서 넣을 것.
- reason은 한 문장 (60자 이내). 왜 그렇게 판정했는지 간결히.
- is_red_flag=false인 경우 matched_dislike는 빈 문자열("").

반드시 save_red_flag_results 도구를 호출하여 결과를 저장한 뒤 종료하세요.
결과 외 장황한 분석은 생략하고 짧게 마무리하세요."""

TOOLS = [
    {
        "name": "save_red_flag_results",
        "description": "입력받은 각 세일즈 노트에 대해 레드 플래그 판정 결과를 저장합니다.",
        "input_schema": {
            "type": "object",
            "properties": {
                "results": {
                    "type": "array",
                    "description": "각 노트별 판정 결과 배열. 입력된 모든 note_id가 빠짐없이 포함되어야 함.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "note_id": {"type": "string"},
                            "is_red_flag": {"type": "boolean"},
                            "matched_dislike": {
                                "type": "string",
                                "description": "매칭된 explicit_dislike 항목 원문 (매칭 없으면 빈 문자열)",
                            },
                            "reason": {
                                "type": "string",
                                "description": "판정 근거 한 문장 (60자 이내)",
                            },
                        },
                        "required": ["note_id", "is_red_flag", "matched_dislike", "reason"],
                    },
                }
            },
            "required": ["results"],
        },
    }
]


class DislikeCheckerAgent(BaseAgent):
    def __init__(self, model: str = None, provider: str = "anthropic"):
        super().__init__(
            name="DislikeCheckerAgent",
            model=model or MODEL,
            system_prompt=SYSTEM_PROMPT,
            tools=TOOLS,
            provider=provider,
        )
        self._results: list[dict] = []

    def execute_tool(self, tool_name: str, tool_input: dict) -> dict:
        if tool_name == "save_red_flag_results":
            results = tool_input.get("results") or []
            # 정규화: 누락 필드 보정
            normalized = []
            for r in results:
                normalized.append({
                    "note_id": str(r.get("note_id") or ""),
                    "is_red_flag": bool(r.get("is_red_flag")),
                    "matched_dislike": str(r.get("matched_dislike") or ""),
                    "reason": str(r.get("reason") or ""),
                })
            self._results = normalized
            return {"status": "saved", "count": len(normalized)}
        return {"error": f"알 수 없는 도구: {tool_name}"}

    def check(self, customer_id: str, company_name: str, dislikes: list[str], notes: list[dict]) -> list[dict]:
        """
        notes: [{"note_id": "...", "action_point": "..."}]
        반환: [{"note_id", "is_red_flag", "matched_dislike", "reason"}]
        """
        self._results = []
        if not notes:
            return []
        if not dislikes:
            # explicit_dislikes 비어있으면 LLM 호출 생략
            return [
                {
                    "note_id": n.get("note_id", ""),
                    "is_red_flag": False,
                    "matched_dislike": "",
                    "reason": "페르소나에 explicit_dislikes 항목 없음",
                }
                for n in notes
            ]

        dislikes_block = "\n".join(f"- {d}" for d in dislikes)
        notes_block = json.dumps(
            [{"note_id": n.get("note_id", ""), "action_point": n.get("action_point", "")} for n in notes],
            ensure_ascii=False,
            indent=2,
        )
        prompt = f"""아래 고객의 explicit_dislikes와 여러 세일즈 노트의 Action_Point를 비교하여 레드 플래그를 판정하세요.

고객 ID: {customer_id}
고객사: {company_name}

[explicit_dislikes — 고객이 명시적으로 불만/거부를 표현한 항목]
{dislikes_block}

[세일즈 노트 Action_Point 목록]
{notes_block}

모든 note_id에 대해 판정 결과를 save_red_flag_results 도구로 저장하세요.
출력 JSON 외 추가 설명은 최소화하세요."""

        super().run(prompt)
        # 안전망: LLM이 일부 note_id를 빠뜨렸거나 호출하지 않은 경우 기본값 보강
        returned = {r["note_id"]: r for r in self._results}
        final = []
        for n in notes:
            nid = n.get("note_id", "")
            if nid in returned:
                final.append(returned[nid])
            else:
                final.append({
                    "note_id": nid,
                    "is_red_flag": False,
                    "matched_dislike": "",
                    "reason": "에이전트 응답 누락 — 기본 false",
                })
        return final
