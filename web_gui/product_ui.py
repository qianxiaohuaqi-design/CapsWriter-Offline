"""V1 flows embedded into the existing eight control-center pages."""
from __future__ import annotations
import asyncio
import json
from pathlib import Path
from nicegui import ui, run
from core.product import bridge
from core.product.paths import data_root
from web_gui.config_manager import ConfigManager

STATE_TEXT = {'queued': '排队中', 'downloading': '下载中', 'verifying': '校验中',
              'installing': '安装中', 'completed': '已完成', 'paused': '已暂停',
              'pausing': '正在暂停', 'cancelling': '正在安全停止', 'cancelled': '已取消',
              'failed': '失败，可重试', 'interrupted': '上次已中断', 'running': '转写中',
              'loading': '正在加载', 'ready': '模型已加载', 'error': '需要处理', 'idle': '等待操作'}


def size_text(value):
    if value is None:
        return '尚未核验'
    return f'{value / (1024 * 1024):,.1f} MiB'


def task_parameters():
    defaults = {'file_save_txt': True, 'file_save_json': True, 'file_save_srt': False,
                'file_save_merge': False, 'file_seg_duration': 60, 'file_seg_overlap': 4,
                'language': 'auto', 'context': ''}
    return {key: ConfigManager.get_client_var(key, default) for key, default in defaults.items()}


async def observe_transcription(file, progress=None):
    """Observe a persistent task; disconnecting this observer never cancels work."""
    from web_gui.transcription_service import TranscriptionResult
    task = await run.io_bound(lambda: bridge.request('submit_task', path=str(file), parameters=task_parameters(), timeout=60))
    while True:
        if progress:
            progress({'percent': task.get('percent', 0), 'detail': task.get('detail', ''),
                      'phase': task.get('phase'), 'task_id': task['id']})
        if task['state'] in {'completed', 'failed', 'cancelled', 'interrupted'}:
            return TranscriptionResult(task['state'] == 'completed', task.get('detail', ''),
                                       input_file=Path(task['source']['path']),
                                       output_dir=Path(task['output_dir']) if task.get('output_dir') else None,
                                       output_files={k: Path(v) for k, v in task.get('outputs', {}).items()},
                                       text=task.get('text', ''))
        await asyncio.sleep(0.7)
        snapshot = await run.io_bound(bridge.request)
        task = next(row for row in snapshot['tasks'] if row['id'] == task['id'])


