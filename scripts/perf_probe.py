"""前端延迟实测：用真实浏览器（Playwright）测量导航 / 渲染 / 输入耗时。

用法（前后端都已在运行时）：
    python scripts/perf_probe.py

输出三组数据，用来区分"慢在路由""慢在数据""慢在渲染"：
  1. 首屏与每个页面的导航提交耗时、内容出现耗时
  2. 慢资源（>150ms）与失败的请求
  3. 在配置页输入 20 个字符的耗时（反映编辑交互是否卡顿）
"""

from __future__ import annotations

import sys
import time

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = "http://localhost:5173"

PAGES: list[tuple[str, str]] = [
    ("总览", "/"),
    ("场景编排", "/scene"),
    ("模型配置", "/models"),
    ("Prompts", "/prompts"),
    ("Tools", "/tools"),
    ("知识库", "/knowledge-bases"),
    ("Skills", "/skills"),
    ("Agents", "/agents"),
    ("会话", "/sessions"),
]


def ms(start: float) -> int:
    return int((time.perf_counter() - start) * 1000)


def main() -> int:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("未安装 playwright：pip install playwright && python -m playwright install chromium")
        return 2

    console: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.on("console", lambda m: console.append(f"[{m.type}] {m.text[:160]}"))
        page.on("requestfailed", lambda r: console.append(f"[requestfailed] {r.url[:120]}"))
        page.on("pageerror", lambda e: console.append(f"[pageerror] {str(e)[:200]}"))

        started = time.perf_counter()
        page.goto(BASE, wait_until="load")
        load_ms = ms(started)
        started = time.perf_counter()
        page.wait_for_load_state("networkidle")
        idle_ms = ms(started)
        print(f"首屏：load {load_ms} ms，之后 networkidle 再 {idle_ms} ms")
        print()

        print(f"{'页面':<12}{'路由提交':>10}{'内容出现':>10}{'合计':>8}")
        print("-" * 42)
        rows: list[tuple[str, int, int]] = []
        for label, path in PAGES:
            link = page.locator(f'a.rail__link:has-text("{label}")').first
            started = time.perf_counter()
            link.click()
            page.wait_for_function(
                "p => location.pathname === p", arg=path, timeout=15000
            )
            route_ms = ms(started)
            try:
                # 内容出现的通用信号：main 里已有可见文本（各页结构不同，不用 h1 硬匹配）
                page.wait_for_function(
                    "() => { const m = document.querySelector('main');"
                    " return !!m && m.innerText.trim().length > 0; }",
                    timeout=15000,
                )
            except Exception:  # noqa: BLE001
                print(f"{label:<12}{route_ms:>10}{'超时':>10}")
                continue
            content_ms = ms(started)
            rows.append((label, route_ms, content_ms))
            print(f"{label:<12}{route_ms:>10}{content_ms - route_ms:>10}{content_ms:>8}")

        # 会话页单独测：打开某个会话、再切换另一个（报告卡片 + 辩论时间线都在这里渲染）
        try:
            page.locator('a.rail__link:has-text("会话")').first.click()
            page.wait_for_selector(".chat-side__item", timeout=15000)
            items = page.locator(".chat-side__item")
            if items.count() >= 1:
                started = time.perf_counter()
                items.nth(0).click()
                page.wait_for_timeout(1200)
                print(f"\n打开第 1 个会话（含报告渲染）：{ms(started)} ms")
            if items.count() >= 2:
                started = time.perf_counter()
                items.nth(1).click()
                page.wait_for_selector(".chat-side__item.is-active", timeout=15000)
                print(f"切换到第 2 个会话：{ms(started)} ms")
        except Exception as exc:  # noqa: BLE001
            print(f"\n会话页测量失败：{type(exc).__name__}: {str(exc)[:160]}")

        print()
        slow = page.evaluate(
            """() => performance.getEntriesByType('resource')
                 .filter(r => r.duration > 150)
                 .sort((a, b) => b.duration - a.duration)
                 .slice(0, 12)
                 .map(r => [r.name.split('/').slice(-1)[0].slice(0, 48), Math.round(r.duration)])"""
        )
        print("慢资源（>150ms）：")
        if slow:
            for name, duration in slow:
                print(f"  {duration:>6} ms  {name}")
        else:
            print("  （无）")

        print()
        page.locator('a.rail__link:has-text("Skills")').first.click()
        page.wait_for_selector("textarea", timeout=15000)
        box = page.locator("textarea").first
        started = time.perf_counter()
        box.click()
        box.type("x" * 20, delay=0)
        type_ms = ms(started)
        print(f"在 Skills 指令框输入 20 字符：{type_ms} ms（<400ms 属正常，>1000ms 表示渲染卡顿）")

        browser.close()

    if console:
        print()
        print("浏览器控制台（前 15 条）：")
        for line in console[:15]:
            print("  " + line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
