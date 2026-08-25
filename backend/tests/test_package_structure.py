from importlib import import_module
from pathlib import Path


def test_application_imports_from_domain_packages() -> None:
    app = import_module("app.main").app
    assert app is not None
    root = Path(__file__).parents[1] / "app"
    obsolete = {
        "analysis_execution.py",
        "analysis_history.py",
        "analysis_runs.py",
        "analysis_task_routes.py",
        "llm_client.py",
        "market_data.py",
        "provider.py",
        "trades.py",
        "followup.py",
        "personal_center.py",
        "admin_prompts.py",
        "auth.py",
    }
    assert not obsolete.intersection(path.name for path in root.iterdir())
