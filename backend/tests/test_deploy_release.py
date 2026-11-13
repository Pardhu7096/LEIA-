"""Deployment failure detection for deploy/ec2/deploy-release.sh.

The real script runs in a temporary directory tree with stand-ins for python3,
pip, sudo/systemctl and curl. Nothing here contacts a server, so a pass proves
the script's control flow only; it is not evidence of a working EC2 deployment.
"""

import os
import shutil
import stat
import subprocess
import tarfile
from pathlib import Path

import pytest

REPOSITORY = Path(__file__).resolve().parents[2]
SCRIPT = REPOSITORY / "deploy" / "ec2" / "deploy-release.sh"
WORKFLOW = REPOSITORY / ".github" / "workflows" / "leia-ec2-delivery.yml"
SHA_1, SHA_2 = "1" * 40, "2" * 40

pytestmark = pytest.mark.skipif(
    os.name == "nt" or shutil.which("bash") is None or not SCRIPT.exists(),
    reason="Needs bash and the repository's deploy script.",
)

STUBS = {
    # `python3 -m venv DIR` creates a virtual environment with recording stand-ins.
    "python3": """#!/usr/bin/env bash
[[ "$1" == -m && "$2" == venv ]] || exit 9
mkdir -p "$3/bin"
printf '#!/usr/bin/env bash\\nexit 0\\n' > "$3/bin/pip"
cat > "$3/bin/python" <<'EOF'
#!/usr/bin/env bash
echo "preflight $*" >> "$STUB_STATE/calls"
exit "$(cat "$STUB_STATE/preflight_exit" 2>/dev/null || echo 0)"
EOF
chmod +x "$3/bin/pip" "$3/bin/python"
""",
    "sudo": """#!/usr/bin/env bash
echo "sudo $*" >> "$STUB_STATE/calls"
exit "$(cat "$STUB_STATE/restart_exit" 2>/dev/null || echo 0)"
""",
    # Healthy only when the live release is listed in $STUB_STATE/healthy.
    "curl": """#!/usr/bin/env bash
url="${!#}"
live="$(basename "$(readlink -f "$LEIA_DEPLOY_BASE/$STUB_ENVIRONMENT/current")")"
echo "curl ${url#http://127.0.0.1:} live=$live" >> "$STUB_STATE/calls"
grep -qx "$live" "$STUB_STATE/healthy" 2>/dev/null
""",
}


class Server:
    def __init__(self, tmp_path, environment="staging", folder=None):
        self.environment = environment
        self.base = tmp_path / "base"
        self.root = self.base / (folder or environment)
        self.incoming = tmp_path / "incoming"
        self.state = tmp_path / "state"
        self.bin = tmp_path / "bin"
        for directory in (self.root / "shared", self.incoming, self.state, self.bin):
            directory.mkdir(parents=True, exist_ok=True)
        (self.root / "shared" / ".env").write_text("PLACEHOLDER=1\n")
        shutil.copy(SCRIPT, self.root / "deploy-release.sh")
        for name, body in STUBS.items():
            stub = self.bin / name
            stub.write_text(body)
            stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
        self.source = tmp_path / "source"
        self.source.mkdir(exist_ok=True)
        (self.source / "requirements-runtime.txt").write_text("")
        (self.source / "preflight.py").write_text("")

    def upload(self, sha):
        archive = self.incoming / f"leia-{self.environment}-{sha}.tgz"
        with tarfile.open(archive, "w:gz") as package:
            for item in self.source.iterdir():
                package.add(item, arcname=item.name)
        return archive

    def healthy(self, *shas):
        (self.state / "healthy").write_text("".join(f"{sha}\n" for sha in shas))

    def deploy(self, sha, port="8001", upload=True):
        if upload:
            self.upload(sha)
        return subprocess.run(
            ["bash", str(self.root / "deploy-release.sh"), sha, port],
            capture_output=True, text=True, timeout=60,
            env={
                "PATH": f"{self.bin}:/usr/bin:/bin",
                "LEIA_DEPLOY_BASE": str(self.base),
                "LEIA_DEPLOY_TMP": str(self.incoming),
                "LEIA_HEALTH_INTERVAL": "0",
                "STUB_STATE": str(self.state),
                "STUB_ENVIRONMENT": self.root.name,
            },
        )

    @property
    def live(self):
        current = self.root / "current"
        return current.resolve().name if current.is_symlink() else None

    @property
    def calls(self):
        log = self.state / "calls"
        return log.read_text().splitlines() if log.exists() else []


@pytest.fixture
def server(tmp_path):
    return Server(tmp_path)


def test_healthy_release_goes_live_after_preflight_and_readiness(server):
    server.healthy(SHA_1)
    result = server.deploy(SHA_1)
    assert result.returncode == 0, result.stderr
    assert server.live == SHA_1
    assert server.calls == [
        "preflight preflight.py --deployed",
        "sudo systemctl restart leia-staging.service",
        f"curl 8001/health/ready live={SHA_1}",
    ]
    release = server.root / "releases" / SHA_1
    assert (release / ".env").resolve() == server.root / "shared" / ".env"
    assert not (server.incoming / f"leia-staging-{SHA_1}.tgz").exists()


