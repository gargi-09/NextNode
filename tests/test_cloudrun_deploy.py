"""The Cloud Run deployment stays public-mode, cheap and secret-free (no gcloud or Docker needed)."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = (ROOT / "Dockerfile").read_text(encoding="utf-8")
DEPLOY = (ROOT / "deploy" / "cloudrun" / "deploy.ps1").read_text(encoding="utf-8")
KEEPALIVE = (ROOT / ".github" / "workflows" / "keepalive.yml").read_text(encoding="utf-8")


def lines(name: str) -> list[str]:
    return [l.strip() for l in (ROOT / name).read_text(encoding="utf-8").splitlines()
            if l.strip() and not l.startswith("#")]


def test_the_image_runs_public_mode_as_a_non_root_user_with_only_app_code():
    assert "PUBLIC_MODE=1" in DOCKERFILE and re.search(r"^USER (?!root)\w+", DOCKERFILE, re.M)
    copies = re.findall(r"^COPY (\S+)", DOCKERFILE, re.M)
    assert copies == ["requirements.txt", "app/", "ontology/"]
    cmd = re.search(r"^CMD .*$", DOCKERFILE, re.M).group()
    assert "--proxy-headers" not in cmd              # the app reads the client IP itself


def test_env_files_are_never_uploaded_or_copied_into_the_image():
    gcloud = lines(".gcloudignore")
    assert gcloud[0] == "*" and ".env" in gcloud and ".env.*" in gcloud
    assert not any(l.startswith("!") and ".env" in l for l in gcloud)
    docker = lines(".dockerignore")
    assert ".env" in docker and ".env.*" in docker


def test_the_service_scales_to_zero_caps_at_one_instance_and_reads_secrets_from_secret_manager():
    for flag in ("--min-instances 0", "--max-instances 1", "--set-secrets", "--service-account",
                 "PUBLIC_MODE=1", "--memory 512Mi"):
        assert flag in DEPLOY, flag
    assert ".env" not in DEPLOY.split("#>")[1]          # the script never reads .env
    assert re.search(r"Read-Host .*-AsSecureString", DEPLOY)


def test_the_keepalive_workflow_needs_no_secrets_and_only_reads_health():
    assert "permissions: {}" in KEEPALIVE and "secrets." not in KEEPALIVE
    assert "/health/graph" in KEEPALIVE and "vars.NEXTNODE_API_URL" in KEEPALIVE
