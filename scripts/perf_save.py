"""保存链路实测：真实浏览器里点「保存」到看到「已保存」的耗时。

用法（前后端都在运行）：
    python scripts/perf_save.py

不改动任何数据：选中条目后直接点保存（提交原值）。
"""

from __future__ import annotations

import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = "http://localhost:5173"

PAGES: list[tuple[str, str]] = [
    ("模型配置", "/models"),
    ("Prompts", "/prompts"),
    ("Tools", "/tools"),
    ("知识库", "/knowledge-bases"),
    ("Skills", "/skills"),
    ("Agents", "/agents"),
]


def main() -> int:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        console: list[str] = []
        page.on("console", lambda m: console.append(f"[{m.type}] {m.text[:160]}"))
        page.on("pageerror", lambda e: console.append(f"[pageerror] {str(e)[:200]}"))
        # 在页面里包一层 fetch：精确记录每次 API 调用的浏览器侧耗时
        page.add_init_script(
            """
            window.__api = [];
            window.__long = [];
            const orig = window.fetch;
            window.fetch = async (...args) => {
              const t0 = performance.now();
              const res = await orig(...args);
              window.__api.push({
                url: String(args[0]).replace(location.origin, ''),
                ms: Math.round(performance.now() - t0),
                status: res.status,
              });
              return res;
            };
            try {
              new PerformanceObserver((list) => {
                for (const e of list.getEntries()) {
                  window.__long.push({ ms: Math.round(e.duration), at: Math.round(e.startTime) });
                }
              }).observe({ entryTypes: ['longtask'] });
            } catch (e) { /* 某些环境不支持 longtask */ }
            """
        )

        print(f"{'页面':<12}{'总耗时(第1/2/3次)':>26}{'其中 API 耗时':>26}{'按钮位移px':>14}")
        print("-" * 84)
        for label, path in PAGES:
            page.goto(BASE + path)
            page.wait_for_selector(".workbench__item", timeout=20000)
            page.locator(".workbench__item").first.click()
            page.wait_for_timeout(200)

            timings: list[int] = []
            api_ms: list[int] = []
            shifts: list[int] = []
            for _ in range(3):
                page.evaluate("window.__api = []; window.__long = []")
                # 页面内直接点按钮并计时：绕开 Playwright 的元素稳定性等待（那是工具开销，不是应用延迟）
                elapsed = page.evaluate(
                    """async () => {
                        const btn = [...document.querySelectorAll('.workbench__editor button')]
                          .find(b => b.textContent.includes('保存'));
                        if (!btn) return -1;
                        const beforeTop = btn.getBoundingClientRect().top;
                        const t0 = performance.now();
                        btn.click();
                        return await new Promise((resolve) => {
                          const id = setInterval(() => {
                            const n = document.querySelector('.notice');
                            const busy = [...document.querySelectorAll('button')]
                              .some(b => b.textContent.includes('保存中'));
                            if (n && n.textContent.includes('已保存') && !busy) {
                              clearInterval(id);
                              resolve({
                                ms: Math.round(performance.now() - t0),
                                shift: Math.round(btn.getBoundingClientRect().top - beforeTop),
                              });
                            }
                          }, 5);
                          setTimeout(() => { clearInterval(id); resolve({ ms: -2, shift: 0 }); }, 20000);
                        });
                    }"""
                )
                timings.append(int(elapsed["ms"]))
                shifts.append(int(elapsed["shift"]))
                calls = page.evaluate("window.__api")
                api_ms.append(sum(item["ms"] for item in calls) if calls else 0)
                longs = page.evaluate("window.__long")
                if longs and max(item["ms"] for item in longs) > 100:
                    print(
                        f"    · {label} 本次有长任务："
                        + ", ".join(f"{item['ms']}ms@t+{item['at']}" for item in longs[:5])
                    )
                page.wait_for_timeout(250)
            total = "/".join(str(t) for t in timings)
            api = "/".join(str(a) for a in api_ms)
            shift = "/".join(str(s) for s in shifts)
            print(f"{label:<12}{total:>26}{api:>26}{shift:>14}")

        print()
        if console:
            print()
            print("控制台：")
            for line in console[:10]:
                print("  " + line)
        browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
