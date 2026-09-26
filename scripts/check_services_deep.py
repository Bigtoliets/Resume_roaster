"""外部服务深度校验：真的登录 MySQL、真的连 Milvus（需要 pymysql / pymilvus）。

与 check_env.py 的区别：check_env.py 只测端口通不通（零依赖）；本脚本验证
凭据是否正确、库是否存在、Milvus 版本是否 >= 2.4（稀疏向量与 hybrid search 的前提）。

用法：
    python scripts/check_services_deep.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / ".env"

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

OK = "[ OK ]"
FAIL = "[FAIL]"

MILVUS_MIN_MAJOR = 2
MILVUS_MIN_MINOR = 4


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


def check_mysql(env: dict[str, str]) -> list[tuple[str, str]]:
    host = env.get("MYSQL_HOST", "")
    if not host:
        return [(FAIL, "MySQL: .env 未填写 MYSQL_HOST")]
    try:
        import pymysql
    except ImportError:
        return [(FAIL, "MySQL: 未安装 pymysql（pip install pymysql cryptography）")]

    try:
        conn = pymysql.connect(
            host=host,
            port=int(env.get("MYSQL_PORT", 3306)),
            user=env.get("MYSQL_USER", "root"),
            password=env.get("MYSQL_PASSWORD", ""),
            charset=env.get("MYSQL_CHARSET", "utf8mb4"),
            connect_timeout=8,
        )
    except Exception as exc:  # noqa: BLE001
        return [(FAIL, f"MySQL: 登录失败 -> {exc}")]

    rows: list[tuple[str, str]] = []
    try:
        with conn.cursor() as cur:
            cur.execute("select version(), current_user()")
            version, current_user = cur.fetchone()
            rows.append((OK, f"MySQL: 登录成功，版本 {version}，账号 {current_user}"))
            cur.execute("show databases")
            dbs = [row[0] for row in cur.fetchall()]
            target = env.get("MYSQL_DB", "resume_roaster")
            if target in dbs:
                rows.append((OK, f"MySQL: 目标库 {target} 已存在"))
            else:
                rows.append(
                    (FAIL, f"MySQL: 目标库 {target} 不存在（现有库: {', '.join(dbs)}）")
                )
    finally:
        conn.close()
    return rows


def check_milvus(env: dict[str, str]) -> list[tuple[str, str]]:
    host = env.get("MILVUS_HOST", "")
    if not host:
        return [(FAIL, "Milvus: .env 未填写 MILVUS_HOST")]
    try:
        from pymilvus import MilvusClient
    except ImportError:
        return [(FAIL, "Milvus: 未安装 pymilvus")]

    uri = f"http://{host}:{env.get('MILVUS_PORT', 19530)}"
    kwargs: dict[str, object] = {"uri": uri, "timeout": 10}
    if env.get("MILVUS_USER"):
        kwargs["token"] = f"{env['MILVUS_USER']}:{env.get('MILVUS_PASSWORD', '')}"
    if env.get("MILVUS_DB"):
        kwargs["db_name"] = env["MILVUS_DB"]

    try:
        client = MilvusClient(**kwargs)  # type: ignore[arg-type]
        version = client.get_server_version()
        collections = client.list_collections()
    except Exception as exc:  # noqa: BLE001
        return [(FAIL, f"Milvus: 连接失败 -> {exc}")]

    rows: list[tuple[str, str]] = [(OK, f"Milvus: 连接成功，服务端版本 {version}")]

    try:
        match = re.search(r"(\d+)\.(\d+)", version)
        if match is None:
            rows.append((FAIL, f"Milvus: 无法解析版本号 {version!r}"))
            return rows
        major, minor = int(match.group(1)), int(match.group(2))
        if (major, minor) >= (MILVUS_MIN_MAJOR, MILVUS_MIN_MINOR):
            rows.append(
                (
                    OK,
                    f"Milvus: 版本满足 >= {MILVUS_MIN_MAJOR}.{MILVUS_MIN_MINOR}"
                    "（支持稀疏向量 + hybrid search）",
                )
            )
        else:
            rows.append(
                (
                    FAIL,
                    f"Milvus: 版本 {version} 低于 {MILVUS_MIN_MAJOR}.{MILVUS_MIN_MINOR}，"
                    "稀疏向量不可用，需升级或退回纯稠密检索（改写 §5.3）",
                )
            )
    except (ValueError, IndexError):
        rows.append((FAIL, f"Milvus: 无法解析版本号 {version!r}"))

    rows.append(
        (
            OK,
            "Milvus: 现有 collection = "
            + (str(collections) if collections else "(空，跑 init_collections.py 会创建 jd)"),
        )
    )
    return rows


def main() -> int:
    env = load_env(ENV_PATH)
    if not env:
        print(f"未找到或无法解析 {ENV_PATH}")
        return 2

    rows = check_mysql(env) + check_milvus(env)
    width = max(len(msg) for _, msg in rows)
    bar = "=" * (width + 8)
    print(bar)
    for status, msg in rows:
        print(f"{status} {msg}")
    print(bar)

    failed = [msg for status, msg in rows if status == FAIL]
    if failed:
        print(f"\n{len(failed)} 项未通过，请先解决上面标 FAIL 的问题。")
        return 1
    print("\nMySQL 与 Milvus 均可用。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
