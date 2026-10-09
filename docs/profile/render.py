import asyncio, sys
from pathlib import Path
from playwright.async_api import async_playwright
HERE = Path(__file__).resolve().parent
async def main(src, dst):
    async with async_playwright() as p:
        br = await p.chromium.launch(); pg = await br.new_page()
        await pg.goto((HERE / src).as_uri()); await pg.wait_for_timeout(1500)
        await pg.pdf(path=str(HERE / dst), format="A4", print_background=True, prefer_css_page_size=True)
        await br.close()
asyncio.run(main(sys.argv[1], sys.argv[2]))
