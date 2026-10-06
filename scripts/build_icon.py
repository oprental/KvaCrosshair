"""Render the SVG source and encode the multi-resolution Windows icon."""
from pathlib import Path
from PIL import Image
from playwright.sync_api import sync_playwright

assets = Path(__file__).resolve().parent.parent / 'assets'
with sync_playwright() as p:
    browser = p.chromium.launch(executable_path='C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless=True)
    page = browser.new_page(viewport={'width':512,'height':512})
    page.set_content('<style>body{margin:0;background:transparent}svg{display:block}</style>'+ (assets / 'icon.svg').read_text(encoding='utf-8'))
    page.locator('svg').screenshot(path=str(assets / 'icon.png'), omit_background=True)
    browser.close()
with Image.open(assets / 'icon.png') as image:
    image.save(assets / 'icon.ico', format='ICO', sizes=[(n,n) for n in (16,24,32,48,64,128,256)])
print('Built icon.png and icon.ico from icon.svg')
