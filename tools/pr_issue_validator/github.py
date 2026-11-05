"""Bounded GitHub REST collector for the issue-to-PR validator."""

from __future__ import annotations

import json
import re
from urllib.request import Request, urlopen

LINK = re.compile(r"(?i)(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s+#(\d+)")
FALLBACK_LINK = re.compile(r"(?<!\d)#(\d+)(?!\d)")


class GitHubClient:
    def __init__(
        self, token: str, api_url: str = "https://api.github.com", timeout: float = 15
    ):
        self.token = token
        self.api_url = api_url.rstrip("/")
        self.timeout = timeout

    def get(self, path: str):
        request = Request(
            self.api_url + path,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        with urlopen(request, timeout=self.timeout) as response:
            return json.load(response)

    def collect(self, repo: str, pr_number: int) -> dict:
        base = f"/repos/{repo}"
        pr = self.get(f"{base}/pulls/{pr_number}")
        body = str(pr.get("body") or "")
        match = LINK.search(body) or FALLBACK_LINK.search(body)
        if not match:
            raise ValueError("PR body does not link an issue")
        issue_number = int(match.group(1))
        issue = self.get(f"{base}/issues/{issue_number}")
        checks = self.get(f"{base}/commits/{pr['head']['sha']}/check-runs")
        return {
            "issue": {
                "number": issue_number,
                "title": issue.get("title", ""),
                "body": issue.get("body", ""),
                "comments": self.get(f"{base}/issues/{issue_number}/comments"),
            },
            "pr": {
                "number": pr_number,
                "title": pr.get("title", ""),
                "body": body,
                "base": pr.get("base", {}).get("ref", ""),
                "head": pr.get("head", {}).get("ref", ""),
                "files": [
                    item["filename"]
                    for item in self.get(f"{base}/pulls/{pr_number}/files")
                ],
                "commits": self.get(f"{base}/pulls/{pr_number}/commits"),
                "comments": self.get(f"{base}/issues/{pr_number}/comments"),
                "review_comments": self.get(f"{base}/pulls/{pr_number}/comments"),
                "checks": [
                    {
                        "name": item.get("name"),
                        "status": item.get("status"),
                        "conclusion": item.get("conclusion"),
                    }
                    for item in checks.get("check_runs", [])
                ],
            },
            "tests": [],
            "evidence": [],
        }
