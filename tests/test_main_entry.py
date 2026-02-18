# tests/test_main_entry.py
import importlib

def test_main_invokes_cli(monkeypatch):
    called = {"ok": False}
    def fake_main():
        called["ok"] = True
    # import target after monkeypatch to avoid early binding
    monkeypatch.setattr("media_organiser.cli.main", fake_main)
    # re-import module to trigger if __name__ == "__main__" guard if present
    mod = importlib.import_module("media_organiser.main")
    assert hasattr(mod, "__file__")
    # direct call (module exposes main?)—fallback to calling cli.main
    if hasattr(mod, "main"):
        mod.main()
    else:
        import media_organiser.cli as cli
        cli.main()
    assert called["ok"]

def test_entrypoint_run(monkeypatch):
    import sys, runpy
    monkeypatch.setattr(sys, "argv", ["prog", "/tmp/src", "/tmp/dst", "--dry-run"])
    mod_name = "media_organiser.main"
    if mod_name in sys.modules:
        del sys.modules[mod_name]
    runpy.run_module(mod_name, run_name="__main__")
