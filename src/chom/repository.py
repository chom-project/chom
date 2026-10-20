"""Manage GitHub-hosted .cho formula repositories."""

from __future__ import annotations

import json
import hashlib
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from .formula import FormulaError, parse_formula


DEFAULT_REPOSITORY = {"name": "chom-project", "url": "https://github.com/chom-project/repo", "branch": None}


class RepositoryError(RuntimeError):
    """A formula repository could not be used."""


def _paths() -> tuple[Path, Path]:
    base = Path.home() / ".config" / "chom"
    cache = Path.home() / ".cache" / "chom" / "repositories"
    return base / "repositories.json", cache


def _cache_name(repository: dict) -> str:
    name = re.sub(r"[^A-Za-z0-9_.-]", "_", repository["name"])
    identity = hashlib.sha256(repository["url"].encode("utf-8")).hexdigest()[:12]
    return f"{name}-{identity}"


def list_repositories() -> list[dict]:
    registry_path, _ = _paths()
    repositories = [dict(DEFAULT_REPOSITORY)]
    if registry_path.is_file():
        try:
            data = json.loads(registry_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RepositoryError(f"cannot read repository list: {exc}") from exc
        if not isinstance(data, list):
            raise RepositoryError("repository list must be a JSON array")
        for item in data:
            if isinstance(item, dict) and isinstance(item.get("name"), str) and item["name"] != DEFAULT_REPOSITORY["name"]:
                repositories.append(item)
    return repositories


def _save_repositories(repositories: list[dict]) -> None:
    registry_path, _ = _paths()
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    custom = [item for item in repositories if item["name"] != DEFAULT_REPOSITORY["name"]]
    temporary = registry_path.with_suffix(".tmp")
    temporary.write_text(json.dumps(custom, indent=2) + "\n", encoding="utf-8")
    temporary.replace(registry_path)


def _github_parts(url: str) -> tuple[str, str]:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or parsed.netloc.lower() != "github.com":
        raise RepositoryError("repository URL must be an https://github.com/OWNER/REPO URL")
    parts = [part for part in parsed.path.strip("/").split("/") if part]
    if len(parts) != 2:
        raise RepositoryError("repository URL must point directly to an OWNER/REPO repository")
    owner, name = parts
    name = name.removesuffix(".git")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", owner) or not re.fullmatch(r"[A-Za-z0-9_.-]+", name):
        raise RepositoryError("invalid GitHub repository URL")
    return owner, name


def add_repository(url: str, name: str | None = None, branch: str | None = None) -> dict:
    owner, repo = _github_parts(url)
    repo_name = name or f"{owner}/{repo}"
    if not re.fullmatch(r"[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)?", repo_name):
        raise RepositoryError("repository name may contain letters, numbers, '.', '_' and '-'")
    repositories = list_repositories()
    if any(item["name"] == repo_name for item in repositories):
        raise RepositoryError(f"repository already exists: {repo_name}")
    item = {"name": repo_name, "url": f"https://github.com/{owner}/{repo}", "branch": branch}
    repositories.append(item)
    _save_repositories(repositories)
    return item


def remove_repository(name: str) -> None:
    if name == DEFAULT_REPOSITORY["name"]:
        raise RepositoryError("the default repository cannot be removed")
    repositories = list_repositories()
    remaining = [item for item in repositories if item["name"] != name]
    if len(remaining) == len(repositories):
        raise RepositoryError(f"repository not found: {name}")
    _save_repositories(remaining)


def _request_json(url: str) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": "chom/0.1", "Accept": "application/vnd.github+json"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read(8 * 1024 * 1024).decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RepositoryError(f"GitHub request failed for {url}: {exc}") from exc


def _request_text(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "chom/0.1"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            content = response.read(1024 * 1024 + 1)
        if len(content) > 1024 * 1024:
            raise RepositoryError("formula exceeds 1 MiB")
        return content
    except RepositoryError:
        raise
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise RepositoryError(f"cannot download formula from {url}: {exc}") from exc


def update_repository(repository: dict) -> list[dict]:
    owner, repo = _github_parts(repository["url"])
    api = f"https://api.github.com/repos/{owner}/{repo}"
    metadata = _request_json(api)
    branch = repository.get("branch") or metadata.get("default_branch")
    if not isinstance(branch, str) or not branch:
        raise RepositoryError(f"could not determine default branch for {repository['name']}")
    encoded_branch = urllib.parse.quote(branch, safe="")
    tree = _request_json(f"{api}/git/trees/{encoded_branch}?recursive=1")
    if tree.get("truncated"):
        raise RepositoryError(f"repository tree is too large to index: {repository['name']}")
    entries = [entry["path"] for entry in tree.get("tree", []) if entry.get("type") == "blob" and entry.get("path", "").endswith(".cho")]
    if not entries:
        raise RepositoryError(f"no .cho formulas found in {repository['name']}")

    _, cache_root = _paths()
    destination = cache_root / _cache_name(repository)
    destination.mkdir(parents=True, exist_ok=True)
    results = []
    for index, formula_path in enumerate(sorted(entries)):
        encoded_path = urllib.parse.quote(formula_path, safe="/")
        raw_branch = urllib.parse.quote(branch, safe="/")
        raw_url = f"https://raw.githubusercontent.com/{owner}/{repo}/{raw_branch}/{encoded_path}"
        content = _request_text(raw_url)
        cached_path = destination / f"{index:05d}-{Path(formula_path).name}"
        cached_path.write_bytes(content)
        try:
            formula = parse_formula(cached_path)
        except FormulaError as exc:
            cached_path.unlink(missing_ok=True)
            print(f"chom: skipping invalid formula {repository['name']}:{formula_path}: {exc}", file=sys.stderr)
            continue
        results.append({"formula": formula, "path": cached_path, "repo": repository["name"], "relative_path": formula_path})
    if not results:
        raise RepositoryError(f"no valid .cho formulas found in {repository['name']}")
    index_path = destination / "index.json"
    index_path.write_text(json.dumps([{"name": item["formula"]["name"], "version": item["formula"]["version"], "path": str(item["path"]), "relative_path": item["relative_path"]} for item in results], indent=2) + "\n", encoding="utf-8")
    repository["branch"] = branch
    return results


def cached_formulas(repository: dict) -> list[dict]:
    _, cache_root = _paths()
    index_path = cache_root / _cache_name(repository) / "index.json"
    if not index_path.is_file():
        return []
    try:
        entries = json.loads(index_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RepositoryError(f"cannot read cached index for {repository['name']}: {exc}") from exc
    results = []
    for entry in entries:
        path = Path(entry["path"])
        try:
            formula = parse_formula(path)
        except (FormulaError, OSError):
            continue
        results.append({"formula": formula, "path": path, "repo": repository["name"], "relative_path": entry["relative_path"]})
    return results


def all_cached_formulas() -> list[dict]:
    """Return formulas from the local cache without contacting repositories."""
    repositories = list_repositories()
    result = []
    for repository in repositories:
        result.extend(cached_formulas(repository))
    return result
