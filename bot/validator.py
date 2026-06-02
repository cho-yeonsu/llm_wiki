import re


def validate_ingest_result(result: dict, ontology: dict, routing: dict) -> list[str]:
    """검증 실패 이유 목록 반환. 빈 리스트면 통과."""
    errors = []

    valid_wiki_paths = [
        t["path"]
        for d in ontology.get("domains", {}).values()
        for t in d["types"].values()
    ]
    valid_source_paths = [
        d["source_base"] for d in ontology.get("domains", {}).values()
    ]
    required_fields = ontology.get("required_frontmatter", {}).get("fields", [])

    # source_file 경로 검증
    src_path = result.get("source_file", {}).get("path", "")
    if src_path:
        expected_src = routing.get("source_base")
        if expected_src and not src_path.startswith(expected_src):
            errors.append(f"소스 경로 오류: '{src_path}' — 예상 경로: '{expected_src}'")
        elif not expected_src and not any(src_path.startswith(p) for p in valid_source_paths):
            errors.append(f"소스 경로 오류: '{src_path}' — ontology에 없는 경로")

    is_free = routing.get("is_free", False)

    # wiki_updates 검증
    for item in result.get("wiki_updates", []):
        path = item.get("path", "")
        content = item.get("content", "")

        # 자유 라우팅은 경로 제약 없음 (새 도메인 폴더 허용)
        if not is_free and not any(path.startswith(p) for p in valid_wiki_paths):
            errors.append(f"경로 오류: '{path}' — ontology에 없는 wiki 경로")

        for field in required_fields:
            if f"**{field}**" not in content:
                errors.append(f"필드 누락: '{path}' — **{field}** 없음")

        # 내부 링크에 마크다운 형식 사용 여부 경고
        bad_links = re.findall(r'\[([^\]]+)\]\((?!../../sources|http)', content)
        if bad_links:
            errors.append(
                f"링크 형식 경고: '{path}' — [[]] 대신 []() 사용: {bad_links[:3]}"
            )

    return errors
