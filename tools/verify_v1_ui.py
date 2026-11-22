"""Exercise real NiceGUI handlers and coordinator IPC without native hardware.

Uses NiceGUI's User simulation helper (no browser rendering). No
models are downloaded; microphone capture is explicitly skipped. All writes
belong to a temporary data root. Run this as its own process, not in a user app.
"""
from __future__ import annotations
import argparse
import asyncio
import json
import logging
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


async def exercise():
    import httpx
    import runpy
    from nicegui import core, ui
    from nicegui.testing.user import User
    from core.product import bridge

    # This verifier is a fresh process. Avoid the test framework's global reset,
    # which in NiceGUI 3.15 can create a script client while clearing storage.
    os.environ['NICEGUI_USER_SIMULATION'] = 'true'
    runpy.run_path(str(ROOT / 'web_gui' / 'app.py'), run_name='__main__')
    async with core.app.router.lifespan_context(core.app), httpx.AsyncClient(
            transport=httpx.ASGITransport(core.app), base_url='http://test') as client:
        user = User(client)
        def click_exact(label):
            interaction = user.find(kind=ui.button, content=label)
            interaction.elements = {button for button in interaction.elements if button.text == label}
            assert interaction.elements, f'Missing exact button: {label}'
            interaction.click()
        await user.open('/')
        await user.should_see('欢迎使用 CapsWriter', retries=100)
        click_exact('稍后继续')
        for _ in range(100):
            state = await asyncio.to_thread(bridge.request)
            if state['setup']['status'] == 'skipped':
                break
            await asyncio.sleep(0.05)
        assert state['setup']['status'] == 'skipped'
        assert not state['health']['can_dictate'] and not state['health']['model_loaded']
        await user.should_not_see('欢迎使用 CapsWriter', retries=100)
        click_exact('首次设置 / 继续设置')
        await user.should_see('1 / 6 · 了解设备')
        click_exact('继续')
        await user.should_see('2 / 6 · 选择本地模型', retries=30)
        await user.should_see('SenseVoice')
        click_exact('暂不安装，继续设置')
        await user.should_see('4 / 6 · 麦克风测试', retries=30)
        click_exact('继续 / 跳过测试')
        await user.should_see('5 / 6 · 快捷键与输出', retries=30)
        click_exact('继续')
        await user.should_see('6 / 6 · 开始使用', retries=30)
        click_exact('进入控制中心')
        for _ in range(100):
            state = await asyncio.to_thread(bridge.request)
            if state['setup']['status'] == 'complete':
                break
            await asyncio.sleep(0.05)
        assert state['setup']['status'] == 'complete'
        assert state['setup']['microphone_test'] == 'not_tested'
        assert not state['health']['can_dictate']
        await user.should_not_see('欢迎使用 CapsWriter', retries=100)
        owner = state['background_owner']
        await user.open('/')
        await asyncio.sleep(0.3)
        assert not any(isinstance(element, ui.dialog) and element.value for element in user.client.elements.values())
        reconnected = await asyncio.to_thread(bridge.request)
        assert reconnected['background_owner'] == owner
        assert reconnected['setup']['status'] == 'complete'
        return {'result': 'PASS', 'checks': ['HTTP page construction', 'skip and resume wizard',
                'six-step no-model path', 'microphone skipped truthfully',
                'completed setup survives page reopen', 'same coordinator after reopen'],
                'limitations': ['simulated NiceGUI events; no browser pixels',
                                'no Windows native window, microphone, hotkey or input test']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='capswriter-ui-review-') as temporary:
        os.environ['CAPSWRITER_DATA_DIR'] = temporary
        os.environ['CAPSWRITER_MODEL_DIR'] = str(Path(temporary) / 'models')
        os.environ['CAPSWRITER_WEB_PREVIEW'] = '1'
        os.environ['CAPSWRITER_CONTROL_CENTER'] = '1'
        os.environ.pop('CAPSWRITER_MODEL_ID', None)
        try:
            result = asyncio.run(exercise())
        finally:
            from core.product import bridge
            import psutil
            try:
                owner = psutil.Process(bridge.descriptor()['pid'])
                parent = owner.parent()
                # The Windows venv redirector inherits the startup log handle.
                # Wait for it as well only when it launched this same command.
                redirector = parent if parent and parent.cmdline()[1:] == owner.cmdline()[1:] else None
                bridge.request('shutdown', timeout=3)
                owner.wait(timeout=12)
                if redirector:
                    redirector.wait(timeout=4)
            except (RuntimeError, psutil.NoSuchProcess):
                pass
            # On Windows the app's file handlers keep the temporary profile
            # locked until closed, even after the simulated page disconnects.
            logging.shutdown()
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
