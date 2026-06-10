import json
import anthropic

SYSTEM_PROMPT = """당신은 투자 지식베이스 위키를 관리하는 AI입니다.

새로운 소스가 주어지면:
1. 소스의 핵심 내용을 파악한다
2. 관련 기존 wiki 페이지를 업데이트한다 (최대 10개)
3. 새로운 개념/종목/브랜드가 있으면 새 페이지를 만든다
4. 모든 고유명사(종목, 인물, 기관, 지표)는 [[위키링크]] 형식으로 연결한다
5. 기존 페이지와 상충되는 내용은 덮어쓰지 말고 "이견" 섹션에 병기한다
6. wiki 파일 경로는 반드시 ONTOLOGY의 node_types 경로를 사용한다. 임의 경로 생성 금지
7. 라우팅이 지정된 경우 source_file.path는 반드시 지정된 소스 경로로 시작해야 한다

반드시 아래 JSON 형식으로만 응답한다. 다른 텍스트는 포함하지 않는다:
{
  "title": "소스 제목 (간결하게, 한국어 OK)",
  "source_file": {
    "path": "sources/[도메인]/[YYYYMMDD_HHMM]_[title].md",
    "content": "# [제목]\\n\\n**수집일**: [날짜]\\n\\n[원문 내용]"
  },
  "wiki_updates": [
    {
      "path": "wiki/[경로]/[이름].md",
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
        ontology: str = "",
        routing: dict | None = None,
        codes_context: str = "",
    ) -> dict:
        wiki_context = "\n\n".join(
            f"--- {path} ---\n{content}" for path, content in wiki_files.items()
        )

        routing_block = ""
        if routing and routing.get("domain"):
            if routing.get("node_type"):
                routing_block = f"""
== 라우팅 (반드시 준수) ==
도메인: {routing['domain']}
노드타입: {routing['node_type']}
wiki 경로: {routing['path']}  ← 신규 페이지는 반드시 이 경로 아래에 생성
소스 경로: {routing['source_base']}  ← source_file.path는 반드시 이 경로로 시작
파일 명명: {routing['naming']}
분류 기준: {routing['when']}

"""
            elif routing.get("is_free"):
                hint = f" (힌트: {routing['node_type']})" if routing.get("node_type") else ""
                routing_block = f"""
== 라우팅 (반드시 준수) ==
도메인: {routing['domain']} (사용자 정의 신규 도메인)
wiki 경로: {routing['path']}  ← 신규 페이지는 반드시 이 경로 아래에 생성{hint}
소스 경로: {routing['source_base']}  ← source_file.path는 반드시 이 경로로 시작
참고: 이 도메인은 ONTOLOGY에 미정의된 자유 분류다. 내용에 맞게 파일명과 구조를 자유롭게 설계해도 된다.

"""
            else:
                routing_block = f"""
== 라우팅 (반드시 준수) ==
도메인: {routing['domain']}
노드타입: 내용을 보고 ONTOLOGY의 node_types 중 가장 적합한 것으로 결정
소스 경로: {routing['source_base']}  ← source_file.path는 반드시 이 경로로 시작

"""

        codes_block = f"""== 엔티티 표준명 (반드시 이 이름으로 [[위키링크]] 생성) ==
{codes_context}

""" if codes_context else ""

        user_message = f"""{codes_block}== ONTOLOGY (분류 체계, 반드시 준수) ==
{ontology or "(없음)"}

== SCHEMA 규칙 ==
{schema}
{routing_block}== 현재 위키 파일들 ==
{wiki_context or "(비어있음 — 새로 시작하는 위키입니다)"}

---
새 소스 (날짜: {date_str}):
{source_text}

위 소스를 wiki에 통합해줘. JSON으로만 응답해."""

        response = self.client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=8000,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_message}],
        )

        text = response.content[0].text.strip()

        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]

        start = text.find("{")
        end = text.rfind("}") + 1
        if start < 0 or end <= start:
            raise ValueError(f"JSON을 파싱할 수 없음: {text[:300]}")

        return json.loads(text[start:end])
