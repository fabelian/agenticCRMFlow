"""
OpenRouter 클라이언트 팩토리
- OPENROUTER_API_KEY 환경변수에서 키를 읽어 OpenAI 호환 클라이언트를 반환
- 직접 사용: get_client()
- 스트리밍: get_client().chat.completions.create(..., stream=True)
"""

import os
from openai import OpenAI

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


def get_client() -> OpenAI:
    """
    OpenRouter 클라이언트를 반환.
    OPENROUTER_API_KEY가 없으면 즉시 오류를 발생시켜 키 누락을 명확히 알림.
    """
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise EnvironmentError(
            "OPENROUTER_API_KEY가 설정되지 않았습니다. "
            ".env 파일에 OPENROUTER_API_KEY=sk-or-... 를 추가하세요."
        )
    return OpenAI(
        api_key=api_key,
        base_url=OPENROUTER_BASE_URL,
    )


def chat(model: str, messages: list, **kwargs) -> str:
    """
    단순 텍스트 완성 헬퍼.

    사용 예:
        from tools.openrouter_client import chat
        reply = chat("openai/gpt-4o", [{"role": "user", "content": "안녕"}])
    """
    client = get_client()
    response = client.chat.completions.create(
        model=model,
        messages=messages,
        **kwargs,
    )
    return response.choices[0].message.content
