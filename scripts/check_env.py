"""外部服务连通性检查（零依赖，仅用标准库）。

用法：
    python scripts/check_env.py

读取项目根目录的 .env，检查 MySQL / Milvus / Langfuse 是否可达。
填完 .env 后先跑这个脚本，通过再进入开发。
"""

from __future__ import annotations

import base64
import json
import socket
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / ".env"

# Windows 控制台默认 GBK，中文会乱码，这里强制 UTF-8 输出
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

OK = "[ OK ]"
FAIL = "[FAIL]"
SKIP = "[SKIP]"


def load_env(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    if not path.exists():
        return env
    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def check_tcp(name: str, host: str, port: str | int, timeout: float = 5.0) -> tuple[str, str]:
    if not host:
        return SKIP, f"{name}: 未填写地址"
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return OK, f"{name}: {host}:{port} 可连接"
    except Exception as exc:  # noqa: BLE001
        return FAIL, f"{name}: {host}:{port} 连接失败 -> {exc}"


def http_get(url: str, timeout: float = 8.0, auth: str | None = None) -> tuple[int, str]:
    req = urllib.request.Request(url)
    if auth:
        token = base64.b64encode(auth.encode()).decode()
        req.add_header("Authorization", f"Basic {token}")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, resp.read().decode("utf-8", errors="ignore")


def check_langfuse(env: dict[str, str]) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    base = (env.get("LANGFUSE_BASE_URL") or env.get("LANGFUSE_HOST") or "").rstrip("/")
    if not base:
        return [(SKIP, "Langfuse: 未填写地址")]

    try:
        status, body = http_get(f"{base}/api/public/health")
        version = ""
        try:
            version = json.loads(body).get("version", "")
        except Exception:  # noqa: BLE001
            pass
        label = f"Langfuse health: HTTP {status} (version {version or 'unknown'})"
        rows.append((OK if status == 200 else FAIL, label))
    except Exception as exc:  # noqa: BLE001
        rows.append((FAIL, f"Langfuse health 失败 -> {exc}"))
        return rows

    public_key = env.get("LANGFUSE_PUBLIC_KEY", "")
    secret_key = env.get("LANGFUSE_SECRET_KEY", "")
    if not public_key or not secret_key:
        rows.append((SKIP, "Langfuse 密钥: 未填写"))
        return rows

    try:
        _, body = http_get(f"{base}/api/public/projects", auth=f"{public_key}:{secret_key}")
        names = [item.get("name") for item in json.loads(body).get("data", [])]
        listed = ", ".join([name for name in names if name]) or "(无)"
        rows.append((OK, f"Langfuse 密钥有效，可访问项目: {listed}"))
    except urllib.error.HTTPError as exc:
        rows.append((FAIL, f"Langfuse 密钥校验失败 -> HTTP {exc.code}"))
    except Exception as exc:  # noqa: BLE001
        rows.append((FAIL, f"Langfuse 密钥校验失败 -> {exc}"))
    return rows


def main() -> int:
    env = load_env(ENV_PATH)
    if not env:
        print(f"未找到或无法解析 {ENV_PATH}，请先从 .env.example 复制。")
        return 2

    rows: list[tuple[str, str]] = [
        check_tcp("MySQL ", env.get("MYSQL_HOST", ""), env.get("MYSQL_PORT", 3306)),
        check_tcp("Milvus", env.get("MILVUS_HOST", ""), env.get("MILVUS_PORT", 19530)),
    ]
    rows.extend(check_langfuse(env))

    width = max(len(msg) for _, msg in rows)
    bar = "=" * (width + 8)
    print(bar)
    for status, msg in rows:
        print(f"{status} {msg}")
    print(bar)

    failed = [msg for status, msg in rows if status == FAIL]
    skipped = [msg for status, msg in rows if status == SKIP]
    if failed:
        print(f"\n{len(failed)} 项失败：请检查地址、端口与防火墙。")
        return 1
    if skipped:
        print(f"\n{len(skipped)} 项未填写（SKIP）：填完 .env 后重新运行本脚本。")
        return 0
    print("\n全部外部服务可达，可以进入开发。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
