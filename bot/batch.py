#!/usr/bin/env python3
"""Weekly batch: 새 소스 파일을 읽고 Claude로 wiki를 업데이트한다."""

import os
import re
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from github_client import GitHubClient
from claude_client import ClaudeClient
from validator import validate_ingest_result

LAST_BATCH_FILE = "schema/last_batch_at.txt"


# ─── Wiki 필터링 ──────────────────────────────────────────

def extract_keywords(text: str) -> set[str]:
    keywords = set()
    keywords.update(re.findall(r'#([가-힣a-zA-Z0-9_]+)', text))
    keywords.update(re.findall(r'\b[A-Z][A-Za-z0-9]{1,}\b', text))
    keywords.update(re.findall(r'[가-힣]{2,}', text))
    return keywords


def filter_wiki_files(source_text: str, wiki_files: dict[str, str], top_n: int = 15) -> dict[str, str]:
    if len(wiki_files) <= top_n:
        return wiki_files
    keywords = extract_keywords(source_text)
    if not keywords:
        return dict(list(wiki_files.items())[:top_n])
    scores: dict[str, int] = {}
    for path, content in wiki_files.items():
        score = 0
        filename = path.rsplit('/', 1)[-1].replace('.md', '')
        for kw in keywords:
            if not kw:
                continue
            if kw in filename or filename in kw:
                score += 5
            if kw in path:
                score += 2
            if len(kw) >= 2 and kw in content[:300]:
                score += 1
        if score > 0:
            scores[path] = score
    sorted_paths = sorted(scores, key=lambda p: scores[p], reverse=True)
    return {p: wiki_files[p] for p in sorted_paths[:top_n]}


# ─── 새 소스 탐색 ─────────────────────────────────────────

def get_new_source_paths(since_iso: str) -> list[Path]:
    """since 이후에 커밋된 sources/ 하위 .md 파일 목록."""
    result = subprocess.run(
        ["git", "log", f"--since={since_iso}", "--name-only",
         "--pretty=format:", "--diff-filter=A", "--", "sources/"],
        capture_output=True, text=True,
    )
    paths = []
    seen = set()
    for line in result.stdout.split('\n'):
        line = line.strip()
        if line.endswith('.md') and line not in seen:
            seen.add(line)
            p = Path(line)
            if p.exists():
                paths.append(p)
    return paths


# ─── 배치 실행 ────────────────────────────────────────────

def main():
    github = GitHubClient(os.environ["GITHUB_TOKEN"], os.environ["GITHUB_REPO"])
    claude_client = ClaudeClient(os.environ["ANTHROPIC_API_KEY"])

    last_batch = (github.get_file(LAST_BATCH_FILE) or "").strip() or "1970-01-01T00:00:00+00:00"
    print(f"마지막 배치: {last_batch}")

    new_sources = get_new_source_paths(last_batch)
    if not new_sources:
        print("새 소스 없음. 종료.")
        return

    print(f"새 소스 {len(new_sources)}개 처리 시작\n")

    schema = github.get_file("schema/SCHEMA.md")
    ontology_str = github.get_file("schema/ontology.json")
    ontology = json.loads(ontology_str) if ontology_str else {}

    success = 0
    for source_path in new_sources:
        source_text = source_path.read_text(encoding='utf-8')
        print(f"[{source_path}]")

        try:
            wiki_files = github.get_wiki_files()
            relevant_wiki = filter_wiki_files(source_text, wiki_files)
            print(f"  wiki 필터링: {len(wiki_files)}개 → {len(relevant_wiki)}개")

            date_str = datetime.now().strftime("%Y%m%d_%H%M")
            result = claude_client.ingest(
                source_text=source_text,
                date_str=date_str,
                wiki_files=relevant_wiki,
                schema=schema,
                ontology=ontology_str,
            )

            errors = validate_ingest_result(result, ontology, {})
            if errors:
                print(f"  ⚠️ 검증 경고: {errors}")

            wiki_updates = {
                item["path"]: item["content"]
                for item in result.get("wiki_updates", [])
            }
            if wiki_updates:
                title = result.get("title", source_path.stem)
                github.commit_files(wiki_updates, f"wiki: {title}")
                print(f"  ✅ wiki {len(wiki_updates)}개 업데이트")
            else:
                print("  wiki 업데이트 없음")

            success += 1

        except Exception as e:
            print(f"  ❌ 실패: {e}")

    now_iso = datetime.now(timezone.utc).isoformat()
    github.commit_files({LAST_BATCH_FILE: now_iso}, "chore: update last_batch_at")
    print(f"\n배치 완료: {success}/{len(new_sources)} 성공 ({now_iso})")


if __name__ == "__main__":
    main()
