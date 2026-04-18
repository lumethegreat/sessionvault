import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "scripts" / "sessionvault-gateway-patch.sh"


ORIGINAL_RUN = """class GatewayRunner:\n    def run(self):\n        return \"ok\"\n"""

PATCH_TEXT = """diff --git a/gateway/run.py b/gateway/run.py
--- a/gateway/run.py
+++ b/gateway/run.py
@@ -1,3 +1,4 @@
 class GatewayRunner:
     def run(self):
+        self._record_event()
         return \"ok\"
"""


def run_script(*args, env=None):
    return subprocess.run(
        [str(SCRIPT_PATH), *args],
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )


def make_runtime(tmp_path: Path) -> tuple[Path, Path, Path]:
    hermes_home = tmp_path / ".hermes"
    runtime_root = hermes_home / "hermes-agent"
    target = runtime_root / "gateway" / "run.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(ORIGINAL_RUN, encoding="utf-8")
    patch_path = tmp_path / "gateway.patch"
    patch_path.write_text(PATCH_TEXT, encoding="utf-8")
    return hermes_home, runtime_root, patch_path


def test_check_reports_patch_not_applied_before_apply(tmp_path):
    hermes_home, _, patch_path = make_runtime(tmp_path)

    result = run_script("--check", "--hermes-home", str(hermes_home), "--patch-file", str(patch_path))

    assert result.returncode == 1
    assert "Patch not applied" in result.stdout


def test_apply_then_check_is_idempotent(tmp_path):
    hermes_home, runtime_root, patch_path = make_runtime(tmp_path)

    apply_result = run_script("--apply", "--hermes-home", str(hermes_home), "--patch-file", str(patch_path))
    assert apply_result.returncode == 0
    assert "Patch applied" in apply_result.stdout
    patched_text = (runtime_root / "gateway" / "run.py").read_text(encoding="utf-8")
    assert "self._record_event()" in patched_text

    check_result = run_script("--check", "--hermes-home", str(hermes_home), "--patch-file", str(patch_path))
    assert check_result.returncode == 0
    assert "Patch already applied" in check_result.stdout

    second_apply = run_script("--apply", "--hermes-home", str(hermes_home), "--patch-file", str(patch_path))
    assert second_apply.returncode == 0
    assert "Patch already applied" in second_apply.stdout


def test_check_detects_runtime_drift(tmp_path):
    hermes_home, runtime_root, patch_path = make_runtime(tmp_path)
    target = runtime_root / "gateway" / "run.py"
    target.write_text("class GatewayRunner:\n    pass\n", encoding="utf-8")

    result = run_script("--check", "--hermes-home", str(hermes_home), "--patch-file", str(patch_path))

    assert result.returncode == 2
    assert "Runtime file has drifted" in result.stdout
