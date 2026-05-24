from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()
    
    page.on("console", lambda msg: print(f"CONSOLE: {msg.text}"))
    page.on("pageerror", lambda err: print(f"ERROR: {err}"))
    
    print(f"Navigating to remote dashboard...")
    page.goto("http://127.0.0.1:8000/static/admin_dashboard.html")
    
    page.wait_for_timeout(3000)
    
    print("Clicking obstacles tab...")
    page.evaluate("() => { document.querySelectorAll('.nav-item').forEach(el => { if (el.innerText.includes('障碍物库')) el.click() }) }")
    
    page.wait_for_timeout(2000)
    page.screenshot(path="obstacles.png")
    
    print("Clicking shops tab...")
    page.evaluate("() => { document.querySelectorAll('.nav-item').forEach(el => { if (el.innerText.includes('店铺数据')) el.click() }) }")
    
    page.wait_for_timeout(2000)
    page.screenshot(path="shops.png")
    
    print("Done")
    browser.close()
