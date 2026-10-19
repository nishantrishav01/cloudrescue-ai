from importlib.metadata import distribution
from pathlib import Path

from packaging.markers import default_environment
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name


def main():
    root = Path(__file__).resolve().parent.parent
    environment = {
        **default_environment(),
        "sys_platform": "linux",
        "platform_system": "Linux",
        "os_name": "posix",
        "platform_machine": "x86_64",
    }
    pending = [
        Requirement(line)
        for line in (root / "backend/requirements.txt").read_text().splitlines()
        if line.strip()
    ]
    seen = set()
    versions = {}
    while pending:
        requirement = pending.pop()
        name = canonicalize_name(requirement.name)
        key = (name, tuple(sorted(requirement.extras)))
        if key in seen:
            continue
        seen.add(key)
        package = distribution(name)
        versions[name] = package.version
        for dependency in package.requires or []:
            child = Requirement(dependency)
            if child.marker is None or any(
                child.marker.evaluate({**environment, "extra": extra})
                for extra in (requirement.extras or {""})
            ):
                pending.append(child)
    (root / "backend/requirements-lambda.lock").write_text(
        "\n".join(f"{name}=={version}" for name, version in sorted(versions.items())) + "\n"
    )


if __name__ == "__main__":
    main()
