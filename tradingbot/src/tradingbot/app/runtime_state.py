"""Separate position/override state by database and paper/live mode.

Legacy shared state requires an explicit, reconciled migration for live use.
Never automatically adopt a file that might contain paper positions.
"""
from pathlib import Path
from sqlalchemy.engine import make_url


def runtime_state_dir(db_url: str, dry_run: bool) -> Path:
    url = make_url(db_url)
    if url.get_backend_name() != 'sqlite' or not url.database or url.database == ':memory:':
        raise ValueError('Persistent file-backed SQLite is required for runner state')
    database = Path(url.database).resolve()
    mode = 'paper' if dry_run else 'live'
    state = database.parent / 'state' / f'{database.name}-{mode}'
    if not dry_run:
        legacy = [(Path('data/positions.json'), state / 'positions.json'),
                  (Path('runtime_overrides.json'), state / 'runtime_overrides.json')]
        for old, new in legacy:
            if old.exists() and not new.exists():
                raise RuntimeError(
                    f'Legacy shared state {old} needs reconciliation before migration to {new}; '
                    'live startup blocked')
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    return state
