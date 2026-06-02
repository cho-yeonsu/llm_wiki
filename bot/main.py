import os
import re
import json
import asyncio
from datetime import datetime
from telegram import Bot
from fastapi import FastAPI, HTTPException, Header, Request
from pydantic import BaseModel
import uvicorn

from github_client import GitHubClient
from claude_client import ClaudeClient
from validator import validate_ingest_result

TELEGRAM_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "")

github = GitHubClient(os.environ["GITHUB_TOKEN"], os.environ["GITHUB_REPO"])
claude = ClaudeClient(os.environ["ANTHROPIC_API_KEY"])
bot = Bot(token=TELEGRAM_TOKEN)


# ─── 라우팅 ────────────────────────────────────────────────

def resolve_routing(chat_id: int, text: str, ontology: dict) -> dict:
    """채널 ID와 해시태그로 도메인·타입·경로를 결정한다."""
    channels = ontology.get("channels", {})
    channel_cfg = channels.get(str(chat_id), {})
    hashtag_list = re.findall(r'#[가-힣a-zA-Z0-9_]+', text)  # 순서 보존
    hashtags = set(hashtag_list)
    domains = ontology.get("domains", {})
    is_archive_channel = channel_cfg.get("auto_domain") is None and str(chat_id) in channels

    # 1. 도메인: 채널 설정(auto_domain) 우선, 없으면 해시태그에서 읽기
    domain = channel_cfg.get("auto_domain")
    matched_domain_cfg = None

    if domain is None:
        for d_name, d_cfg in domains.items():
            if d_cfg["hashtag"] in hashtags:
                domain = d_name
                matched_domain_cfg = d_cfg
                break
    elif domain in domains:
        matched_domain_cfg = domains[domain]

    # 2. ontology에 정의된 도메인 → strict routing
    if matched_domain_cfg:
        node_type = None
        for t_name, t_cfg in matched_domain_cfg["types"].items():
            if t_cfg["hashtag"] in hashtags:
                node_type = t_name
                break

        if node_type is None:
            return {
                "domain": domain, "node_type": None, "path": None,
                "source_base": matched_domain_cfg["source_base"],
                "naming": None, "when": None, "is_free": False,
            }

        type_cfg = matched_domain_cfg["types"][node_type]
        return {
            "domain": domain, "node_type": node_type,
            "path": type_cfg["path"],
            "source_base": matched_domain_cfg["source_base"],
            "naming": type_cfg["naming"], "when": type_cfg["when"],
            "is_free": False,
        }

    # 3. 아카이빙 채널 + ontology에 없는 해시태그 → 자유 라우팅
    if is_archive_channel and hashtag_list:
        free_domain = hashtag_list[0].lstrip("#")
        free_hint = hashtag_list[1].lstrip("#") if len(hashtag_list) > 1 else None
        return {
            "domain": free_domain, "node_type": free_hint,
            "path": f"wiki/{free_domain}/",
            "source_base": f"sources/{free_domain}/",
            "naming": None, "when": None, "is_free": True,
        }

    return {"domain": None, "node_type": None, "path": None, "source_base": None,
            "naming": None, "when": None, "is_free": False}


# ─── Ingest ────────────────────────────────────────────────

async def run_ingest(text: str, chat_id: int = 0) -> dict:
    date_str = datetime.now().strftime("%Y%m%d_%H%M")

    wiki_files = github.get_wiki_files()
    schema = github.get_file("schema/SCHEMA.md")
    ontology_str = github.get_file("schema/ontology.json")
    ontology = json.loads(ontology_str) if ontology_str else {}

    routing = resolve_routing(chat_id, text, ontology)

    if routing.get("domain"):
        print(f"  라우팅: {routing['domain']} / {routing['node_type']} → {routing['path']}")
    else:
        print("  라우팅: 미결정 (LLM 자율 추론)")

    result = claude.ingest(
        source_text=text,
        date_str=date_str,
        wiki_files=wiki_files,
        schema=schema,
        ontology=ontology_str,
        routing=routing,
    )

    errors = validate_ingest_result(result, ontology, routing)
    if errors:
        print(f"⚠️ 검증 경고 {len(errors)}건:")
        for e in errors:
            print(f"  - {e}")

    files_to_commit = {result["source_file"]["path"]: result["source_file"]["content"]}
    for update_item in result.get("wiki_updates", []):
        files_to_commit[update_item["path"]] = update_item["content"]

    commit_msg = f"ingest: {result.get('title', date_str)}"
    github.commit_files(files_to_commit, commit_msg)

    updated = list(files_to_commit.keys())
    print(f"✅ {len(updated)}개 파일 커밋: {updated}")
    return {"title": result.get("title"), "files": updated}


# ─── FastAPI 앱 ────────────────────────────────────────────

api = FastAPI()


class IngestRequest(BaseModel):
    content: str
    url: str = ""
    title: str = ""


@api.get("/health")
async def health():
    return {"ok": True}


@api.post("/telegram")
async def telegram_webhook(request: Request):
    """텔레그램이 메시지를 직접 쏴주는 웹훅 엔드포인트."""
    data = await request.json()

    message = data.get("channel_post") or data.get("message")
    if not message:
        return {"ok": True}

    text = message.get("text") or message.get("caption") or ""
    if not text.strip():
        return {"ok": True}

    chat_id = message["chat"]["id"]
    date_str = datetime.now().strftime("%Y%m%d_%H%M")
    print(f"[{date_str}] 텔레그램 웹훅 (chat_id={chat_id}): {text[:80]}...")

    # 텔레그램은 5초 내 응답을 요구 → 백그라운드로 실행
    asyncio.create_task(run_ingest(text, chat_id=chat_id))
    return {"ok": True}


@api.post("/ingest")
async def webhook_ingest(req: IngestRequest, authorization: str = Header(None)):
    """iOS 단축어 등 외부 웹훅용 엔드포인트."""
    if WEBHOOK_SECRET and authorization != f"Bearer {WEBHOOK_SECRET}":
        raise HTTPException(status_code=401, detail="Unauthorized")

    parts = []
    if req.title:
        parts.append(f"제목: {req.title}")
    if req.url:
        parts.append(f"URL: {req.url}")
    if req.content:
        parts.append(req.content)
    full_text = "\n\n".join(parts)

    date_str = datetime.now().strftime("%Y%m%d_%H%M")
    print(f"[{date_str}] 웹훅 수신: {req.title or req.url or '(제목 없음)'}...")

    try:
        result = await run_ingest(full_text, chat_id=0)
        return {"ok": True, **result}
    except Exception as e:
        print(f"❌ 처리 실패: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ─── 실행 ──────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    print(f"🌐 서버 시작 (port {port})...")
    uvicorn.run(api, host="0.0.0.0", port=port, log_level="info")