class ProductUI:
    def __init__(self):
        self.state = {}
        self.status_label = None
        self.model_box = None
        self.task_box = None
        self.wizard = None
        self.wizard_step = None
        self.wizard_box = None
        # Dialogs outlive model/task rows which are rebuilt as progress changes.
        self.dialog_host = ui.element('div').classes('contents')
        self.recovery_box = None
        self._last_recovery = None
        self._last_models = None
        self._last_tasks = None
        self._opened_initial = False
        self._refreshing = False
        ui.timer(1.0, self.refresh)
        ui.timer(0.1, self.initialize, once=True)

    async def initialize(self):
        try:
            self.state = await run.io_bound(bridge.ensure_coordinator)
            self.paint()
            setup = self.state['setup']
            if setup['status'] == 'new' and not self._opened_initial:
                self._opened_initial = True
                self.open_wizard()
        except Exception as error:
            if self.status_label:
                self.status_label.set_text(str(error))
            ui.notify(str(error), type='warning', timeout=6000)

    async def refresh(self):
        if self._refreshing:
            return
        self._refreshing = True
        try:
            self.state = await run.io_bound(bridge.request)
            self.paint()
        except Exception:
            if self.status_label:
                self.status_label.set_text('后台暂未连接 · 可在服务与诊断重试')
        finally:
            self._refreshing = False

    def paint(self):
        health = self.state.get('health', {})
        state = self.state.get('switch', {})
        if self.status_label:
            if self.state.get('shutting_down'):
                text = '正在安全退出 · 等待任务停止'
            elif state.get('state') == 'loading':
                text = '模型正在加载 · 控制中心可继续使用'
            elif health.get('file_inference_busy'):
                text = '正在处理文件片段 · 请稍后听写'
            elif health.get('can_dictate'):
                text = '听写已就绪'
            elif health.get('model_loaded'):
                text = '模型已加载 · 等待麦克风与快捷键'
            elif health.get('server_alive'):
                text = '模型加载中或需要处理'
            else:
                text = '模型未就绪 · 请到模型中心'
            self.status_label.set_text(text)
        model_key = json.dumps({key: self.state.get(key) for key in ('models', 'downloads', 'switch', 'setup')}, ensure_ascii=False)
        if self.model_box is not None and model_key != self._last_models:
            self._last_models = model_key
            self.paint_models()
        task_key = json.dumps(self.state.get('tasks', []), ensure_ascii=False)
        if self.task_box is not None and task_key != self._last_tasks:
            self._last_tasks = task_key
            self.paint_tasks()
        if self.wizard and self.wizard_step == 3:
            # This step contains no editable controls, so progress redraw is safe.
            self.paint_wizard()
        recovery_key = json.dumps(self.state.get('model_recovery', []), ensure_ascii=False)
        if self.recovery_box is not None and recovery_key != self._last_recovery:
            self._last_recovery = recovery_key
            self.paint_recovery()

    async def action(self, action, **values):
        try:
            result = await run.io_bound(lambda: bridge.request(action, timeout=60 if action in {'activate', 'submit_task'} else 5, **values))
            if isinstance(result, dict) and result.get('ok') is False:
                raise ValueError(result.get('message', '操作未完成，请查看诊断。'))
            await self.refresh()
            if action in {'start_components', 'recover_model_switch'} and isinstance(result, dict) and result.get('message'):
                ui.notify(result['message'], type='info', timeout=5000)
            return result
        except Exception as error:
            ui.notify(str(error), type='negative', timeout=6000)
            return None

    def header(self):
        self.status_label = ui.label('正在核验运行状态…').classes('text-xs text-slate-600 font-medium')

    def setup_banner(self):
        with ui.row().classes('w-full items-center justify-between gap-3 p-4 rounded-xl bg-orange-50 border border-orange-100'):
            with ui.column().classes('gap-1'):
                ui.label('开始使用 CapsWriter').classes('font-semibold text-slate-800')
                ui.label('安装本地模型后即可离线听写；已有设置会保留。').classes('text-xs text-slate-500')
            ui.button('首次设置 / 继续设置', icon='explore', on_click=self.open_wizard).props('outline color=amber-8')

    def model_center(self):
        self.model_box = ui.column().classes('w-full gap-4')
        with self.model_box:
            ui.label('正在读取模型和下载状态…').classes('text-sm text-slate-500')

    def paint_models(self):
        self.model_box.clear()
        with self.model_box:
            with ui.row().classes('items-center justify-between w-full'):
                ui.label('推荐与模型列表').classes('text-lg font-bold text-slate-900')
                ui.button('设置向导', icon='explore', on_click=self.open_wizard).props('flat color=amber-8')
            ui.label(self.state.get('hardware', {}).get('reason', '')).classes('text-sm text-slate-500')
            switch = self.state.get('switch', {})
            if switch.get('message'):
                ui.label(switch['message']).classes('text-sm text-amber-800')
            default = self.state.get('setup', {}).get('default_model')
            if default:
                ui.button('按现有配置重试加载', icon='refresh',
                          on_click=lambda: self.action('start_components')).props('flat color=amber-8')
            for item in self.state.get('models', []):
                with ui.card().classes('w-full p-4 rounded-xl shadow-none border border-slate-200 gap-2'):
                    with ui.row().classes('w-full justify-between items-center'):
                        with ui.column().classes('gap-1 flex-1'):
                            ui.label(item['name'] + (' · 推荐' if item.get('recommended') else '')).classes('font-semibold text-slate-800')
                            if item['id'] == default:
                                ui.label('当前默认模型（加载状态以上方为准）').classes('text-xs text-amber-700')
                            ui.label('文件已安装 · ' + ('安装时已校验' if item.get('installation_kind') == 'managed' else '手动文件，尚未校验')
                                     if item.get('installed') else '尚未安装').classes('text-xs text-slate-500')
                        if item.get('installed'):
                            ui.button('加载 / 切换', icon='play_arrow',
                                      on_click=lambda _, model=item: self.confirm_activate(model, first_setup=not self.state.get('setup', {}).get('default_model'))).props('outline color=amber-8')
                        elif item.get('can_download'):
                            ui.button('下载安装', icon='download',
                                      on_click=lambda _, model=item: self.confirm_download(model, first_setup=not self.state.get('setup', {}).get('default_model'))).props('color=amber-8')
                        else:
                            ui.button('下载暂未开放', icon='info').props('outline').disable()
                    ui.label(item.get('description') or item.get('disabled_reason', '')).classes('text-xs text-slate-500')
                    if item.get('can_download'):
                        ui.label(f"下载 {size_text(item.get('size_bytes'))} · 需要空间约 {size_text(item.get('max_unpacked_bytes', 0) + item.get('size_bytes', 0))}").classes('text-xs text-slate-500')
                    if item.get('installed_path'):
                        ui.label(item['installed_path']).classes('text-xs text-slate-400 break-all')
                    if not item.get('installed') and item['id'] != default:
                        ui.button('使用本地模型目录', icon='folder_open',
                                  on_click=lambda _, model=item: self.import_manual(model, first_setup=not self.state.get('setup', {}).get('default_model'))).props('flat color=grey-7')
                    elif item['id'] == default and not item.get('installed'):
                        ui.label('已有选择保留。请检查原模型文件、下方高级路径，或按现有配置重试；不会自动替换默认模型。').classes('text-xs text-amber-800')
            for job in self.state.get('downloads', []):
                with ui.card().classes('w-full p-4 shadow-none rounded-xl bg-slate-50 border border-slate-200 gap-2'):
                    ui.label(f"{job['model_id']} · {STATE_TEXT.get(job['status'], job['status'])}").classes('text-sm font-semibold')
                    total = job.get('bytes_total') or 1
                    ui.linear_progress(value=min(1, job.get('bytes_received', 0) / total)).props('color=amber-7')
                    ui.label(f"{size_text(job.get('bytes_received', 0))} / {size_text(total)} · 关闭窗口后后台继续").classes('text-xs text-slate-500')
                    if job.get('error'):
                        ui.label(job['error']).classes('text-xs text-red-600 break-all')
                    with ui.row().classes('gap-2'):
                        if job['status'] in {'downloading', 'queued'}:
                            ui.button('暂停', on_click=lambda _, ident=job['download_id']: self.action('pause_download', download_id=ident)).props('flat color=grey-7')
                        if job['status'] in {'paused', 'failed'}:
                            ui.button('继续 / 重试', on_click=lambda _, ident=job['download_id']: self.action('resume_download', download_id=ident)).props('flat color=amber-8')
                        if job['status'] not in {'completed', 'cancelled', 'cancelling'}:
                            ui.button('取消下载', on_click=lambda _, ident=job['download_id']: self.action('cancel_download', download_id=ident)).props('flat color=grey-7')

    def confirm_download(self, item, first_setup=False):
        with self.dialog_host, ui.dialog() as dialog, ui.card().classes('w-full max-w-xl p-6 gap-4'):
            ui.label('确认下载模型').classes('text-xl font-bold')
            ui.label(item['name']).classes('font-semibold')
            ui.label(f"下载 {size_text(item['size_bytes'])}；预留约 {size_text(item.get('max_unpacked_bytes', 0) + item['size_bytes'])}").classes('text-sm')
            ui.label('保存目录：' + self.state.get('model_root', '')).classes('text-xs text-slate-500 break-all')
            ui.link('查看模型来源', item['source_page'], new_tab=True)
            ui.link('阅读模型许可', item['license_url'], new_tab=True)
            ui.label(item.get('attribution', '')).classes('text-xs text-slate-500')
            ui.label('这是首次选择的模型，完成校验后会尝试加载。' if first_setup and not self.state.get('setup', {}).get('default_model')
                     else '下载完成后保留现有模型，需要你手动确认切换。').classes('text-sm text-slate-600')
            consent = ui.checkbox('我已阅读模型许可并同意联网下载上述模型')
            async def begin():
                if not consent.value:
                    ui.notify('请先阅读并确认模型许可。', type='warning')
                    return
                result = await self.action('download', model_id=item['id'], license_accepted=True,
                                           first_setup=first_setup)
                if result:
                    dialog.close()
                    if first_setup and self.wizard:
                        await self.go_step(3)
            with ui.row().classes('w-full justify-end'):
                ui.button('取消', on_click=dialog.close).props('flat')
                ui.button('开始下载', on_click=begin).props('color=amber-8')
        dialog.open()

    def confirm_activate(self, item, first_setup=False):
        with self.dialog_host, ui.dialog() as dialog, ui.card().classes('w-full max-w-lg p-6 gap-4'):
            ui.label('确认加载模型').classes('text-xl font-bold')
            ui.label(f"将加载 {item['name']}。当前模型会在空闲时停止；新模型加载失败将尝试恢复原模型并核验。").classes('text-sm text-slate-600')
            async def activate():
                result = await self.action('activate', model_id=item['id'], confirmed=True, first_setup=first_setup)
                if result:
                    dialog.close()
                    if first_setup and self.wizard:
                        await self.go_step(3)
            with ui.row().classes('w-full justify-end'):
                ui.button('取消', on_click=dialog.close).props('flat')
                ui.button('确认加载', on_click=activate).props('color=amber-8')
        dialog.open()

    def import_manual(self, item, first_setup=False):
        with self.dialog_host, ui.dialog() as dialog, ui.card().classes('w-full max-w-xl p-6 gap-4'):
            ui.label('使用本地模型目录').classes('text-xl font-bold')
            ui.label(item['name']).classes('font-semibold')
            ui.label('请选择已经解压的模型目录。这里只登记位置，不复制或删除原文件；手动文件不会标记为已校验。').classes('text-sm text-slate-600')
            ui.label('目录内需要：' + '、'.join(item.get('required_files', []))).classes('text-xs text-slate-500 break-all')
            directory = ui.input('完整目录路径', placeholder=r'D:\Models\Sensevoice-Small-ONNX').classes('w-full').props('outlined')
            async def register():
                result = await self.action('import_manual', model_id=item['id'],
                                           directory=(directory.value or '').strip(), confirmed=True)
                if result:
                    dialog.close()
                    self.confirm_activate(result, first_setup=first_setup)
            with ui.row().classes('w-full justify-end'):
                ui.button('取消', on_click=dialog.close).props('flat')
                ui.button('检查目录并登记', on_click=register).props('color=amber-8')
        dialog.open()

    def tasks_panel(self):
        with ui.column().classes('w-full gap-2'):
            ui.label('任务列表').classes('text-lg font-bold text-slate-900')
            ui.label('关闭窗口后可重新接回；中断任务从文件开头重试，保留原模型、参数及旧结果。').classes('text-xs text-slate-500')
            self.task_box = ui.column().classes('w-full gap-2')
            self.paint_tasks()

    def paint_tasks(self):
        self.task_box.clear()
        with self.task_box:
            records = self.state.get('tasks', [])
            if not records:
                ui.label('还没有转写任务。').classes('text-sm text-slate-400')
            for task in records[:50]:
                with ui.card().classes('w-full p-4 shadow-none border border-slate-200 rounded-xl gap-2'):
                    with ui.row().classes('w-full justify-between items-start'):
                        with ui.column().classes('gap-1 flex-1 min-w-0'):
                            ui.label(Path(task['source']['path']).name).classes('font-semibold break-all')
                            ui.label(f"{STATE_TEXT.get(task['state'], task['state'])} · {task['model_id']} · {task.get('percent', 0):.0f}%").classes('text-xs text-slate-500')
                        if task['state'] in {'failed', 'cancelled', 'interrupted', 'completed'}:
                            ui.button('从头重试', on_click=lambda _, ident=task['id']: self.action('retry_task', task_id=ident)).props('outline color=amber-8')
                        elif task['state'] != 'cancelling':
                            ui.button('安全停止', on_click=lambda _, ident=task['id']: self.action('cancel_task', task_id=ident)).props('flat color=grey-7')
                    ui.label(task.get('detail', '')).classes('text-xs text-slate-500 break-all')
                    if task.get('output_dir'):
                        ui.label('结果目录：' + task['output_dir']).classes('text-xs text-slate-500 break-all')
                        def open_result(path=task['output_dir']):
                            import os
                            if os.name == 'nt':
                                os.startfile(path)
                            else:
                                ui.notify(path)
                        ui.button('打开结果目录', icon='folder_open', on_click=open_result).props('flat color=amber-8')

    def open_wizard(self):
        if not self.state:
            ui.notify('正在连接后台，请稍候。', type='info')
            return
        if self.wizard:
            self.wizard.open()
            return
        setup = self.state.get('setup', {})
        self.wizard_step = min(6, max(1, int(setup.get('step', 1)))) if setup.get('status') not in {'complete', 'existing'} else 1
        with self.dialog_host, ui.dialog().props('persistent') as self.wizard:
            with ui.card().classes('w-[720px] max-w-[94vw] p-6 rounded-2xl gap-5'):
                with ui.row().classes('w-full justify-between items-center'):
                    ui.label('欢迎使用 CapsWriter').classes('text-xl font-bold text-slate-900')
                    ui.button('稍后继续', on_click=self.skip_wizard).props('flat color=grey-7')
                self.wizard_box = ui.column().classes('w-full gap-4')
                self.paint_wizard()
        self.wizard.open()

    async def skip_wizard(self):
        saved = await self.action('setup', values={'status': 'skipped', 'step': self.wizard_step})
        if saved is None:
            return
        self.wizard.close()
        self.wizard.delete()
        self.wizard = None
        self.wizard_box = None

    async def go_step(self, step):
        saved = await self.action('setup', values={'status': 'in_progress', 'step': step})
        if saved is None:
            return
        self.wizard_step = step
        self.paint_wizard()

    def paint_wizard(self):
        if self.wizard_box is None:
            return
        step = self.wizard_step
        self.wizard_box.clear()
        titles = ['了解设备', '选择本地模型', '下载、校验与加载', '麦克风测试', '快捷键与输出', '开始使用']
        with self.wizard_box:
            ui.label(f'{step} / 6 · {titles[step-1]}').classes('font-semibold text-amber-800')
            ui.linear_progress(value=step / 6).props('color=amber-7')
            if step == 1:
                hw = self.state.get('hardware', {})
                ui.label('日常识别在本地完成；模型首次下载需要联网。').classes('text-slate-700')
                ui.label(hw.get('reason', '')).classes('text-sm text-slate-500')
                with ui.expansion('查看硬件检测详情').classes('w-full'):
                    ui.label(f"内存 {size_text(hw.get('memory_bytes'))}；可用磁盘 {size_text(hw.get('free_bytes'))}").classes('text-xs')
                    ui.label('可用后端：' + ', '.join(hw.get('onnx_providers', []))).classes('text-xs')
                ui.button('继续', on_click=lambda: self.go_step(2)).props('color=amber-8')
            elif step == 2:
                models = self.state.get('models', [])
                for item in models:
                    with ui.row().classes('w-full items-center justify-between gap-3'):
                        ui.label(item['name'] + (' · 推荐' if item.get('recommended') else '')).classes('text-sm')
                        if item.get('installed'):
                            ui.button('使用此模型', on_click=lambda _, model=item: self.confirm_activate(model, first_setup=True)).props('outline color=amber-8')
                        elif item.get('can_download'):
                            ui.button('下载 ' + size_text(item.get('size_bytes')), on_click=lambda _, model=item: self.confirm_download(model, first_setup=True)).props('color=amber-8')
                        else:
                            ui.button('使用本地目录', icon='folder_open',
                                      on_click=lambda _, model=item: self.import_manual(model, first_setup=True)).props('flat color=grey-7')
                    if item.get('can_download') and not item.get('installed'):
                        ui.button('已有离线文件，选择目录',
                                  on_click=lambda _, model=item: self.import_manual(model, first_setup=True)).props('flat color=grey-7')
                ui.label('已有默认模型时，仍需确认切换。手动模型和高级参数可在模型中心处理。').classes('text-xs text-slate-500')
                ui.button('暂不安装，继续设置', on_click=lambda: self.go_step(4)).props('flat color=grey-7')
            elif step == 3:
                health = self.state.get('health', {})
                for job in self.state.get('downloads', [])[:2]:
                    ui.label(f"{STATE_TEXT.get(job['status'], job['status'])} · {size_text(job.get('bytes_received'))} / {size_text(job.get('bytes_total'))}").classes('text-sm')
                    if job.get('error'):
                        ui.label(job['error']).classes('text-xs text-red-600')
                ui.label(self.state.get('switch', {}).get('message', '下载完成后校验、安装；首次选择的模型会自动加载。')).classes('text-sm text-slate-600')
                if health.get('model_loaded') and self.state.get('switch', {}).get('state') != 'loading':
                    ui.button('模型已加载，继续', on_click=lambda: self.go_step(4)).props('color=amber-8')
                else:
                    ui.button('稍后处理模型，继续设置', on_click=lambda: self.go_step(4)).props('flat color=grey-7')
                ui.label('可关闭此向导，后台下载保持运行；不要选择“完全退出”。').classes('text-xs text-slate-500')
            elif step == 4:
                ui.label('点击后采集两秒音频，只检测本地输入信号，不保存、不上传。可跳过。').classes('text-sm text-slate-600')
                result = ui.label('测试状态：' + self.state.get('setup', {}).get('microphone_test', 'not_tested')).classes('text-sm')
                async def test():
                    result.set_text('正在采集，请说一句话…')
                    def capture():
                        import sounddevice as sd
                        import numpy as np
                        audio = sd.rec(32000, samplerate=16000, channels=1, dtype='float32')
                        sd.wait()
                        return float(np.max(np.abs(audio)))
                    try:
                        peak = await run.io_bound(capture)
                        value = 'detected_signal' if peak > 0.001 else 'no_signal'
                        result.set_text('已检测到麦克风信号' if peak > 0.001 else '没有检测到明显声音，请检查设备或重试。')
                        await self.action('setup', values={'microphone_test': value})
                    except Exception as error:
                        result.set_text('麦克风不可用：' + str(error))
                        await self.action('setup', values={'microphone_test': 'failed'})
                ui.button('测试麦克风', icon='mic', on_click=test).props('outline color=amber-8')
                ui.button('继续 / 跳过测试', on_click=lambda: self.go_step(5)).props('color=amber-8')
            elif step == 5:
                cfg = ConfigManager.get_all_config()
                ui.label('沿用现有快捷键和输入模式，完整选项仍在“通用与交互”。').classes('text-sm text-slate-600')
                ui.label('当前快捷键：' + str(cfg.get('shortcut', 'CapsLock'))).classes('font-semibold')
                ui.label('输出模式：' + ('确认后写入' if cfg.get('output_destination') == 'overlay_preview' else '直接写入')).classes('text-sm')
                ui.label('需要在线 AI 时请到 AI 页面主动启用；文字会发送到你配置的提供商。').classes('text-xs text-slate-500')
                ui.button('继续', on_click=lambda: self.go_step(6)).props('color=amber-8')
            else:
                ready = self.state.get('health', {}).get('can_dictate')
                ui.label('已准备好听写' if ready else '设置已保存，模型或麦克风仍需处理').classes('text-lg font-semibold')
                ui.label('按住设置的快捷键说话，松开后识别。若使用单击模式，再按一次结束。').classes('text-sm')
                ui.label('关闭窗口后可从托盘或再次打开快捷方式回来。').classes('text-xs text-slate-500')
                async def finish():
                    saved = await self.action('setup', values={'status': 'complete', 'step': 6})
                    if saved is None:
                        return
                    self.wizard.close()
                    self.wizard.delete()
                    self.wizard = None
                    self.wizard_box = None
                ui.button('进入控制中心', on_click=finish).props('color=amber-8')
            if step not in (1, 3):
                ui.button('上一步', on_click=lambda: self.go_step(step - 1)).props('flat color=grey-7')

    def diagnostics(self):
        with ui.card().classes('w-full p-5 gap-3 rounded-xl shadow-none border border-slate-200'):
            ui.label('版本与恢复').classes('font-semibold text-slate-900')
            from core.product.updates import current_version, check_for_update
            ui.label('CapsWriter V1 · ' + current_version()).classes('text-sm text-slate-500')
            update_result = ui.column().classes('gap-2 w-full')
            async def check():
                update_result.clear()
                with update_result:
                    ui.label('正在检查…').classes('text-sm')
                result = await run.io_bound(check_for_update)
                update_result.clear()
                with update_result:
                    ui.label(result.get('message', result['status'])).classes('text-sm')
                    if result.get('release_url'):
                        ui.link('查看可信发行页面', result['release_url'], new_tab=True)
                    if result.get('notes'):
                        ui.label(result['notes'][:6000]).classes('text-xs whitespace-pre-wrap')
            with ui.row().classes('gap-3'):
                ui.button('手动检查更新', icon='system_update', on_click=check).props('outline color=amber-8')
                ui.button('重新连接后台', icon='refresh', on_click=self.initialize).props('flat color=grey-7')
                def export_summary():
                    from core.product.diagnostics import diagnostic_summary
                    body = json.dumps(diagnostic_summary(self.state), ensure_ascii=False, indent=2).encode('utf-8')
                    ui.download(body, 'capswriter-diagnostics.json', 'application/json')
                ui.button('导出诊断摘要', icon='download', on_click=export_summary).props('flat color=grey-7')
            ui.label('更新前保留配置和模型；本程序不会自动安装或覆盖当前版本。').classes('text-xs text-slate-500')
            ui.label('诊断摘要只含版本、运行状态和任务数量，不包含录音、识别文本、文件路径、密钥或日志原文。').classes('text-xs text-slate-500')
            from web_gui.private_config import load_private_config
            private_error = load_private_config().get('_error')
            if private_error:
                ui.label(private_error).classes('text-sm text-red-700')
            self.recovery_box = ui.column().classes('w-full gap-2')
            self.paint_recovery()
            pending = ConfigManager.pending_config_recovery()
            for journal in pending:
                ui.label('发现未完成的配置导入：' + str(journal)).classes('text-sm text-amber-800')
                def recover(item=journal):
                    path = item.get('journal') or item.get('path') if isinstance(item, dict) else item
                    ok, message = ConfigManager.recover_config_import(path)
                    ui.notify(message, type='positive' if ok else 'negative')
                ui.button('恢复导入前配置', on_click=recover).props('outline color=amber-8')

    def paint_recovery(self):
        self.recovery_box.clear()
        with self.recovery_box:
            for journal in self.state.get('model_recovery', []):
                ui.label('发现未完成的模型切换，当前加载已暂停保护。').classes('text-sm text-amber-800')
                ui.label(str(journal.get('journal', ''))).classes('text-xs text-slate-500 break-all')
                ui.button('恢复切换前配置',
                          on_click=lambda _, item=journal: self.action('recover_model_switch', journal=item['journal'])).props('outline color=amber-8')
