"""平台资源 CRUD 冒烟：六类资源逐个走「创建 → 查询 → 修改 → 删除」，并验证删除保护。

用法（后端需先启动）：
    python scripts/smoke_crud.py
    python scripts/smoke_crud.py --base-url http://127.0.0.1:8000 --keep

退出码 0 表示全部通过。`--keep` 会保留创建的测试数据便于人工查看。
"""

from __future__ import annotations

import argparse
import sys
import time

import httpx

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

STAMP = time.strftime("%m%d%H%M%S")

PAYLOADS: dict[str, dict] = {
    "models": {
        "name": f"smoke_model_{STAMP}",
        "description": "CRUD 冒烟",
        "base_url": "https://api.deepseek.com",
        "api_key": "sk-smoke-not-real",
        "model_name": "deepseek-chat",
        "default_params": {"temperature": 0.5},
        "timeout_s": 30,
        "is_default": False,
        "is_enabled": True,
    },
    "prompts": {
        "name": f"smoke_prompt_{STAMP}",
        "description": "CRUD 冒烟",
        "messages": [
            {"role": "system", "content": "你是测试助手。", "order": 1},
            {"role": "user", "content": "{{input}}", "order": 2},
        ],
        "params": {"temperature": 0.2},
        "variables": ["input"],
        "is_enabled": True,
    },
    "tools": {
        "name": f"smoke_tool_{STAMP}",
        "description": "CRUD 冒烟",
        "impl_type": "builtin",
        "impl_config": {"builtin_name": "milvus_search"},
        "params_schema": {"type": "object", "properties": {"query": {"type": "string"}}},
        "returns_schema": {"type": "array"},
        "test_cases": [],
        "is_enabled": True,
    },
    "knowledge-bases": {
        "name": f"smoke_kb_{STAMP}",
        "description": "CRUD 冒烟",
        "collection": "jd",
        "embedding_model": "text-embedding-v4",
        "top_k": 5,
        "chunk_config": {},
        "filter_fields": {"id_prefix": "bigdata_"},
        "is_enabled": True,
    },
    "skills": {
        "name": f"smoke_skill_{STAMP}",
        "description": "CRUD 冒烟",
        "instructions": "测试指令",
        "prompt_id": None,
        "tool_ids": [],
        "kb_ids": [],
        "output_schema": {},
        "is_enabled": True,
    },
    "agents": {
        "name": f"smoke_agent_{STAMP}",
        "description": "CRUD 冒烟",
        "agent_type": "general",
        "persona": "",
        "model_config_id": None,
        "prompt_id": None,
        "skill_ids": [],
        "tool_ids": [],
        "kb_ids": [],
        "params_override": {},
        "is_enabled": True,
    },
}


class Report:
    def __init__(self) -> None:
        self.rows: list[tuple[bool, str]] = []

    def add(self, ok: bool, text: str) -> None:
        self.rows.append((ok, text))

    @property
    def failed(self) -> int:
        return sum(1 for ok, _ in self.rows if not ok)


def crash_free_crud(client: httpx.Client, slug: str, report: Report) -> int | None:
    payload = PAYLOADS[slug]
    try:
        created = client.post(f"/{slug}", json=payload).raise_for_status().json()
    except Exception as exc:  # noqa: BLE001
        report.add(False, f"{slug:<15} 创建失败：{exc}")
        return None
    item_id = created["id"]
    report.add(True, f"{slug:<15} 创建 → #{item_id}")

    # 查询
    try:
        fetched = client.get(f"/{slug}/{item_id}").raise_for_status().json()
        assert fetched["name"] == payload["name"], "名称不一致"
        report.add(True, f"{slug:<15} 查询 → {fetched['name']}")
    except Exception as exc:  # noqa: BLE001
        report.add(False, f"{slug:<15} 查询失败：{exc}")

    # 修改
    try:
        updated_payload = {**payload, "description": "CRUD 冒烟（已修改）"}
        updated = client.put(f"/{slug}/{item_id}", json=updated_payload).raise_for_status().json()
        assert "已修改" in str(updated["description"]), updated.get("description")
        report.add(True, f"{slug:<15} 修改 → {updated['description']}")
    except Exception as exc:  # noqa: BLE001
        report.add(False, f"{slug:<15} 修改失败：{exc}")

    # 密钥不回显
    if slug == "models":
        masked = fetched.get("api_key_masked", "")
        report.add(
            "sk-smoke" not in str(fetched),
            f"{slug:<15} 密钥掩码 → {masked or '（空）'}",
        )
    return item_id


