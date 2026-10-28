"""Read exact App knowledge versions and record explicit, real use feedback."""
from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from pathlib import PurePosixPath
from typing import Any

from akbs_member_ops.http_client import invalid_success_response, request_json
from akbs_member_ops.knowledge.merge_confirmation.client import member_request_headers
from akbs_member_ops.knowledge_search.config import member_api_base_url


SOURCE_FIELDS = frozenset({"repository_url", "commit", "branch", "paths"})
FEEDBACK_OUTCOMES = ("unknown", "not_executed", "rejected_with_reason", "not_applicable", "acquisition_failed",
    "operation_failed", "content_error", "used")


def validate_sources(sources: Any) -> None:
    if not isinstance(sources, list) or not sources or len(sources) > 32:
        raise ValueError("sources must identify at least one exact LincHub version")
    for source in sources:
        if not isinstance(source, dict) or not set(source) <= SOURCE_FIELDS:
            raise ValueError("App source fields must be repository_url, commit, branch and paths")
        url = urllib.parse.urlsplit(str(source.get("repository_url") or ""))
        if (url.scheme != "https" or url.netloc != "git.lincos.ai" or url.query or url.fragment
                or not re.fullmatch(r"/[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*/?", url.path)):
            raise ValueError("repository_url must identify the actual https://git.lincos.ai/owner/repository")
        if not isinstance(source.get("commit"), str) or not re.fullmatch(r"[0-9a-fA-F]{40}", source["commit"]):
            raise ValueError("App source commit must be the full 40-character Git commit, not a branch")
        if (not isinstance(source.get("branch", ""), str)
                or any(ord(c) < 32 or ord(c) == 127 for c in source.get("branch", ""))):
            raise ValueError("App source branch must be text")
        paths = source.get("paths", [])
        if not isinstance(paths, list) or len(paths) > 128:
            raise ValueError("App source paths must be an array")
        for path in paths:
            if (not isinstance(path, str) or not path or "\\" in path or PurePosixPath(path).is_absolute()
                    or any(part in {"", ".", ".."} for part in path.split("/"))
                    or any(ord(c) < 32 or ord(c) == 127 for c in path)):
                raise ValueError("App source paths must be repository-relative paths")


def endpoint(app_id: str, suffix: str = "") -> str:
    if not isinstance(app_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,255}", app_id):
        raise ValueError("app_id must be a server-returned App identity")
    base, _ = member_api_base_url()
    return base.rstrip("/") + "/akbs/api/member/me/apps/" + urllib.parse.quote(app_id, safe="") + suffix


def exact_revision(revision: int) -> int:
    if type(revision) is not int or revision < 1:
        raise ValueError("App reads and feedback require the exact positive server revision")
    return revision


def fetch_app(app_id: str, *, revision: int, timeout: float = 15.0) -> dict[str, Any]:
    url = endpoint(app_id) + "?" + urllib.parse.urlencode({"revision": exact_revision(revision)})
    payload = request_json(urllib.request.Request(url, headers=member_request_headers()), timeout=timeout)
    view = payload.get("app_view")
    if not isinstance(view, dict) or view.get("app_id") != app_id or view.get("revision") != revision:
        raise invalid_success_response("App detail identity or exact revision mismatch")
    try:
        validate_sources(view.get("sources"))
    except ValueError:
        raise invalid_success_response("App detail does not pin its LincHub sources") from None
    knowledge = payload.get("knowledge")
    if knowledge is not None and (not isinstance(knowledge, dict) or knowledge.get("revision") != revision):
        raise invalid_success_response("App reviewed knowledge differs from the selected version")
    return {**payload, "source": "server_api"}


def fetch_app_source(app_id: str, *, revision: int, timeout: float = 15.0) -> dict[str, Any]:
    detail = fetch_app(app_id, revision=revision, timeout=timeout)
    url = endpoint(app_id, f"/revisions/{exact_revision(revision)}/source")
    payload = request_json(urllib.request.Request(url, headers=member_request_headers()), timeout=timeout)
    if payload.get("app_id") != app_id or payload.get("revision") != revision:
        raise invalid_success_response("App source locator identity or revision mismatch")
    sources = payload.get("sources")
    if not isinstance(sources, list):
        raise invalid_success_response("App source locator is missing exact sources")
    pinned = [{key: source.get(key, [] if key == "paths" else "") for key in SOURCE_FIELDS}
              for source in sources if isinstance(source, dict)]
    expected = [{key: source.get(key, [] if key == "paths" else "") for key in SOURCE_FIELDS}
                for source in detail["app_view"]["sources"]]
    if len(pinned) != len(sources) or pinned != expected:
        raise invalid_success_response("App source locator differs from the reviewed sources")
    # Return locators, not code acquired using AKBS administrator credentials.
    return {**payload, "source": "server_api", "reuse_outcome": "not_started"}


def submit_app_feedback(app_id: str, payload: dict[str, Any], *, timeout: float = 15.0) -> dict[str, Any]:
    if (not isinstance(payload, dict) or set(payload) != {"revision", "outcome", "content", "request_id"}
            or payload.get("outcome") not in FEEDBACK_OUTCOMES
            or any(not isinstance(payload.get(key), str) or not payload[key].strip() for key in ("content", "request_id"))):
        raise ValueError("App feedback requires exact revision, actual outcome, content and stable request_id")
    exact_revision(payload.get("revision"))
    headers = {**member_request_headers(), "Content-Type": "application/json"}
    return request_json(urllib.request.Request(endpoint(app_id, "/feedback"),
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"), headers=headers, method="POST"), timeout=timeout)


def format_app_detail(payload: dict[str, Any]) -> str:
    view = payload["app_view"]
    knowledge = payload.get("knowledge") or view
    lines = [f"# {knowledge.get('title', '')}", f"app_id={view['app_id']} / revision={view['revision']}",
        str(knowledge.get("summary", "")), str(knowledge.get("content", "")),
        "使用方法：" + str(view.get("usage", "")), "适用条件：" + str(knowledge.get("applicability", "")),
        "限制：" + str(view.get("limitations", ""))]
    lines.extend(f"源码：{source['repository_url']} @ {source['commit']}" for source in view["sources"])
    return "\n\n".join(lines)
