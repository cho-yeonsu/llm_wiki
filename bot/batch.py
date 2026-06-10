#!/usr/bin/env python3
"""Weekly batch: 새 소스 파일을 읽고 Claude로 wiki를 업데이트한다."""

import os
import re
import json
from datetime import datetime, timezone
from pathlib import Path

from github_client import GitHubClient
from claude_client import ClaudeClient
from validator import validate_ingest_result

PROCESSED_MARKER = "**wiki 반영**:"


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


# ─── 소스 탐색 / 마커 ─────────────────────────────────────

def get_unprocessed_source_paths() -> list[Path]:
    """PROCESSED_MARKER가 없는 sources/ 하위 .md 파일 목록."""
    paths = []
    for p in sorted(Path("sources").rglob("*.md")):
        if PROCESSED_MARKER not in p.read_text(encoding='utf-8'):
            paths.append(p)
    return paths


def add_processed_marker(content: str, date_str: str) -> str:
    """**수집일**: 줄 바로 다음에 **wiki 반영**: 줄을 삽입한다."""
    marker_line = f"{PROCESSED_MARKER} {date_str}"
    lines = content.split('\n')
    for i, line in enumerate(lines):
        if line.strip().startswith('**수집일**:'):
            lines.insert(i + 1, marker_line)
            return '\n'.join(lines)
    # **수집일** 줄이 없으면 두 번째 줄에 삽입
    lines.insert(1, marker_line)
    return '\n'.join(lines)


# ─── 배치 실행 ────────────────────────────────────────────

def main():
    github = GitHubClient(os.environ["GITHUB_TOKEN"], os.environ["GITHUB_REPO"])
    claude_client = ClaudeClient(os.environ["ANTHROPIC_API_KEY"])

    unprocessed = get_unprocessed_source_paths()
    if not unprocessed:
        print("미처리 소스 없음. 종료.")
        return

    print(f"미처리 소스 {len(unprocessed)}개 처리 시작\n")

    schema = github.get_file("schema/SCHEMA.md")
    ontology_str = github.get_file("schema/ontology.json")
    ontology = json.loads(ontology_str) if ontology_str else {}

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    success = 0

    for source_path in unprocessed:
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

            # 소스 파일에 처리 완료 마커 추가
            marked_source = add_processed_marker(source_text, today)
            files_to_commit = {**wiki_updates, str(source_path): marked_source}

            title = result.get("title", source_path.stem)
            github.commit_files(files_to_commit, f"wiki: {title}")
            print(f"  ✅ wiki {len(wiki_updates)}개 업데이트, 소스 마커 추가")

            success += 1

        except Exception as e:
            print(f"  ❌ 실패: {e}")

    print(f"\n배치 완료: {success}/{len(unprocessed)} 성공")


if __name__ == "__main__":
    main()
