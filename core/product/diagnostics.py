"""Allowlisted diagnostics: never include free-form content or user paths."""
from __future__ import annotations

from collections import Counter
import platform
from .updates import current_version


def diagnostic_summary(snapshot: dict) -> dict:
    health = snapshot.get('health', {})
    setup = snapshot.get('setup', {})
    # Counts use a fixed vocabulary. Unknown/free-form status strings cannot
    # accidentally carry text, paths or credentials into an exported report.
    states = {'queued', 'downloading', 'verifying', 'installing', 'paused', 'pausing',
              'cancelling', 'cancelled', 'failed', 'interrupted', 'running', 'completed'}
    def counts(key):
        result = Counter()
        for row in snapshot.get(key, []):
            state = row.get('state') if key == 'tasks' else row.get('status')
            result[state if state in states else 'unknown'] += 1
        return dict(result)
    return {'schema': 1, 'product_version': current_version(),
            'system': platform.system(), 'machine': platform.machine(),
            'python': platform.python_version(),
            'observations': {key: bool(health.get(key)) for key in
                             ('server_alive', 'client_alive', 'model_loaded', 'can_dictate', 'file_inference_busy')},
            'setup_complete': setup.get('status') in {'complete', 'existing'},
            'has_default_model': bool(setup.get('default_model')),
            'has_pending_model_recovery': bool(snapshot.get('model_recovery')),
            'downloads': counts('downloads'), 'tasks': counts('tasks'),
            'shutting_down': bool(snapshot.get('shutting_down'))}
