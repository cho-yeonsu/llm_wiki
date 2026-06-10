import os
import re
import json
import asyncio
from datetime import datetime
from fastapi import FastAPI, HTTPException, Header, Request
from pydantic import BaseModel
import uvicorn
import httpx
from bs4 import BeautifulSoup

from github_client import GitHubClient

TELEGRAM_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "")

github = GitHubClient(os.environ["GITHUB_TOKEN"], os.environ["GITHUB_REPO"])


# ─── URL / 파일 fetch ──────────────────────────────────────

_SUPPORTED_EXTS = {"pdf", "txt", "md"}
_SUPPORTED_MIMES = {"application/pdf", "text/plain", "text/markdown", "text/x-markdown"}


async def fetch_url_content(url: str) -> str:
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            resp = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
            resp.raise_for_status()
            content_type = resp.headers.get("content-type", "")
            if "text/html" in content_type:
                soup = BeautifulSoup(resp.text, "lxml")
                for tag in soup(["script", "style", "nav", "header", "footer"]):
                    tag.decompose()
                return soup.get_text(separator="\n", strip=True)
            return resp.text
    except Exception as e:
        return f"(URL 읽기 실패: {e})"


async def fetch_telegram_file_content(file_id: str, mime_type: str, file_name: str) -> str:
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getFile",
                params={"file_id": file_id},
            )
            resp.raise_for_status()
            file_path = resp.json()["result"]["file_path"]

            file_resp = await client.get(
                f"https://api.telegram.org/file/bot{TELEGRAM_TOKEN}/{file_path}"
            )
            file_resp.raise_for_status()
            data = file_resp.content

        ext = file_name.lower().rsplit(".", 1)[-1] if "." in file_name else ""
        if mime_type == "application/pdf" or ext == "pdf":
            import fitz
            doc = fitz.open(stream=data, filetype="pdf")
            return "\n".join(page.get_text() for page in doc)
        return data.decode("utf-8", errors="replace")
    except Exception as e:
        return f"(파일 읽기 실패: {e})"


# ─── 라우팅 ────────────────────────────────────────────────

def resolve_routing(chat_id: int, text: str, ontology: dict) -> dict:
    """채널 ID와 해시태그로 도메인·타입·경로를 결정한다."""
    channels = ontology.get("channels", {})
    channel_cfg = channels.get(str(chat_id), {})
    hashtag_list = re.findall(r'#[가-힣a-zA-Z0-9_]+', text)
    hashtags = set(hashtag_list)
    domains = ontology.get("domains", {})
    is_archive_channel = channel_cfg.get("auto_domain") is None and str(chat_id) in channels

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


# ─── 소스 저장 ─────────────────────────────────────────────

async def save_source(text: str, chat_id: int = 0) -> dict:
    """Claude 호출 없이 소스 파일만 저장한다. wiki 업데이트는 배치에서 처리."""
    date_str = datetime.now().strftime("%Y%m%d_%H%M")
    date_display = datetime.now().strftime("%Y-%m-%d")

    ontology_str = github.get_file("schema/ontology.json")
    ontology = json.loads(ontology_str) if ontology_str else {}
    routing = resolve_routing(chat_id, text, ontology)

    source_base = routing.get("source_base") or "sources/inbox/"

    title = date_str
    for line in text.split('\n'):
        line = line.strip().lstrip('#').strip()
        if line and not line.startswith('http') and len(line) > 1:
            title = line[:40]
            break

    safe_title = re.sub(r'[^\w가-힣]', '_', title)
    path = f"{source_base}{date_str}_{safe_title}.md"
    content = f"# {title}\n\n**수집일**: {date_display}\n\n{text}"

    github.commit_files({path: content}, f"source: {title}")
    print(f"✅ 소스 저장: {path}")
    return {"path": path, "title": title}


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
    document = message.get("document")

    supported_doc = None
    if document:
        mime_type = document.get("mime_type", "")
        file_name = document.get("file_name", "")
        ext = file_name.lower().rsplit(".", 1)[-1] if "." in file_name else ""
        if mime_type in _SUPPORTED_MIMES or ext in _SUPPORTED_EXTS:
            supported_doc = document

    entities = message.get("entities") or message.get("caption_entities") or []
    urls = []
    for entity in entities:
        if entity["type"] == "url":
            offset, length = entity["offset"], entity["length"]
            urls.append(text[offset:offset + length])
        elif entity["type"] == "text_link":
            urls.append(entity["url"])

    if not text.strip() and not supported_doc:
        return {"ok": True}

    chat_id = message["chat"]["id"]
    date_str = datetime.now().strftime("%Y%m%d_%H%M")
    print(f"[{date_str}] 텔레그램 웹훅 (chat_id={chat_id}): {text[:80]}...")

    async def safe_save():
        try:
            parts = []
            if text:
                parts.append(text)

            if supported_doc:
                file_name = supported_doc.get("file_name", "file")
                print(f"  파일 다운로드: {file_name}")
                file_content = await fetch_telegram_file_content(
                    supported_doc["file_id"],
                    supported_doc.get("mime_type", ""),
                    file_name,
                )
                parts.append(f"[첨부파일: {file_name}]\n{file_content}")

            for url in urls:
                print(f"  URL fetch: {url}")
                url_content = await fetch_url_content(url)
                parts.append(f"[링크 내용: {url}]\n{url_content}")

            full_text = "\n\n".join(parts)
            await save_source(full_text, chat_id=chat_id)
        except Exception as e:
            print(f"❌ 소스 저장 실패: {e}")

    asyncio.create_task(safe_save())
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
        result = await save_source(full_text, chat_id=0)
        return {"ok": True, **result}
    except Exception as e:
        print(f"❌ 저장 실패: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ─── 실행 ──────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    print(f"🌐 서버 시작 (port {port})...")
    uvicorn.run(api, host="0.0.0.0", port=port, log_level="info")
