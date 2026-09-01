# coding: utf-8
"""AI settings panel for API profiles, default polish, and role bindings."""

from __future__ import annotations

from nicegui import run, ui

from web_gui.config_manager import ConfigManager
from web_gui.private_config import mask_key

ROLE_OUTPUT_OPTIONS = {
    'typing': '直接写入光标位置',
    'overlay_preview': '确认后写入',
}

PREVIEW_CLOSE_OPTIONS = {
    'auto': '按文本长度自动关闭',
    'manual': '手动关闭',
}


def _dedupe_models(models: list[str] | tuple[str, ...] | None, current: str | None = None) -> list[str]:
    values: list[str] = []
    for model in list(models or []) + ([current] if current else []):
        model = str(model or '').strip()
        if model and model not in values:
            values.append(model)
    return values


def _profile_options(profiles: list[dict]) -> dict:
    return {profile['id']: profile.get('name') or profile['id'] for profile in profiles}


def _active_profile(profiles_data: dict) -> dict | None:
    profiles = profiles_data.get('profiles', [])
    active_id = profiles_data.get('active_profile')
    return next((p for p in profiles if p.get('id') == active_id), None) or (profiles[0] if profiles else None)


def _profile_summary(profile: dict | None) -> str:
    if not profile:
        return '未绑定 API 配置'
    provider = ConfigManager.provider_options().get(profile.get('provider'), profile.get('provider') or '未知服务商')
    model = profile.get('default_model') or ((profile.get('models') or [''])[0] if profile.get('models') else '')
    return f'{profile.get("name") or profile.get("id")} · {provider} · {model or "未设置默认模型"}'


def _provider_default_models(provider: str) -> list[str]:
    defaults = {
        'deepseek': ['deepseek-v4-flash', 'deepseek-v4-pro'],
        'openai': [],
        'ollama': [],
        'lmstudio': [],
        'moonshot': [],
        'zhipu': [],
        'volcengine': [],
        'cerebras': [],
        'custom': [],
    }
    return defaults.get(provider, [])


def _refresh_select_options(select, options: list[str], value: str | None = None) -> None:
    clean_options = _dedupe_models(options)
    select.options = clean_options
    if value and value in clean_options:
        select.value = value
    else:
        select.value = clean_options[0] if clean_options else None
    select.update()


def _update_profile_models(profile_id: str, model: str) -> None:
    model = (model or '').strip()
    if not profile_id or not model:
        return
    data = ConfigManager.load_llm_profiles()
    for profile in data.get('profiles', []):
        if profile.get('id') == profile_id:
            profile['models'] = _dedupe_models(profile.get('models'), model)
            if not profile.get('default_model'):
                profile['default_model'] = model
            ConfigManager.save_llm_profiles(data)
            return


def _normalized_role_output_mode(value: str | None) -> str:
    if value == 'toast':
        return 'overlay_preview'
    return value or 'inherit'