def crud_suite(client: httpx.Client, report: Report, keep: bool) -> dict[str, int]:
    ids: dict[str, int] = {}
    for slug in PAYLOADS:
        item_id = crash_free_crud(client, slug, report)
        if item_id:
            ids[slug] = item_id

    if keep:
        report.add(True, "--keep：保留测试数据，跳过删除")
        return ids

    for slug, item_id in ids.items():
        try:
            resp = client.delete(f"/{slug}/{item_id}")
            if resp.status_code == 409:
                report.add(False, f"{slug:<15} 删除被拒（意外）：{resp.text[:80]}")
                continue
            resp.raise_for_status()
            gone = client.get(f"/{slug}/{item_id}")
            report.add(gone.status_code == 404, f"{slug:<15} 删除 → 再查返回 {gone.status_code}")
        except Exception as exc:  # noqa: BLE001
            report.add(False, f"{slug:<15} 删除失败：{exc}")
    return ids


def reference_protection(client: httpx.Client, report: Report) -> None:
    """被 Agent 引用的资源不允许直接删除。"""
    skill_resp = client.post(
        "/skills",
        json={**PAYLOADS["skills"], "name": f"ref_skill_{STAMP}"},
    )
    if skill_resp.status_code >= 400:
        report.add(False, f"{'删除保护':<15} 准备数据失败：{skill_resp.status_code} {skill_resp.text[:120]}")
        return
    skill = skill_resp.json()

    agent_resp = client.post(
        "/agents",
        json={**PAYLOADS["agents"], "name": f"ref_agent_{STAMP}", "skill_ids": [skill["id"]]},
    )
    if agent_resp.status_code >= 400:
        report.add(False, f"{'删除保护':<15} 准备 Agent 失败：{agent_resp.status_code} {agent_resp.text[:120]}")
        client.delete(f"/skills/{skill['id']}")
        return
    agent = agent_resp.json()
    try:
        resp = client.delete(f"/skills/{skill['id']}")
        ok = resp.status_code == 409
        detail = resp.json().get("detail") if resp.status_code == 409 else resp.text[:120]
        report.add(ok, f"{'删除保护':<15} 引用中删除 → {resp.status_code} {str(detail)[:70]}")
    except Exception as exc:  # noqa: BLE001
        report.add(False, f"{'删除保护':<15} 请求异常：{exc}")
    finally:
        client.delete(f"/agents/{agent['id']}")
        client.delete(f"/skills/{skill['id']}")


def main() -> int:
    parser = argparse.ArgumentParser(description="平台资源 CRUD 冒烟")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--keep", action="store_true", help="保留测试数据不删除")
    args = parser.parse_args()

    api = f"{args.base_url.rstrip('/')}/api/v1"
    report = Report()

    with httpx.Client(base_url=api, timeout=30) as client:
        try:
            client.get("/system/status").raise_for_status()
        except Exception as exc:  # noqa: BLE001
            print(f"后端不可达（{api}）：{exc}")
            return 2

        crud_suite(client, report, args.keep)
        reference_protection(client, report)

    print("=" * 72)
    for ok, text in report.rows:
        print(f"{'[ OK ]' if ok else '[FAIL]'} {text}")
    print("=" * 72)
    if report.failed:
        print(f"{report.failed} 项失败")
        return 1
    print(f"全部通过（{len(report.rows)} 项）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
