import os
import asyncio
from datetime import datetime
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes
from fastapi import FastAPI, HTTPException, Header
from pydantic import BaseModel
import uvicorn

from github_client import GitHubClient
from claude_client import ClaudeClient

TELEGRAM_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "")

github = GitHubClient(os.environ["GITHUB_TOKEN"], os.environ["GITHUB_REPO"])
claude = ClaudeClient(os.environ["ANTHROPIC_API_KEY"])

# ─── FastAPI 앱 ────────────────────────────────────────────

api = FastAPI()


class IngestRequest(BaseModel):
    content: str
    url: str = ""
    title: str = ""


async def run_ingest(text: str) -> dict:
    date_str = datetime.now().strftime("%Y%m%d_%H%M")
    wiki_files = github.get_wiki_files()
    schema = github.get_file("schema/SCHEMA.md")

    result = claude.ingest(
        source_text=text,
        date_str=date_str,
        wiki_files=wiki_files,
        schema=schema,
    )

    files_to_commit = {result["source_file"]["path"]: result["source_file"]["content"]}
    for update_item in result.get("wiki_updates", []):
        files_to_commit[update_item["path"]] = update_item["content"]

    commit_msg = f"ingest: {result.get('title', date_str)}"
    github.commit_files(files_to_commit, commit_msg)

    updated = list(files_to_commit.keys())
    print(f"✅ {len(updated)}개 파일 커밋: {updated}")
    return {"title": result.get("title"), "files": updated}


@api.get("/health")
async def health():
    return {"ok": True}


@api.post("/ingest")
async def webhook_ingest(req: IngestRequest, authorization: str = Header(None)):
    if WEBHOOK_SECRET and authorization != f"Bearer {WEBHOOK_SECRET}":
        raise HTTPException(status_code=401, detail="Unauthorized")

    # 제목 + URL + 본문을 하나의 텍스트로 구성
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
        result = await run_ingest(full_text)
        return {"ok": True, **result}
    except Exception as e:
        print(f"❌ 처리 실패: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ─── Telegram 핸들러 ───────────────────────────────────────

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.channel_post or update.message
    if not message:
        return

    text = message.text or message.caption or ""
    if not text.strip():
        return

    date_str = datetime.now().strftime("%Y%m%d_%H%M")
    print(f"[{date_str}] 텔레그램 메시지: {text[:80]}...")

    try:
        await run_ingest(text)
    except Exception as e:
        print(f"❌ 처리 실패: {e}")
        raise


# ─── 동시 실행 ─────────────────────────────────────────────

async def run_telegram(tg_app):
    async with tg_app:
        await tg_app.start()
        await tg_app.updater.start_polling(allowed_updates=["channel_post", "message"])
        print("🤖 텔레그램 봇 시작...")
        await asyncio.Event().wait()  # 영구 대기


async def run_fastapi():
    port = int(os.environ.get("PORT", 8000))
    config = uvicorn.Config(api, host="0.0.0.0", port=port, log_level="info")
    server = uvicorn.Server(config)
    print(f"🌐 웹훅 서버 시작 (port {port})...")
    await server.serve()


async def main_async():
    tg_app = Application.builder().token(TELEGRAM_TOKEN).build()
    tg_app.add_handler(MessageHandler(filters.ALL, handle_message))
    await asyncio.gather(run_fastapi(), run_telegram(tg_app))


if __name__ == "__main__":
    asyncio.run(main_async())
