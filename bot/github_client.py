import base64
from github import Github, InputGitTreeElement


class GitHubClient:
    def __init__(self, token: str, repo_name: str):
        self.repo = Github(token).get_repo(repo_name)

    def get_file(self, path: str) -> str:
        try:
            content = self.repo.get_contents(path)
            return base64.b64decode(content.content).decode("utf-8")
        except Exception:
            return ""

    def get_wiki_files(self) -> dict[str, str]:
        """wiki/ 하위 모든 .md 파일을 {path: content} 로 반환"""
        files = {}
        try:
            stack = list(self.repo.get_contents("wiki"))
            while stack:
                item = stack.pop()
                if item.type == "dir":
                    stack.extend(self.repo.get_contents(item.path))
                elif item.name.endswith(".md"):
                    files[item.path] = base64.b64decode(item.content).decode("utf-8")
        except Exception:
            pass
        return files

    def get_source_file_paths(self) -> list[str]:
        """sources/ 하위 .md 파일 경로 목록을 git tree 1회 조회로 가져온다."""
        try:
            ref = self.repo.get_git_ref("heads/main")
            tree = self.repo.get_git_tree(ref.object.sha, recursive=True)
            return [
                item.path for item in tree.tree
                if item.path.startswith("sources/") and item.path.endswith(".md")
            ]
        except Exception:
            return []

    def commit_files(self, files: dict[str, str], commit_message: str):
        """여러 파일을 단일 커밋으로 저장"""
        ref = self.repo.get_git_ref("heads/main")
        base_sha = ref.object.sha
        base_tree = self.repo.get_git_tree(base_sha)

        elements = [
            InputGitTreeElement(
                path=path,
                mode="100644",
                type="blob",
                sha=self.repo.create_git_blob(content, "utf-8").sha,
            )
            for path, content in files.items()
        ]

        new_tree = self.repo.create_git_tree(elements, base_tree)
        parent = self.repo.get_git_commit(base_sha)
        commit = self.repo.create_git_commit(commit_message, new_tree, [parent])
        ref.edit(commit.sha)
