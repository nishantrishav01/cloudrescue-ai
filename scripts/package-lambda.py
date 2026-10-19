from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


def main():
    root = Path(__file__).resolve().parent.parent
    dependencies = root / "backend/.package"
    with ZipFile(root / "backend/lambda.zip", "w", ZIP_DEFLATED) as archive:
        for file in dependencies.rglob("*"):
            relative = file.relative_to(dependencies)
            if (
                file.is_file()
                and relative.parts[0] != "backend"
                and "__pycache__" not in relative.parts
                and file.suffix != ".pyc"
            ):
                archive.write(file, relative.as_posix())
        for directory in ["api", "agent", "common", "simulation", "live", "recovery"]:
            for file in (root / "backend" / directory).rglob("*.py"):
                if "__pycache__" not in file.parts:
                    archive.write(file, file.relative_to(root).as_posix())
        archive.write(root / "backend/__init__.py", "backend/__init__.py")
    print("Built backend/lambda.zip with Linux dependencies and current application source.")


if __name__ == "__main__":
    main()
