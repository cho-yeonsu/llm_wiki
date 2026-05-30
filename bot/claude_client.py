import json
import anthropic

SYSTEM_PROMPT = """당신은 투자 지식베이스 위키를 관리하는 AI입니다.

새로운 투자 관련 소스가 주어지면:
1. 소스의 핵심 내용을 파악한다
2. 관련 기존 wiki 페이지를 업데이트한다 (최대 10개)
3. 새로운 개념/종목/섹터/거시 주제가 있으면 새 페이지를 만든다
4. 모든 고유명사(종목, 인물, 기관, 지표)는 [[위키링크]] 형식으로 연결한다
5. 기존 페이지와 상충되는 내용은 덮어쓰지 말고 "이견" 섹션에 병기한다

반드시 아래 JSON 형식으로만 응답한다. 다른 텍스트는 포함하지 않는다:
{
  "title": "소스 제목 (간결하게, 한국어 OK)",
  "source_file": {
    "path": "sources/articles/[YYYYMMDD_HHMM]_[title].md",
    "content": "# [제목]\\n\\n**수집일**: [날짜]\\n\\n[원문 내용]"
  },
  "wiki_updates": [
    {
      "path": "wiki/[companies|sectors|concepts|macro]/[이름].md",
      "content": "[SCHEMA 형식에 맞는 전체 파일 내용]"
    }
  ]
}
"""


class ClaudeClient:
    def __init__(self, api_key: str):
        self.client = anthropic.Anthropic(api_key=api_key)

    def ingest(
        self,
        source_text: str,
        date_str: str,
        wiki_files: dict[str, str],
        schema: str,
    ) -> dict:
        wiki_context = "\n\n".join(
            f"--- {path} ---\n{content}" for path, content in wiki_files.items()
        )

        user_message = f"""SCHEMA 규칙:
{schema}

현재 위키 파일들:
{wiki_context or "(비어있음 — 새로 시작하는 위키입니다)"}

---
새 소스 (날짜: {date_str}):
{source_text}

위 소스를 wiki에 통합해줘. JSON으로만 응답해."""

        response = self.client.messages.create(
            model="claude-opus-4-8",
            max_tokens=8000,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_message}],
        )

        text = response.content[0].text.strip()

        # JSON 블록 추출 (```json ... ``` 감싸진 경우도 처리)
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]

        start = text.find("{")
        end = text.rfind("}") + 1
        if start < 0 or end <= start:
            raise ValueError(f"JSON을 파싱할 수 없음: {text[:300]}")

        return json.loads(text[start:end])
