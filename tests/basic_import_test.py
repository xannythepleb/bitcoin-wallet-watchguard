from wallet_watchguard import cli

def test_cli_module_imports():
    assert hasattr(cli, "main")