def test_unhealthy_release_fails_and_restores_the_previous_one(server):
    server.healthy(SHA_1)
    assert server.deploy(SHA_1).returncode == 0
    result = server.deploy(SHA_2)
    assert result.returncode == 1
    assert server.live == SHA_1
    assert "Health check failed; rolling back" in result.stderr
    assert f"Rolled back to {SHA_1}" in result.stderr
    assert "healthy in staging" not in result.stdout
    assert server.calls.count("sudo systemctl restart leia-staging.service") == 3


def test_failed_first_deployment_is_reported_as_down(server):
    result = server.deploy(SHA_1)
    assert result.returncode == 1
    assert "No earlier release to roll back to. staging is DOWN" in result.stderr


def test_failed_rollback_is_reported_as_down(server):
    server.healthy(SHA_1)
    assert server.deploy(SHA_1).returncode == 0
    server.healthy()
    result = server.deploy(SHA_2)
    assert result.returncode == 1
    assert f"ROLLBACK FAILED: {SHA_1} is not responding. staging is DOWN" in result.stderr


def test_restart_failure_fails_the_deployment(server):
    server.healthy(SHA_1, SHA_2)
    assert server.deploy(SHA_1).returncode == 0
    (server.state / "restart_exit").write_text("1")
    result = server.deploy(SHA_2)
    assert result.returncode == 1
    assert server.live == SHA_1
    assert "Service restart failed; rolling back" in result.stderr


def test_preflight_failure_leaves_the_running_release_untouched(server):
    server.healthy(SHA_1, SHA_2)
    assert server.deploy(SHA_1).returncode == 0
    restarts = server.calls.count("sudo systemctl restart leia-staging.service")
    (server.state / "preflight_exit").write_text("1")
    result = server.deploy(SHA_2)
    assert result.returncode == 1
    assert server.live == SHA_1
    assert "Preflight failed; the live staging release was not changed" in result.stderr
    assert server.calls.count("sudo systemctl restart leia-staging.service") == restarts


def test_same_commit_can_be_deployed_again_after_a_fix(server):
    assert server.deploy(SHA_1).returncode == 1  # Unhealthy first attempt.
    server.healthy(SHA_1)
    result = server.deploy(SHA_1)
    assert result.returncode == 0, result.stderr
    assert server.live == SHA_1


@pytest.mark.parametrize("sha,port", [("main", "8001"), (SHA_1[:39], "8001"),
                                      (SHA_1 + "; id", "8001"), (SHA_1, "80a"), (SHA_1, "")])
def test_malformed_arguments_are_refused_before_any_work(server, sha, port):
    result = server.deploy(sha, port, upload=False)
    assert result.returncode != 0
    assert server.calls == [] and not (server.root / "releases").exists()


def test_missing_archive_or_settings_stop_the_deployment(server):
    assert server.deploy(SHA_1, upload=False).returncode != 0
    (server.root / "shared" / ".env").unlink()
    assert server.deploy(SHA_1).returncode != 0
    assert server.live is None and server.calls == []


def test_production_copy_only_touches_production(tmp_path):
    staging = Server(tmp_path, "staging")
    production = Server(tmp_path, "production")
    production.healthy(SHA_1)
    result = production.deploy(SHA_1)
    assert result.returncode == 0, result.stderr
    assert production.calls[1] == "sudo systemctl restart leia-production.service"
    assert production.live == SHA_1
    assert staging.live is None and not (staging.root / "releases").exists()


def test_script_refuses_to_run_outside_a_known_environment_folder(tmp_path):
    server = Server(tmp_path, "staging", folder="preview")
    result = server.deploy(SHA_1)
    assert result.returncode == 2
    assert server.calls == []


def test_delivery_workflow_keeps_its_production_safeguards():
    workflow = WORKFLOW.read_text(encoding="utf-8")
    triggers = workflow.split("permissions:")[0]
    assert "workflow_dispatch:" in triggers
    assert "push:" not in triggers and "schedule:" not in triggers
    assert "permissions:\n  contents: read" in workflow
    assert "cancel-in-progress: false" in workflow
    assert "group: leia-deploy-${{ inputs.target }}" in workflow
    assert "needs: verify" in workflow
    assert "environment: ${{ inputs.target }}" in workflow
    assert 'test "$GITHUB_REF" = "refs/heads/main"' in workflow
    assert 'test "$CONFIRMATION" = "DEPLOY-LEIA-PRODUCTION"' in workflow
    assert "StrictHostKeyChecking=yes" in workflow
    assert "ssh-keyscan" not in workflow
    assert "StrictHostKeyChecking=no" not in workflow
    assert "python -m pytest backend/tests -q" in workflow
