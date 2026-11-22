"""Apply a user-confirmed managed selection without hard-coding portable paths."""
from __future__ import annotations
import os
from .paths import data_root, model_root
from .storage import read_json


def apply_selected_model(namespace: dict) -> None:
    target = namespace['ServerConfig']
    selected = None
    try:
        # A legacy switch can be interrupted before a selection file exists.
        # Always inspect the journal before considering an absent choice safe.
        from .data_safety import pending_imports
        if pending_imports(data_root() / 'product' / 'model-switch-backups'):
            raise ValueError('发现中断的模型切换，请在服务与诊断恢复后重试。')
        if pending_imports(data_root() / 'web_gui' / 'config_backups'):
            raise ValueError('发现中断的配置导入，请在服务与诊断恢复后重试。')
        choice = read_json(data_root() / 'product' / 'model-selection.json', {})
        if not isinstance(choice, dict):
            raise ValueError('模型选择记录格式无效；原文件已保留。')
        candidate_supplied = 'CAPSWRITER_MODEL_ID' in os.environ
        if candidate_supplied:
            selected = os.environ['CAPSWRITER_MODEL_ID']
        elif 'selected_model' in choice:
            selected = choice['selected_model']
        else:
            return  # No pending transaction and no managed choice: legacy config.
        if not isinstance(selected, str) or not selected.strip():
            raise ValueError('模型选择必须是非空模型 ID；原记录已保留，请在诊断页检查。')
        from .models import ModelCatalog
        catalog = ModelCatalog()
        config = catalog.configuration(selected, model_root())
        setup = read_json(data_root() / 'product' / 'setup.json', {})
        if not isinstance(setup, dict):
            raise ValueError('首次设置记录格式无效；原文件已保留。')
        first_candidate = (candidate_supplied and setup.get('schema') == 1
                           and setup.get('default_model') is None
                           and setup.get('first_candidate') == selected)
        # Validate every destination before applying any path changes.
        updates = []
        for class_name, values in config['classes'].items():
            configured_class = namespace[class_name]
            for key, value in values.items():
                if key.endswith(('_path', '_dir')) or key in ('paraformer', 'tokens'):
                    updates.append((configured_class, key, value))
        if first_candidate and config['model_type'] == 'sensevoice':
            updates.append((namespace['SenseVoiceArgs'], 'onnx_provider', 'CPU'))
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as error:
        if isinstance(selected, str) and selected.strip():
            target._product_model_id = selected
        target._product_model_error = str(error)
        return  # The GUI remains available; the Worker explicitly rejects this.
    target.model_type = config['model_type']
    target._product_model_id = selected
    target._product_model_error = ''
    for configured_class, key, value in updates:
        # Existing GPU/language choices remain intact after initial setup.
        setattr(configured_class, key, value)
