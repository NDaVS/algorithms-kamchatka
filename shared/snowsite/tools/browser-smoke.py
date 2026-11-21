"""Optional actual browser regression: Linux/Windows, starts a disposable local API.
Install tools/browser-requirements.txt and playwright install chromium. On systems
with installed Chromium, set SNOW_CHROMIUM to its executable. No network service.
"""
import json
import os
from pathlib import Path
import socket
import subprocess
import time
from urllib.request import urlopen
from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parent.parent
with socket.socket() as s:
    s.bind(('127.0.0.1', 0))
    port = s.getsockname()[1]
url = f'http://127.0.0.1:{port}'
env = {**os.environ, 'PORT': str(port)}
env.pop('SNOW_OPEN', None)
proc = subprocess.Popen(['node', str(ROOT / 'apps/api/server.mjs')], cwd=ROOT, env=env)
try:
    for _ in range(100):
        if proc.poll() is not None:
            raise RuntimeError('API exited during startup')
        try:
            with urlopen(url + '/api/health', timeout=0.5) as r:
                if r.status == 200:
                    break
        except OSError:
            time.sleep(0.1)
    else:
        raise RuntimeError('API not ready')
    with sync_playwright() as p:
        options = {'headless': True}
        if os.getenv('SNOW_CHROMIUM'):
            options['executable_path'] = os.environ['SNOW_CHROMIUM']
        browser = p.chromium.launch(**options)
        print('Chromium', browser.version, flush=True)
        page = browser.new_page(viewport={'width': 1366, 'height': 768})
        errors = []
        page.on('pageerror', lambda e: errors.append(str(e)))
        page.goto(url)
        expect(page.locator('#variants button')).to_have_count(3, timeout=20000)
        expect(page.locator('#map')).to_be_visible()
        expect(page.locator('#headline')).to_contain_text('меньше базового')
        page.locator('#variants button').first.focus()
        page.keyboard.press('Enter')
        expect(page.locator('#variants button').first).to_have_attribute('aria-pressed', 'true')
        expect(page.locator('#composition')).to_contain_text('База')
        page.locator('#edit-btn').click()
        expect(page.locator('#drawer')).to_be_visible()
        page.locator('#tab-truck').click()
        page.locator('#speed').fill('33')
        page.locator('#speed').blur()
        page.locator('#drawer-close').click()
        with page.expect_request(lambda r: r.url.endswith('/api/compare') and r.method == 'POST') as request:
            page.locator('#compare').click()
        assert request.value.post_data_json['truck']['driveKmh'] == 33
        expect(page.locator('#stats-status')).to_have_text('Готово', timeout=20000)
        print('PASS legacy map, 3 rankings, keyboard, edit, compare', flush=True)
        page.goto(url + '/exact.html')
        page.locator('#input').set_input_files(str(ROOT / 'examples/demo-v1.json'))
        page.locator('#run').click()
        expect(page.locator('#status')).to_contain_text('exact_top_k', timeout=30000)
        result = json.loads(page.locator('#output').text_content())
        assert result['alternatives'][0]['transportDistanceMm'] == 92228770
        assert result['baseline']['transportDistanceMm'] == 159425990
        assert not errors, errors
        print('PASS exact file upload, API solve, baseline and best, no page errors', flush=True)
        browser.close()
finally:
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
