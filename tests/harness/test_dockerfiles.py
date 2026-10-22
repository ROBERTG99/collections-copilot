"""NFR-6: every image runs as a non-root user and reports its health; compose waits on it."""

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
DOCKERFILES = [ROOT / "docker" / "python.Dockerfile", ROOT / "web" / "Dockerfile"]
# One-shot jobs exit when done; a healthcheck would never report on them.
JOBS = {"migrate", "seed", "tests"}


def stages(dockerfile: Path) -> dict[str, list[str]]:
    """Instructions per named stage, with each stage inheriting its parent stage's lines."""
    out: dict[str, list[str]] = {}
    current: list[str] = []
    for line in dockerfile.read_text().splitlines():
        m = re.match(r"FROM\s+(\S+)(?:\s+AS\s+(\S+))?", line, re.IGNORECASE)
        if m:
            current = list(out.get(m.group(1), []))
            out[m.group(2) or f"_{len(out)}"] = current
        elif line.strip() and not line.lstrip().startswith("#"):
            current.append(line.strip())
    return out


def final_stages(dockerfile: Path) -> dict[str, list[str]]:
    """Stages nothing else builds FROM: the images compose and ECS actually run."""
    all_stages = stages(dockerfile)
    parents = set(
        re.findall(r"^FROM\s+(\S+)", dockerfile.read_text(), re.MULTILINE | re.IGNORECASE)
    )
    copied = set(re.findall(r"--from=(\S+)", dockerfile.read_text()))
    return {k: v for k, v in all_stages.items() if k not in parents | copied}


def last_user(lines: list[str]) -> str | None:
    users = [ln.split(None, 1)[1] for ln in lines if ln.upper().startswith("USER ")]
    return users[-1] if users else None


def test_non_root_and_healthcheck() -> None:
    problems = []
    for dockerfile in DOCKERFILES:
        assert dockerfile.is_file(), f"{dockerfile} is missing"
        targets = final_stages(dockerfile)
        assert targets, f"{dockerfile} has no final stage"
        for name, lines in targets.items():
            user = last_user(lines)
            if user is None or user.split(":")[0] in {"root", "0"}:
                problems.append(f"{dockerfile.name}:{name} runs as {user or 'root'}")
            if not any(ln.upper().startswith("HEALTHCHECK") for ln in lines):
                problems.append(f"{dockerfile.name}:{name} has no HEALTHCHECK")
    assert problems == []


def test_web_image_serves_the_standalone_build() -> None:
    text = (ROOT / "web" / "Dockerfile").read_text()
    assert ".next/standalone" in text
    assert "/healthz" in text
    assert (ROOT / "web" / "app" / "healthz" / "route.ts").is_file()


def test_every_long_running_service_has_a_healthcheck() -> None:
    compose = yaml.safe_load((ROOT / "compose.yaml").read_text())
    dockerfile_targets = {
        name
        for f in DOCKERFILES
        for name, lines in final_stages(f).items()
        if any(ln.upper().startswith("HEALTHCHECK") and "NONE" not in ln.upper() for ln in lines)
    }
    missing = []
    for name, svc in compose["services"].items():
        if name in JOBS:
            continue
        target = (svc.get("build") or {}).get("target")
        if "healthcheck" not in svc and target not in dockerfile_targets:
            missing.append(name)
    assert missing == []
    assert "web" in compose["services"]
    assert compose["services"]["web"]["ports"] == ["127.0.0.1:8300:8300"]


def test_ports_bind_to_loopback_in_the_local_range() -> None:
    compose = yaml.safe_load((ROOT / "compose.yaml").read_text())
    for name, svc in compose["services"].items():
        for port in svc.get("ports", []):
            host, published, _ = str(port).split(":")
            assert host == "127.0.0.1", name
            assert 8300 <= int(published) <= 8399, name


def test_test_compose_runs_the_suite_offline() -> None:
    compose = yaml.safe_load((ROOT / "compose.test.yaml").read_text())
    tests = compose["services"]["tests"]
    env = tests["environment"]
    assert env["CC_LLM_PROVIDER"] == "fake"
    assert "pytest" in " ".join(tests["command"])
    assert (ROOT / "scripts" / "time_up.sh").is_file()


def test_image_builds_use_the_default_network_unless_told_otherwise() -> None:
    """Compose v5 builds through bake, which refuses `network: host` without an entitlement
    compose cannot pass; only the proxied cloud container sets CC_BUILD_NETWORK=host."""
    raw = (ROOT / "compose.yaml").read_text() + (ROOT / "compose.test.yaml").read_text()
    networks = re.findall(r"^\s+network:\s*(.+)$", raw, re.MULTILINE)
    assert networks, "compose builds declare their network"
    assert all(n.strip().strip("\"'") == "${CC_BUILD_NETWORK:-default}" for n in networks)
    hook = (ROOT / ".claude" / "hooks" / "session_start.sh").read_text()
    assert "export CC_BUILD_NETWORK=host" in hook
