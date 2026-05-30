import os
import asyncio
from datetime import datetime
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes

from github_client import GitHubClient
from claude_client import ClaudeClient

TELEGRAM_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
github = GitHubClient(os.environ["GITHUB_TOKEN"], os.environ["GITHUB_REPO"])
claude = ClaudeClient(os.environ["ANTHROPIC_API_KEY"])


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.channel_post or update.message
    if not message:
        return

    text = message.text or message.caption or ""
    if not text.strip():
        return

    date_str = datetime.now().strftime("%Y%m%d_%H%M")
    print(f"[{date_str}] 새 메시지: {text[:80]}...")

    try:
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

    except Exception as e:
        print(f"❌ 처리 실패: {e}")
        raise


def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(MessageHandler(filters.ALL, handle_message))
    print("🤖 LLM Wiki 봇 시작 (polling)...")
    app.run_polling(allowed_updates=["channel_post", "message"])


if __name__ == "__main__":
    main()
