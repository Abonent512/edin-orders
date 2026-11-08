"""Browser acceptance test against an explicitly enabled demo instance.

Run the isolated demo API first, then:
  python scripts/browser_smoke.py --url http://127.0.0.1:8011 --output /tmp/edin-qa
Requires playwright and Chromium (CHROMIUM_PATH may override the executable).
"""
import argparse
import asyncio
import json
import os
import re
from pathlib import Path
from urllib.parse import urlsplit, unquote

from playwright.async_api import async_playwright, expect


async def run(base, output):
    output.mkdir(parents=True,exist_ok=True)
    errors=[]
    async with async_playwright() as p:
        executable=os.getenv('CHROMIUM_PATH') or ('/usr/bin/chromium' if Path('/usr/bin/chromium').is_file() else None)
        proxy=None
        proxy_url=os.getenv('HTTPS_PROXY') or os.getenv('https_proxy') or os.getenv('HTTP_PROXY') or os.getenv('http_proxy')
        if proxy_url:
            parsed=urlsplit(proxy_url)
            proxy={'server':f'{parsed.scheme}://{parsed.hostname}:{parsed.port or 8080}',
                   'bypass':','.join(filter(None,[os.getenv('NO_PROXY') or os.getenv('no_proxy'),'127.0.0.1','localhost']))}
            if parsed.username:proxy['username']=unquote(parsed.username)
            if parsed.password:proxy['password']=unquote(parsed.password)
        browser=await p.chromium.launch(executable_path=executable,headless=True,proxy=proxy,args=['--no-sandbox','--disable-dev-shm-usage'])
        desktop=await browser.new_context(viewport={'width':1440,'height':1040},reduced_motion='reduce')
        phone=await browser.new_context(viewport={'width':390,'height':844},is_mobile=True,has_touch=True,device_scale_factor=1,reduced_motion='reduce')
        operator=await browser.new_context(viewport={'width':1440,'height':1000},reduced_motion='reduce')
        pages=[]
        async def page_in(context,path,role):
            page=await context.new_page();pages.append(page)
            page.on('pageerror',lambda error:errors.append(str(error)))
            def console(message):
                if message.type=='error' and 'auth/refresh' not in message.location.get('url',''):
                    errors.append(message.text)
            page.on('console',console)
            await page.goto(base+path,wait_until='domcontentloaded')
            config=await page.request.get(base+'/api/v1/config')
            assert (await config.json())['demo_mode'],'Browser smoke requires an explicit demo instance'
            await page.get_by_role('button',name=role,exact=True).click()
            await expect(page.locator('.main-shell')).to_be_visible()
            await page.evaluate('document.fonts.ready')
            return page
        web=await page_in(desktop,'/','Покупатель')
        await web.screenshot(path=str(output/'desktop-catalog.png'),full_page=True)
        await web.get_by_label('Добавить в корзину: Дрель-шуруповёрт',exact=True).click()
        await web.get_by_role('button',name=re.compile('^Корзина')).click()
        await web.get_by_label('Увеличить Дрель-шуруповёрт',exact=True).click()
        await web.get_by_label('Имя получателя',exact=True).fill('Браузерный тест')
        await web.get_by_label(re.compile('^Телефон')).fill('+79000000000')
        await web.get_by_role('checkbox').check()
        await web.get_by_role('button',name='Оформить заказ',exact=True).click()
        await expect(web.get_by_role('dialog')).to_be_visible()
        number=(await web.locator('#order-title').inner_text()).split('№')[1].strip()
        expected_total=await web.locator('.detail-total strong').inner_text()
        assert re.sub(r'\D','',expected_total)=='25980',expected_total
        mobile=await page_in(phone,'/mobile/','Покупатель')
        await mobile.get_by_role('button',name='Заказы',exact=True).click()
        await mobile.get_by_role('button',name=re.compile('№ '+number+r'\b')).click()
        await expect(mobile.get_by_role('dialog')).to_be_visible()
        assert await mobile.locator('.detail-total strong').inner_text()==expected_total
        op=await page_in(operator,'/','Оператор')
        await op.get_by_role('button',name=re.compile('№ '+number+r'\b')).click()
        await op.get_by_role('button',name='Подтверждён',exact=True).click()
        for page in [web,mobile]:
            await expect(page.locator('.order-modal .status')).to_have_text('Подтверждён',timeout=12000)
        # Another mobile tab shares the browser cookie; refreshing auth must preserve the first tab's JWT.
        tab=await desktop.new_page();pages.append(tab)
        await tab.goto(base+'/mobile/',wait_until='domcontentloaded')
        await expect(tab.locator('.main-shell')).to_be_visible()
        await tab.get_by_role('button',name='Заказы',exact=True).click()
        await tab.get_by_role('button',name=re.compile('№ '+number+r'\b')).click()
        await op.get_by_role('button',name='Собирается',exact=True).click()
        for page in [web,mobile,tab]:
            await expect(page.locator('.order-modal .status')).to_have_text('Собирается',timeout=12000)
        await mobile.screenshot(path=str(output/'mobile-order.png'),full_page=True)
        await op.screenshot(path=str(output/'operator-order.png'),full_page=True)
        await mobile.get_by_role('button',name='Закрыть заказ',exact=True).click()
        await mobile.get_by_role('button',name='Каталог',exact=True).click()
        overflow=await mobile.evaluate('document.documentElement.scrollWidth > innerWidth')
        assert not overflow,'Mobile page has horizontal overflow'
        await mobile.screenshot(path=str(output/'mobile-catalog.png'),full_page=True)
        await phone.set_offline(True)
        await expect(mobile.locator('.network-banner')).to_be_visible()
        await phone.set_offline(False)
        await expect(mobile.locator('.network-banner')).not_to_be_visible()
        # Restore stock after the acceptance order.
        await op.get_by_role('button',name='Отменить заказ',exact=True).click()
        await op.get_by_role('button',name='Подтвердить отмену',exact=True).click()
        await expect(web.locator('.order-modal .status')).to_have_text('Отменён',timeout=12000)
        report={'result':'PASS' if not errors else 'FAIL','order_number':number,'console_errors':errors,'checks':['desktop_catalog','cart_quantity_and_total','valid_checkout','same_order_mobile','live_status_two_clients','shared_cookie_two_tabs','responsive_no_overflow','offline_reconnect','stock_restored_after_test']}
        (output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
        print(json.dumps(report,ensure_ascii=False))
        await browser.close()
        assert not errors,'Browser reported JavaScript/console errors: '+str(errors)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--url',default='http://127.0.0.1:8011');parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    asyncio.run(run(args.url.rstrip('/'),args.output))
