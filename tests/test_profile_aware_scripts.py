import os
import sqlite3
import stat
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
REAL_INSTALL = REPO_ROOT / "scripts" / "install.sh"
REAL_DOCTOR = REPO_ROOT / "scripts" / "sessionvault-doctor.sh"


def _copy_script(src: Path, dst: Path) -> None:
    dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    dst.chmod(dst.stat().st_mode | stat.S_IXUSR)


def make_fake_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "plugin").mkdir(parents=True)
    (repo / "plugin" / "plugin.yaml").write_text("name: sessionvault\n", encoding="utf-8")
    (repo / "plugin" / "provider.py").write_text("print('ok')\n", encoding="utf-8")
    scripts = repo / "scripts"
    scripts.mkdir()
    _copy_script(REAL_INSTALL, scripts / "install.sh")
    _copy_script(REAL_DOCTOR, scripts / "sessionvault-doctor.sh")
    patch_helper = scripts / "sessionvault-gateway-patch.sh"
    patch_helper.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "action=\"\"\n"
        "hermes_home=\"\"\n"
        "while [[ $# -gt 0 ]]; do\n"
        "  case \"$1\" in\n"
        "    --check) action=check; shift ;;\n"
        "    --apply) action=apply; shift ;;\n"
        "    --hermes-home) hermes_home=\"$2\"; shift 2 ;;\n"
        "    *) shift ;;\n"
        "  esac\n"
        "done\n"
        "if [[ \"${PATCH_HELPER_MODE:-applied}\" == \"drift\" ]]; then\n"
        "  echo 'runtime drift detected'\n"
        "  exit 2\n"
        "fi\n"
        "if [[ \"$action\" == \"check\" ]]; then\n"
        "  if [[ \"${PATCH_HELPER_MODE:-applied}\" == \"missing\" ]]; then\n"
        "    echo 'patch not applied'\n"
        "    exit 1\n"
        "  fi\n"
        "  echo 'patch already applied'\n"
        "  exit 0\n"
        "fi\n"
        "echo 'patch applied'\n"
        "exit 0\n",
        encoding="utf-8",
    )
    patch_helper.chmod(patch_helper.stat().st_mode | stat.S_IXUSR)
    return repo


def make_hermes_home(tmp_path: Path) -> Path:
    hermes_home = tmp_path / ".hermes"
    (hermes_home / "hermes-agent" / "plugins" / "memory").mkdir(parents=True, exist_ok=True)
    (hermes_home / "hermes-agent" / "gateway").mkdir(parents=True, exist_ok=True)
    (hermes_home / "hermes-agent" / "gateway" / "run.py").write_text("print('gateway')\n", encoding="utf-8")
    (hermes_home / "config.yaml").write_text("memory:\n  provider: builtin\n", encoding="utf-8")
    return hermes_home


def make_profile(hermes_home: Path, name: str, provider: str = "sessionvault") -> Path:
    profile_home = hermes_home / "profiles" / name
    profile_home.mkdir(parents=True, exist_ok=True)
    (profile_home / "config.yaml").write_text(f"memory:\n  provider: {provider}\n", encoding="utf-8")
    return profile_home


def make_db(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE sessions (id INTEGER PRIMARY KEY)")
    conn.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY)")
    conn.execute("CREATE TABLE summaries (id INTEGER PRIMARY KEY)")
    conn.execute("INSERT INTO sessions DEFAULT VALUES")
    conn.execute("INSERT INTO messages DEFAULT VALUES")
    conn.execute("INSERT INTO summaries DEFAULT VALUES")
    conn.commit()
    conn.close()


def run_script(script: Path, *args: str, hermes_home: Path, patch_mode: str | None = None) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["HERMES_HOME"] = str(hermes_home)
    if patch_mode is not None:
        env["PATCH_HELPER_MODE"] = patch_mode
    return subprocess.run([str(script), *args], text=True, capture_output=True, check=False, env=env)


def test_install_profile_errors_when_profile_missing(tmp_path):
    repo = make_fake_repo(tmp_path)
    hermes_home = make_hermes_home(tmp_path)

    result = run_script(repo / "scripts" / "install.sh", "--profile", "kimi", hermes_home=hermes_home)

    assert result.returncode != 0
    assert "Profile not found" in (result.stdout + result.stderr)


def test_install_profile_targets_profile_data_and_skips_reinstall_when_aligned(tmp_path):
    repo = make_fake_repo(tmp_path)
    hermes_home = make_hermes_home(tmp_path)
    profile_home = make_profile(hermes_home, "kimi")

    first = run_script(repo / "scripts" / "install.sh", "--profile", "kimi", hermes_home=hermes_home)
    assert first.returncode == 0
    runtime_plugin = hermes_home / "hermes-agent" / "plugins" / "memory" / "sessionvault"
    assert (runtime_plugin / "plugin.yaml").exists()
    assert (profile_home / "sessionvault").exists()
    assert "Target profile: kimi" in first.stdout
    assert str(profile_home / "sessionvault" / "vault.db") in first.stdout

    second = run_script(repo / "scripts" / "install.sh", "--profile", "kimi", hermes_home=hermes_home)
    assert second.returncode == 0
    assert "Runtime plugin already aligned; skipping reinstall" in second.stdout


def test_doctor_profile_reads_profile_config_and_db(tmp_path):
    repo = make_fake_repo(tmp_path)
    hermes_home = make_hermes_home(tmp_path)
    profile_home = make_profile(hermes_home, "kimi", provider="sessionvault")
    runtime_plugin = hermes_home / "hermes-agent" / "plugins" / "memory" / "sessionvault"
    runtime_plugin.mkdir(parents=True, exist_ok=True)
    (runtime_plugin / "plugin.yaml").write_text("name: sessionvault\n", encoding="utf-8")
    (runtime_plugin / "provider.py").write_text("print('ok')\n", encoding="utf-8")
    make_db(profile_home / "sessionvault" / "vault.db")

    result = run_script(repo / "scripts" / "sessionvault-doctor.sh", "--profile", "kimi", hermes_home=hermes_home)

    assert result.returncode == 0
    assert "target profile: kimi" in result.stdout.lower()
    assert f"Config: {profile_home / 'config.yaml'}" in result.stdout
    assert f"DB: {profile_home / 'sessionvault' / 'vault.db'}" in result.stdout
    assert "config memory.provider: 'sessionvault'" in result.stdout
