"""
BaseAgent: 모든 에이전트의 공통 Agentic Loop 구현
- provider="anthropic" : Anthropic SDK (tool_use 패턴)
- provider="openrouter": OpenRouter OpenAI 호환 API (function calling 패턴)
"""

import json
import sys
import io
import time
from anthropic import Anthropic

if sys.stdout.encoding != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
if sys.stderr.encoding != "utf-8":
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")


class BaseAgent:
    """
    Agentic Loop를 구현하는 베이스 클래스.
    하위 클래스는 execute_tool()만 구현하면 됨.
    """

    def __init__(
        self,
        name: str,
        model: str,
        system_prompt: str,
        tools: list,
        provider: str = "anthropic",
    ):
        self.name = name
        self.model = model
        self.system_prompt = system_prompt
        self.tools = tools
        self.provider = provider

        if provider == "anthropic":
            self.client = Anthropic()
        else:
            from tools.openrouter_client import get_client
            self.client = get_client()

    def execute_tool(self, tool_name: str, tool_input: dict) -> dict:
        """도구 실행 — 하위 클래스에서 반드시 구현"""
        raise NotImplementedError(f"{self.name}: execute_tool() 미구현 — {tool_name}")

    def _log(self, msg: str) -> None:
        print(f"  [{self.name}] {msg}", flush=True)

    def _to_openai_tools(self) -> list:
        """Anthropic input_schema 형식 → OpenAI function calling 형식 변환"""
        return [
            {
                "type": "function",
                "function": {
                    "name": tool["name"],
                    "description": tool.get("description", ""),
                    "parameters": tool.get("input_schema", {"type": "object", "properties": {}}),
                },
            }
            for tool in self.tools
        ]

    def run(self, prompt: str) -> str:
        if self.provider == "anthropic":
            return self._run_anthropic(prompt)
        return self._run_openrouter(prompt)

    # ── Anthropic SDK 루프 ──────────────────────────────────────────────────

    def _run_anthropic(self, prompt: str) -> str:
        messages = [{"role": "user", "content": prompt}]
        self._log("시작")

        kwargs = dict(
            model=self.model,
            max_tokens=16000,
            system=self.system_prompt,
            messages=messages,
        )
        if self.tools:
            kwargs["tools"] = self.tools

        collected_text = []
        max_continuations = 5
        continuation_count = 0

        while True:
            response = self.client.messages.create(**kwargs)

            text_blocks = [b for b in response.content if b.type == "text"]
            tool_blocks = [b for b in response.content if b.type == "tool_use"]

            for tb in text_blocks:
                if tb.text.strip():
                    print(tb.text, flush=True)
                    collected_text.append(tb.text)

            if response.stop_reason == "end_turn":
                self._log("완료")
                return " ".join(collected_text).strip()

            if response.stop_reason == "max_tokens":
                continuation_count += 1
                if continuation_count >= max_continuations:
                    self._log(f"max_tokens 한도({max_continuations}회) 도달 — 루프 종료")
                    break
                self._log(f"max_tokens 도달 — 계속 생성 요청 ({continuation_count}/{max_continuations})")
                messages.append({"role": "assistant", "content": response.content})
                messages.append({"role": "user", "content": "계속 작성해주세요. 중단된 부분부터 이어서 완성해주세요."})
                kwargs["messages"] = messages
                continue

            if response.stop_reason == "tool_use" and tool_blocks:
                tool_results = []
                for block in tool_blocks:
                    self._log(f"도구 호출: {block.name}({list(block.input.keys())})")
                    try:
                        result = self.execute_tool(block.name, block.input)
                        content = json.dumps(result, ensure_ascii=False, indent=2)
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": content,
                        })
                    except Exception as e:
                        import traceback
                        error_msg = f"{type(e).__name__}: {str(e)}"
                        error_trace = traceback.format_exc()
                        self._log(f"도구 실행 오류: {error_msg}")
                        self._log(f"스택 트레이스:\n{error_trace}")
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": json.dumps({"error": error_msg, "traceback": error_trace}, ensure_ascii=False),
                            "is_error": True,
                        })

                messages.append({"role": "assistant", "content": response.content})
                messages.append({"role": "user", "content": tool_results})
                kwargs["messages"] = messages
            else:
                self._log(f"예기치 않은 stop_reason: {response.stop_reason}")
                break

        return " ".join(collected_text).strip()

    # ── OpenRouter / OpenAI 호환 루프 ──────────────────────────────────────

    def _openrouter_create_with_retry(self, kwargs: dict, max_retries: int = 4):
        """429 rate limit 시 지수 백오프로 재시도"""
        from openai import RateLimitError
        for attempt in range(max_retries):
            try:
                return self.client.chat.completions.create(**kwargs)
            except RateLimitError as e:
                if attempt < max_retries - 1:
                    wait = 5 * (2 ** attempt)  # 5s → 10s → 20s → 40s
                    self._log(f"429 rate limit — {wait}초 후 재시도 ({attempt + 1}/{max_retries})")
                    time.sleep(wait)
                else:
                    raise RuntimeError(
                        f"OpenRouter rate limit 초과 (모델: {self.model}). "
                        "잠시 후 다시 시도하거나 다른 모델을 선택하세요."
                    ) from e

    def _run_openrouter(self, prompt: str) -> str:
        # 시스템 메시지를 첫 번째 메시지로 포함
        messages = []
        if self.system_prompt:
            messages.append({"role": "system", "content": self.system_prompt})
        messages.append({"role": "user", "content": prompt})
        self._log(f"시작 (OpenRouter · {self.model})")

        openai_tools = self._to_openai_tools() if self.tools else None
        collected_text = []
        max_continuations = 5
        continuation_count = 0

        while True:
            kwargs = dict(model=self.model, messages=messages)
            if openai_tools:
                kwargs["tools"] = openai_tools

            response = self._openrouter_create_with_retry(kwargs)
            # OpenRouter는 업스트림 오류 시 HTTP 200 + {"error": {...}} 본문을 반환하므로
            # choices가 None/빈값일 수 있음. OpenAI SDK는 이를 예외로 전환하지 않음.
            if response is None or not getattr(response, "choices", None):
                error_info = None
                err = getattr(response, "error", None)
                if err is not None:
                    error_info = err if isinstance(err, dict) else getattr(err, "model_dump", lambda: str(err))()
                elif response is not None and hasattr(response, "model_dump"):
                    dumped = response.model_dump(exclude_none=True)
                    error_info = dumped.get("error") or dumped
                raise RuntimeError(
                    f"OpenRouter 응답에 choices가 없습니다 (모델: {self.model}). "
                    f"업스트림 오류로 추정됩니다 — 잠시 후 재시도하거나 다른 모델을 선택하세요. "
                    f"details={error_info}"
                )
            choice = response.choices[0]
            msg = choice.message
            finish_reason = choice.finish_reason

            if msg.content:
                print(msg.content, flush=True)
                collected_text.append(msg.content)

            if finish_reason == "stop":
                self._log("완료")
                return " ".join(collected_text).strip()

            if finish_reason == "length":
                continuation_count += 1
                if continuation_count >= max_continuations:
                    self._log(f"length 한도({max_continuations}회) 도달 — 루프 종료")
                    break
                self._log(f"length 도달 — 계속 생성 ({continuation_count}/{max_continuations})")
                messages.append({"role": "assistant", "content": msg.content or ""})
                messages.append({"role": "user", "content": "계속 작성해주세요. 중단된 부분부터 이어서 완성해주세요."})
                continue

            if finish_reason == "tool_calls" and msg.tool_calls:
                # assistant 메시지 추가 (tool_calls 포함)
                messages.append({
                    "role": "assistant",
                    "content": msg.content,
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.function.name,
                                "arguments": tc.function.arguments,
                            },
                        }
                        for tc in msg.tool_calls
                    ],
                })

                # 각 도구 실행 후 결과 추가
                for tc in msg.tool_calls:
                    self._log(f"도구 호출: {tc.function.name}")
                    try:
                        tool_input = json.loads(tc.function.arguments)
                        result = self.execute_tool(tc.function.name, tool_input)
                        content = json.dumps(result, ensure_ascii=False, indent=2)
                    except Exception as e:
                        import traceback
                        error_msg = f"{type(e).__name__}: {str(e)}"
                        error_trace = traceback.format_exc()
                        self._log(f"도구 실행 오류: {error_msg}")
                        self._log(f"스택 트레이스:\n{error_trace}")
                        content = json.dumps({"error": error_msg, "traceback": error_trace}, ensure_ascii=False)

                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": content,
                    })
            else:
                self._log(f"예기치 않은 finish_reason: {finish_reason}")
                break

        return " ".join(collected_text).strip()