def render_ai_panel() -> None:
    search_state = {'text': ''}

    def get_profiles(include_keys: bool = False) -> dict:
        return ConfigManager.load_llm_profiles(include_keys=include_keys)

    def has_profiles() -> bool:
        return bool(get_profiles().get('profiles'))

    def open_api_manager() -> None:
        with ui.dialog() as dialog:
            with ui.card().classes('w-[980px] max-w-[92vw] max-h-[88vh] p-0 overflow-hidden rounded-xl'):
                with ui.row().classes('items-center justify-between w-full px-6 py-4 border-b border-slate-100'):
                    with ui.column().classes('gap-0'):
                        ui.label('API 配置管理').classes('text-xl font-bold text-slate-900')
                        ui.label('管理 API 配置档案：服务商、Base URL、API Key 和模型列表。Key 只保存在本机。').classes('text-xs text-slate-500')
                    ui.button(icon='close', on_click=dialog.close).props('flat round dense')

                api_list = ui.column().classes('gap-3 w-full p-6 overflow-y-auto')

                def render_api_list() -> None:
                    api_list.clear()
                    data = get_profiles(include_keys=True)
                    profiles = data.get('profiles', [])
                    active_id = data.get('active_profile')

                    with api_list:
                        with ui.row().classes('items-center justify-between w-full gap-3 flex-wrap'):
                            ui.label(f'共 {len(profiles)} 套 API 配置').classes('text-sm text-slate-500')
                            ui.button('添加 API 配置', icon='add', on_click=lambda: open_profile_editor(None, render_api_list)).classes('bg-amber-600 text-white px-4')

                        if not profiles:
                            with ui.card().classes('w-full p-6 rounded-lg bg-amber-50 border border-amber-200 shadow-none'):
                                ui.label('还没有 API 配置').classes('font-semibold text-amber-900')
                                ui.label('离线语音转文字仍可正常使用；AI 润色和 AI 角色会等你添加 API 后再启用。').classes('text-sm text-amber-800')

                        for profile in profiles:
                            provider_name = ConfigManager.provider_options().get(profile.get('provider'), profile.get('provider'))
                            models = profile.get('models') or []
                            with ui.card().classes('w-full p-4 rounded-lg bg-slate-50 border border-slate-200 shadow-none'):
                                with ui.row().classes('items-center justify-between w-full gap-4 flex-wrap'):
                                    with ui.column().classes('gap-1 min-w-0'):
                                        with ui.row().classes('items-center gap-2 flex-wrap'):
                                            ui.label(profile.get('name') or profile['id']).classes('font-semibold text-slate-900')
                                            if profile['id'] == active_id:
                                                ui.badge('默认', color='amber-2').classes('text-amber-800 border border-amber-200')
                                        url_label = profile.get('api_url') or ConfigManager.default_api_url(profile.get('provider')) or '未填写地址'
                                        ui.label(f'{provider_name} · {url_label} · {len(models)} 个模型 · Key {mask_key(profile.get("api_key", ""))}').classes('text-xs text-slate-500')
                                    with ui.row().classes('items-center gap-2'):
                                        ui.button('编辑', icon='edit', on_click=lambda p=profile: open_profile_editor(p, render_api_list)).props('outline color=grey-7').classes('bg-white')

                                        def set_active(p=profile):
                                            data = get_profiles()
                                            data['active_profile'] = p['id']
                                            ConfigManager.save_llm_profiles(data)
                                            ui.notify('默认 API 配置已更新。', type='positive')
                                            render_api_list()
                                            main_content.refresh()

                                        ui.button('设为默认', icon='star', on_click=set_active).props('outline color=amber-8').classes('bg-white')
                                        ui.button('删除', icon='delete', on_click=lambda p=profile: open_delete_profile_dialog(p, render_api_list)).props('outline color=red-7').classes('bg-white')

                render_api_list()
        dialog.open()

    def open_profile_editor(profile: dict | None, after_save=None) -> None:
        is_new = profile is None
        source = profile or {
            'name': '新的 API 配置',
            'provider': 'deepseek',
            'api_url': '',
            'models': ['deepseek-v4-flash', 'deepseek-v4-pro'],
            'default_model': 'deepseek-v4-flash',
            'api_key': '',
        }
        model_state = {'models': _dedupe_models(source.get('models'), source.get('default_model'))}

        with ui.dialog() as dialog:
            with ui.card().classes('w-[760px] max-w-[92vw] max-h-[88vh] p-6 rounded-xl gap-5 overflow-y-auto'):
                ui.label('添加 API 配置' if is_new else '编辑 API 配置').classes('text-xl font-bold text-slate-900')
                ui.label('官方 API 可以留空 Base URL；中转站请选择自定义兼容接口，并填写它提供的访问地址。').classes('text-sm text-slate-500')

                name_in = ui.input('配置名称', value=source.get('name', '')).classes('w-full')
                provider_in = ui.select(ConfigManager.provider_options(), value=source.get('provider', 'deepseek'), label='服务提供商').classes('w-full')
                default_url_label = ui.label('').classes('text-xs text-slate-500')
                api_url_in = ui.input('Base URL / 请求地址', value=source.get('api_url', ''), placeholder='留空使用服务商官方默认地址').classes('w-full')
                api_key_in = ui.input('API Key', value=source.get('api_key', ''), password=True, password_toggle_button=True).classes('w-full')

                default_model_in = ui.select(
                    model_state['models'],
                    value=source.get('default_model') or (model_state['models'][0] if model_state['models'] else None),
                    label='默认模型（可输入或从接口拉取）',
                    new_value_mode='add-unique',
                ).classes('w-full')
                model_count = ui.label(f'当前模型列表：{len(model_state["models"])} 个').classes('text-xs text-slate-500')

                with ui.row().classes('items-end gap-2 w-full'):
                    manual_model = ui.input('手动添加模型', placeholder='例如 deepseek-v4-flash').classes('flex-1')

                    def add_manual_model() -> None:
                        model = (manual_model.value or '').strip()
                        if not model:
                            return
                        model_state['models'] = _dedupe_models(model_state['models'], model)
                        _refresh_select_options(default_model_in, model_state['models'], model)
                        model_count.text = f'当前模型列表：{len(model_state["models"])} 个'
                        manual_model.value = ''
                        ui.notify(f'已加入模型：{model}', type='positive')

                    ui.button('加入列表', icon='add', on_click=add_manual_model).props('outline color=amber-8').classes('bg-white')

                def update_provider_hint() -> None:
                    provider = provider_in.value
                    if provider == 'custom':
                        default_url_label.text = '中转站模式：Base URL 必填，通常类似 https://example.com/v1，请按中转站文档填写。'
                        api_url_in.props('placeholder=例如 https://example.com/v1')
                    else:
                        default_url = ConfigManager.default_api_url(provider)
                        default_url_label.text = f'官方默认地址：{default_url or "未内置默认地址"}；填写 Base URL 会覆盖默认地址。'

                def handle_provider_change(e) -> None:
                    provider = e.value if hasattr(e, 'value') else provider_in.value
                    update_provider_hint()
                    api_url_in.value = ''
                    model_state['models'] = _provider_default_models(provider)
                    _refresh_select_options(default_model_in, model_state['models'])
                    model_count.text = f'当前模型列表：{len(model_state["models"])} 个'
                    ui.notify('服务商已切换，已清空旧地址并刷新模型列表。可重新拉取模型。', type='info')

                provider_in.on_value_change(handle_provider_change)
                update_provider_hint()

                async def pull_models() -> None:
                    if provider_in.value == 'custom' and not (api_url_in.value or '').strip():
                        ui.notify('中转站配置需要先填写 Base URL。', type='warning')
                        return
                    ui.notify('正在拉取模型列表...', type='info')
                    ok, result = await run.io_bound(
                        ConfigManager.fetch_llm_models,
                        provider_in.value,
                        api_url_in.value,
                        api_key_in.value,
                    )
                    if ok:
                        model_state['models'] = _dedupe_models(result, default_model_in.value)
                        _refresh_select_options(default_model_in, model_state['models'], default_model_in.value)
                        model_count.text = f'当前模型列表：{len(model_state["models"])} 个'
                        ui.notify(f'已拉取 {len(model_state["models"])} 个模型。', type='positive')
                    else:
                        ui.notify(str(result), type='negative')

                def save_profile() -> None:
                    if provider_in.value == 'custom' and not (api_url_in.value or '').strip():
                        ui.notify('中转站配置必须填写 Base URL。', type='warning')
                        return
                    selected_model = (default_model_in.value or '').strip()
                    models = _dedupe_models(model_state['models'], selected_model)
                    profile_id = ConfigManager.upsert_llm_profile(
                        {
                            'id': source.get('id'),
                            'name': name_in.value,
                            'provider': provider_in.value,
                            'api_url': api_url_in.value,
                            'models': models,
                            'default_model': selected_model or (models[0] if models else ''),
                        },
                        api_key=api_key_in.value,
                        set_active=is_new,
                    )
                    ui.notify(f'API 配置已保存：{profile_id}', type='positive')
                    dialog.close()
                    if after_save:
                        after_save()
                    main_content.refresh()

                with ui.row().classes('items-center justify-end gap-2 w-full'):
                    ui.button('拉取模型', icon='cloud_download', on_click=pull_models).props('outline color=amber-8').classes('bg-white')
                    ui.button('取消', on_click=dialog.close).props('flat')
                    ui.button('保存', icon='save', on_click=save_profile).classes('bg-amber-600 text-white px-6')
        dialog.open()

    def open_delete_profile_dialog(profile: dict, after_delete=None) -> None:
        data = get_profiles()
        is_last = len(data.get('profiles', [])) <= 1
        with ui.dialog() as dialog:
            with ui.card().classes('w-[540px] max-w-[92vw] p-6 rounded-xl gap-4'):
                ui.label('删除 API 配置').classes('text-xl font-bold text-red-700')
                warning = f'确定删除“{profile.get("name") or profile["id"]}”吗？'
                if is_last:
                    warning += ' 这是最后一套 API 配置；删除后离线语音转文字仍可使用，但 AI 润色和 AI 角色会暂时不可用。'
                else:
                    warning += ' 已绑定到它的角色之后会显示为未绑定，需要重新选择 API 配置。'
                ui.label(warning).classes('text-sm text-slate-600')

                def delete() -> None:
                    ConfigManager.delete_llm_profile(profile['id'])
                    ui.notify('API 配置已删除。', type='positive')
                    dialog.close()
                    if after_delete:
                        after_delete()
                    main_content.refresh()

                with ui.row().classes('items-center justify-end gap-2 w-full'):
                    ui.button('取消', on_click=dialog.close).props('flat')
                    ui.button('确认删除', icon='delete', on_click=delete).props('color=red-7')
        dialog.open()

    def open_role_editor(role: dict | None = None) -> None:
        profiles_data = get_profiles()
        profiles = profiles_data.get('profiles', [])
        active = _active_profile(profiles_data)
        options = _profile_options(profiles)
        is_new = role is None
        selected_profile = ConfigManager.get_llm_profile((role or {}).get('profile_id')) or active
        selected_profile_id = selected_profile['id'] if selected_profile else None
        profile_models = _dedupe_models((selected_profile or {}).get('models'))
        raw_selected_model = (role or {}).get('model')
        if raw_selected_model and raw_selected_model in profile_models:
            selected_model = raw_selected_model
        else:
            selected_model = (selected_profile or {}).get('default_model') or (profile_models[0] if profile_models else '')
        model_options = profile_models
        role_output_mode = _normalized_role_output_mode((role or {}).get('output_mode'))
        use_role_overlay_settings = role_output_mode != 'inherit'
        section_close_mode = ConfigManager.get_client_var('llm_role_preview_close_mode', 'auto')
        section_base_seconds = ConfigManager.get_client_var('llm_role_preview_base_seconds', 2)
        section_max_seconds = ConfigManager.get_client_var('llm_role_preview_max_seconds', 10)

        with ui.dialog() as dialog:
            with ui.card().classes('w-[920px] max-w-[94vw] max-h-[90vh] p-0 overflow-hidden rounded-xl'):
                with ui.row().classes('items-center justify-between w-full px-6 py-4 border-b border-slate-100'):
                    with ui.column().classes('gap-0'):
                        ui.label('添加 AI 角色' if is_new else f'编辑角色：{role["display_name"]}').classes('text-xl font-bold text-slate-900')
                        ui.label('角色由触发词唤醒，比如“翻译 这句话”。角色只绑定已有 API 配置，不在这里重复填 Key。').classes('text-xs text-slate-500')
                    ui.button(icon='close', on_click=dialog.close).props('flat round dense')

                with ui.column().classes('gap-5 w-full p-6 overflow-y-auto'):
                    name_in = ui.input('触发词 / 别名（用 | 分隔）', value=(role or {}).get('name', '新角色')).classes('w-full')
                    if not profiles:
                        ui.label('当前没有 API 配置。可以先保存角色，但启用角色前需要添加 API 配置。').classes('text-sm text-amber-700')

                    with ui.grid(columns=2).classes('w-full gap-5'):
                        profile_select = ui.select(options, value=selected_profile_id, label='使用 API 配置').classes('w-full')
                        model_select = ui.select(
                            model_options,
                            value=selected_model or (model_options[0] if model_options else None),
                            label='使用模型',
                            new_value_mode='add-unique',
                        ).classes('w-full')
                        max_context = ui.number('上下文长度', value=(role or {}).get('max_context_length', 4096), min=512, max=1048576, step=512).classes('w-full')
                        selection_max = ui.number('读取选中文字上限', value=(role or {}).get('selection_max_length', 1000), min=0, max=1048576, step=256).classes('w-full')
                        temperature = ui.number('Temperature', value=(role or {}).get('temperature', 0.7), min=0, max=2, step=0.1).classes('w-full')
                        max_tokens = ui.number('最大输出 Token', value=(role or {}).get('max_tokens', 4096), min=256, max=131072, step=256).classes('w-full')

                    def update_role_models(e) -> None:
                        profile = ConfigManager.get_llm_profile(e.value)
                        models = _dedupe_models((profile or {}).get('models'))
                        default_m = (profile or {}).get('default_model') or (models[0] if models else None)
                        _refresh_select_options(model_select, models, default_m)

                    profile_select.on_value_change(update_role_models)

                    with ui.row().classes('items-center gap-5 flex-wrap'):
                        hotwords_switch = ui.switch('注入热词', value=(role or {}).get('enable_hotwords', False)).props('color=amber-8')
                        history_switch = ui.switch('保留上下文', value=(role or {}).get('enable_history', True)).props('color=amber-8')
                        selection_switch = ui.switch('读取选中文字', value=(role or {}).get('enable_read_selection', False)).props('color=amber-8')
                        thinking_switch = ui.switch('启用思考参数', value=(role or {}).get('enable_thinking', False)).props('color=amber-8')

                    with ui.card().classes('w-full p-5 rounded-lg bg-slate-50 border border-slate-200 shadow-none gap-4'):
                        with ui.row().classes('items-center justify-between w-full gap-4 flex-wrap'):
                            with ui.column().classes('gap-1 min-w-0'):
                                ui.label('浮层设置').classes('font-semibold text-slate-900')
                                ui.label('关闭独立设置时，这个角色会跟随 AI 角色总浮层设置。').classes('text-xs text-slate-500')
                            role_overlay_switch = ui.switch('使用独立浮层设置', value=use_role_overlay_settings).props('color=amber-8')

                        inherited_overlay_note = ui.label('当前跟随 AI 角色总浮层设置。').classes('text-sm text-slate-500')
                        with ui.column().classes('gap-4 w-full') as role_overlay_container:
                            role_output_select = ui.select(
                                ROLE_OUTPUT_OPTIONS,
                                value=role_output_mode if role_output_mode in ROLE_OUTPUT_OPTIONS else 'typing',
                                label='结果写入方式',
                            ).classes('w-full')
                            with ui.column().classes('gap-4 w-full') as role_preview_settings_container:
                                role_close_select = ui.select(
                                    PREVIEW_CLOSE_OPTIONS,
                                    value=(role or {}).get('preview_close_mode') or section_close_mode,
                                    label='确认浮层关闭方式',
                                ).classes('w-full')
                                with ui.grid(columns=2).classes('w-full gap-4') as role_timing_row:
                                    role_base_seconds = ui.number(
                                        '基础停留秒数',
                                        value=(role or {}).get('preview_base_seconds') or section_base_seconds,
                                        min=2,
                                        max=60,
                                        step=1,
                                    ).classes('w-full')
                                    role_max_seconds = ui.number(
                                        '最长停留秒数',
                                        value=(role or {}).get('preview_max_seconds') or section_max_seconds,
                                        min=2,
                                        max=120,
                                        step=1,
                                    ).classes('w-full')

                        def sync_role_overlay_visibility() -> None:
                            enabled = bool(role_overlay_switch.value)
                            preview_enabled = role_output_select.value == 'overlay_preview'
                            role_overlay_container.set_visibility(enabled)
                            inherited_overlay_note.set_visibility(not enabled)
                            role_preview_settings_container.set_visibility(enabled and preview_enabled)
                            role_timing_row.set_visibility(enabled and preview_enabled and role_close_select.value == 'auto')

                        role_overlay_switch.on_value_change(lambda _: sync_role_overlay_visibility())
                        role_output_select.on_value_change(lambda _: sync_role_overlay_visibility())
                        role_close_select.on_value_change(lambda _: sync_role_overlay_visibility())
                        sync_role_overlay_visibility()

                    system_prompt = ui.textarea('角色提示词 / 人设', value=(role or {}).get('system_prompt', '')).classes('w-full min-h-56 bg-white font-mono')

                    def save_role() -> None:
                        try:
                            profile_id = profile_select.value or ''
                            selected = model_select.value or ''
                            if profile_id and selected:
                                _update_profile_models(profile_id, selected)
                            if is_new:
                                stem = ConfigManager.create_llm_role(
                                    name=name_in.value,
                                    profile_id=profile_id,
                                    model=selected,
                                    system_prompt=system_prompt.value,
                                )
                            else:
                                stem = role['stem']
                            if role_overlay_switch.value:
                                output_mode = role_output_select.value or 'overlay_preview'
                                if output_mode == 'overlay_preview':
                                    preview_close_mode = role_close_select.value or section_close_mode
                                    preview_base_seconds = int(role_base_seconds.value or section_base_seconds)
                                    preview_max_seconds = int(role_max_seconds.value or section_max_seconds)
                                    if preview_max_seconds < preview_base_seconds:
                                        preview_max_seconds = preview_base_seconds
                                else:
                                    preview_close_mode = ''
                                    preview_base_seconds = 0
                                    preview_max_seconds = 0
                            else:
                                output_mode = 'inherit'
                                preview_close_mode = ''
                                preview_base_seconds = 0
                                preview_max_seconds = 0
                            ConfigManager.set_llm_role_config(
                                stem,
                                name=name_in.value,
                                profile_id=profile_id,
                                model=selected,
                                enable_hotwords=bool(hotwords_switch.value),
                                enable_history=bool(history_switch.value),
                                enable_read_selection=bool(selection_switch.value),
                                enable_thinking=bool(thinking_switch.value),
                                max_context_length=int(max_context.value or 4096),
                                selection_max_length=int(selection_max.value or 1000),
                                temperature=float(temperature.value or 0.7),
                                max_tokens=int(max_tokens.value or 4096),
                                output_mode=output_mode,
                                preview_close_mode=preview_close_mode,
                                preview_base_seconds=preview_base_seconds,
                                preview_seconds_per_20_chars=1 if preview_base_seconds else 0,
                                preview_max_seconds=preview_max_seconds,
                                system_prompt=system_prompt.value,
                            )
                            main_content.refresh()
                            dialog.close()
                            ui.notify('角色配置已保存，列表已刷新。下一次角色调用生效。', type='positive')
                        except Exception as err:
                            ui.notify(f'保存角色失败：{err}', type='negative')

                    def ask_delete_role() -> None:
                        with ui.dialog() as confirm:
                            with ui.card().classes('w-[520px] max-w-[92vw] p-6 rounded-xl gap-4'):
                                ui.label('删除 AI 角色').classes('text-xl font-bold text-red-700')
                                ui.label(f'确定删除“{role["display_name"]}”吗？会删除对应的 LLM/{role["file"]}，删除前会自动备份。').classes('text-sm text-slate-600')

                                def delete_role() -> None:
                                    if ConfigManager.delete_llm_role(role['stem']):
                                        ui.notify('角色已删除。', type='positive')
                                        confirm.close()
                                        dialog.close()
                                        main_content.refresh()
                                    else:
                                        ui.notify('删除失败。', type='negative')

                                with ui.row().classes('items-center justify-end gap-2 w-full'):
                                    ui.button('取消', on_click=confirm.close).props('flat')
                                    ui.button('确认删除', icon='delete', on_click=delete_role).props('color=red-7')
                        confirm.open()

                    with ui.row().classes('items-center justify-between gap-2 w-full'):
                        if not is_new:
                            ui.button('删除角色', icon='delete', on_click=ask_delete_role).props('outline color=red-7').classes('bg-white')
                        else:
                            ui.space()
                        with ui.row().classes('items-center gap-2'):
                            ui.button('取消', on_click=dialog.close).props('flat')
                            ui.button('保存角色', icon='save', on_click=save_role).classes('bg-amber-600 text-white px-6')
        dialog.open()

    @ui.refreshable
    def main_content() -> None:
        profiles_data = get_profiles()
        profiles = profiles_data.get('profiles', [])
        active = _active_profile(profiles_data)
        profile_options = _profile_options(profiles)
        llm_cfg = ConfigManager.get_llm_default_config()
        selected_default = ConfigManager.get_llm_profile(llm_cfg.get('profile_id')) or active
        roles = ConfigManager.list_llm_roles()
        query = search_state['text'].strip().lower()
        filtered_roles = [
            role for role in roles
            if not query
            or query in str(role.get('display_name', '')).lower()
            or query in str(role.get('name', '')).lower()
            or query in str(role.get('file', '')).lower()
            or query in str(role.get('profile_name', '')).lower()
        ]

        with ui.column().classes('gap-6 w-full pb-8'):
            with ui.row().classes('items-start justify-between gap-4 border-b border-slate-100 dark:border-slate-800 pb-4 w-full flex-wrap'):
                with ui.column().classes('gap-1'):
                    ui.label('AI 润色与角色').classes('text-2xl font-bold text-slate-900 dark:text-white')
                    ui.label('API 配置、默认润色和角色人设分开管理。没有 API 配置时，离线听写仍可正常使用。').classes('text-sm text-slate-500 dark:text-slate-400')
                ui.button('管理 API 配置', icon='vpn_key', on_click=open_api_manager).props('outline color=amber-8').classes('bg-white px-4')

            with ui.card().classes('w-full p-6 rounded-xl bg-slate-50 border border-slate-200 shadow-none gap-5'):
                with ui.row().classes('items-center justify-between w-full gap-4 flex-wrap'):
                    with ui.column().classes('gap-1'):
                        ui.label('默认 AI 润色').classes('text-lg font-bold text-slate-900')
                        ui.label('开启后，未命中角色触发词的普通听写会使用这里的 API 和模型进行润色。').classes('text-sm text-slate-500')
                    llm_switch = ui.switch(value=ConfigManager.get_llm_enabled()).props('color=amber-8')

                    def handle_enabled_change(e) -> None:
                        if e.value and not has_profiles():
                            ConfigManager.set_llm_enabled(False)
                            llm_switch.value = False
                            llm_switch.update()
                            ui.notify('还没有 API 配置，无法开启 AI 润色。请点击右上角“管理 API 配置”添加。', type='warning')
                            return
                        ConfigManager.set_llm_enabled(bool(e.value))
                        ui.notify('AI 润色开关已保存，下一次听写生效。', type='positive')

                    llm_switch.on_value_change(handle_enabled_change)

                if profiles:
                    cur_profile = selected_default or active
                    profile_models = _dedupe_models((cur_profile or {}).get('models'))
                    cfg_model = llm_cfg.get('model')
                    if cfg_model and cfg_model in profile_models:
                        initial_model = cfg_model
                    else:
                        initial_model = (cur_profile or {}).get('default_model') or (profile_models[0] if profile_models else None)

                    with ui.grid(columns=2).classes('w-full gap-5'):
                        profile_select = ui.select(profile_options, value=(cur_profile or {})['id'], label='使用 API 配置').classes('w-full')
                        model_select = ui.select(
                            profile_models,
                            value=initial_model,
                            label='使用模型',
                            new_value_mode='add-unique',
                        ).classes('w-full')

                    def save_default_binding() -> None:
                        if not profile_select.value:
                            return
                        if model_select.value:
                            _update_profile_models(profile_select.value, model_select.value)
                        if not ConfigManager.apply_llm_profile_to_default(profile_select.value, model_select.value):
                            ui.notify('API 配置不存在，请重新选择。', type='negative')

                    def update_default_models(e) -> None:
                        profile = ConfigManager.get_llm_profile(e.value)
                        models = _dedupe_models((profile or {}).get('models'))
                        default_m = (profile or {}).get('default_model') or (models[0] if models else None)
                        _refresh_select_options(model_select, models, default_m)
                        save_default_binding()

                    profile_select.on_value_change(update_default_models)
                    model_select.on_value_change(lambda _: save_default_binding())
                else:
                    with ui.card().classes('w-full p-5 rounded-lg bg-amber-50 border border-amber-200 shadow-none'):
                        ui.label('当前没有 API 配置。默认 AI 润色暂不可用，但离线语音转文字不受影响。').classes('text-sm text-amber-800')

            with ui.card().classes('w-full p-6 rounded-xl bg-slate-50 border border-slate-200 shadow-none gap-5'):
                with ui.row().classes('items-start justify-between w-full gap-4 flex-wrap'):
                    with ui.column().classes('gap-1 min-w-0'):
                        ui.label('AI 角色浮层设置').classes('text-lg font-bold text-slate-900')
                        ui.label('未启用独立浮层设置的 AI 角色，会统一使用这里的写入方式和确认浮层停留方式。').classes('text-sm text-slate-500')

                def sync_section_preview_visibility() -> None:
                    enabled = role_output_destination_select.value == 'overlay_preview'
                    auto_close = role_preview_close_select.value == 'auto'
                    role_preview_settings_container.set_visibility(enabled)
                    role_preview_timing_row.set_visibility(enabled and auto_close)

                def save_role_output_destination(e) -> None:
                    ConfigManager.set_client_var('llm_role_output_destination', e.value)
                    sync_section_preview_visibility()
                    ui.notify('AI 角色写入方式已保存，下一次角色调用生效。', type='positive')

                def save_role_preview_close(e) -> None:
                    ConfigManager.set_client_var('llm_role_preview_close_mode', e.value)
                    sync_section_preview_visibility()
                    ui.notify('AI 角色确认浮层关闭方式已保存，下一次角色调用生效。', type='positive')

                with ui.row().classes('items-center justify-between w-full gap-4 flex-wrap'):
                    with ui.column().classes('gap-0.5 min-w-0'):
                        ui.label('结果写入方式').classes('font-semibold text-slate-900 text-base')
                        ui.label('直接写入会把 AI 结果输入到光标位置；确认后写入会先显示可编辑浮层。').classes('text-xs text-slate-500')
                    role_output_destination_select = ui.select(
                        ROLE_OUTPUT_OPTIONS,
                        value=ConfigManager.get_client_var('llm_role_output_destination', 'overlay_preview'),
                        label='结果写入方式',
                        on_change=save_role_output_destination,
                    ).classes('w-56')

                with ui.column().classes('gap-5 w-full') as role_preview_settings_container:
                    with ui.row().classes('items-center justify-between w-full gap-4 flex-wrap'):
                        with ui.column().classes('gap-0.5 min-w-0'):
                            ui.label('确认浮层关闭方式').classes('font-semibold text-slate-900 text-base')
                            ui.label('自动模式会按文本长度延长停留；点进文本框编辑后会暂停自动关闭。').classes('text-xs text-slate-500')
                        role_preview_close_select = ui.select(
                            PREVIEW_CLOSE_OPTIONS,
                            value=ConfigManager.get_client_var('llm_role_preview_close_mode', 'auto'),
                            label='确认浮层关闭方式',
                            on_change=save_role_preview_close,
                        ).classes('w-56')

                    with ui.row().classes('items-end gap-4 w-full flex-wrap') as role_preview_timing_row:
                        role_preview_base = ui.number(
                            '基础停留秒数',
                            value=ConfigManager.get_client_var('llm_role_preview_base_seconds', 2),
                            min=2,
                            max=60,
                            step=1,
                        ).classes('w-48')
                        role_preview_max = ui.number(
                            '最长停留秒数',
                            value=ConfigManager.get_client_var('llm_role_preview_max_seconds', 10),
                            min=2,
                            max=120,
                            step=1,
                        ).classes('w-48')

                        def save_role_preview_timing() -> None:
                            base = int(role_preview_base.value or 2)
                            max_seconds = int(role_preview_max.value or base)
                            if max_seconds < base:
                                max_seconds = base
                            ConfigManager.set_client_var('llm_role_preview_base_seconds', base)
                            ConfigManager.set_client_var('llm_role_preview_seconds_per_20_chars', 1)
                            ConfigManager.set_client_var('llm_role_preview_max_seconds', max_seconds)
                            ui.notify('AI 角色确认浮层停留时间已保存，下一次角色调用生效。', type='positive')

                        ui.button('保存停留时间', icon='schedule', on_click=save_role_preview_timing).props('outline color=amber-8').classes('h-10 px-4 rounded-lg text-sm bg-white')

                sync_section_preview_visibility()

            with ui.card().classes('w-full p-6 rounded-xl bg-slate-50 border border-slate-200 shadow-none gap-5'):
                with ui.row().classes('items-center justify-between w-full gap-4 flex-wrap'):
                    with ui.column().classes('gap-1'):
                        ui.label('AI 角色').classes('text-lg font-bold text-slate-900')
                        ui.label('翻译、小助理、大助理和自定义角色都通过触发词调用；每个角色可以绑定不同 API 配置和模型。').classes('text-sm text-slate-500')
                    ui.button('添加角色', icon='add', on_click=lambda: open_role_editor(None)).classes('bg-amber-600 text-white px-4')

                with ui.row().classes('items-end gap-3 w-full'):
                    search_in = ui.input('搜索角色、触发词、文件或 API 配置', value=search_state['text']).classes('flex-1')

                    def run_search() -> None:
                        search_state['text'] = search_in.value or ''
                        main_content.refresh()

                    search_in.on('keydown.enter', lambda _: run_search())
                    ui.button(icon='search', on_click=run_search).props('outline color=amber-8 round').classes('bg-white')

                if not filtered_roles:
                    ui.label('没有匹配的角色。').classes('text-sm text-slate-500')

                for role in filtered_roles:
                    bound_profile = ConfigManager.get_llm_profile(role.get('profile_id')) if role.get('profile_id') else None
                    with ui.card().classes('w-full p-4 rounded-lg bg-white border border-slate-200 shadow-none'):
                        with ui.row().classes('items-center justify-between gap-4 w-full flex-wrap'):
                            with ui.column().classes('gap-1 min-w-0'):
                                with ui.row().classes('items-center gap-2 flex-wrap'):
                                    ui.label(role.get('display_name') or role['stem']).classes('font-semibold text-slate-900')
                                    ui.badge('已启用' if role.get('enabled') else '已关闭', color='green-2' if role.get('enabled') else 'grey-3').classes('text-slate-700')
                                    if not bound_profile:
                                        ui.badge('未绑定 API', color='amber-2').classes('text-amber-900 border border-amber-200')
                                ui.label(f'触发词：{role.get("name") or role["stem"]}').classes('text-xs text-slate-500')
                                ui.label(_profile_summary(bound_profile)).classes('text-xs text-slate-500')
                                output_mode = _normalized_role_output_mode(role.get('output_mode'))
                                output_text = '浮层：跟随总设置' if output_mode == 'inherit' else f'浮层：{ROLE_OUTPUT_OPTIONS.get(output_mode, "跟随总设置")}'
                                ui.label(output_text).classes('text-xs text-slate-400')
                            with ui.row().classes('items-center gap-2'):

                                def toggle_role(e, r=role):
                                    if e.value and not has_profiles():
                                        ConfigManager.set_llm_role_config(r['stem'], enabled=False)
                                        ui.notify('还没有 API 配置，无法启用 AI 角色。请点击右上角“管理 API 配置”添加。', type='warning')
                                        main_content.refresh()
                                        return
                                    updates = {'enabled': bool(e.value)}
                                    if e.value and not ConfigManager.get_llm_profile(r.get('profile_id')):
                                        current_data = get_profiles()
                                        current_active = _active_profile(current_data)
                                        if current_active:
                                            updates['profile_id'] = current_active['id']
                                            updates['model'] = r.get('model') or current_active.get('default_model') or ((current_active.get('models') or [''])[0])
                                    ConfigManager.set_llm_role_config(r['stem'], **updates)
                                    ui.notify('角色开关已保存，下一次角色调用生效。', type='positive')
                                    main_content.refresh()

                                ui.switch(value=role.get('enabled'), on_change=toggle_role).props('color=amber-8')
                                ui.button('编辑', icon='tune', on_click=lambda r=role: open_role_editor(r)).props('outline color=amber-8').classes('bg-white')

    main_content()
