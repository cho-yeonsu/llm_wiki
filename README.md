# 투자 LLM Wiki

개인 투자 지식을 복리로 축적하는 마크다운 기반 지식베이스.
새 자료를 추가할 때마다 LLM이 위키를 업데이트하고 교차참조를 유지한다.

## 빠른 시작

### 소스 추가 (Ingest)
1. `sources/articles/`, `sources/earnings/`, `sources/notes/` 중 적절한 곳에 파일 저장
2. Claude Code에서 실행:
```
sources/articles/[파일명]을 읽고, schema/SCHEMA.md 규칙에 따라
관련 wiki 페이지들을 업데이트해줘.
```

### 질문 (Query)
```
wiki/ 파일들을 읽고, [질문]에 대해 출처 페이지와 함께 답해줘.
```

### 유지보수 (Lint)
```
wiki/ 전체를 스캔해서 모순, 고아 페이지, 깨진 링크를 찾아줘.
```

## 구조
```
sources/    # 원본 자료 (수정 금지)
wiki/       # LLM이 관리하는 지식 페이지
schema/     # 운영 규칙 (SCHEMA.md)
```

자세한 규칙은 [schema/SCHEMA.md](schema/SCHEMA.md) 참고.
