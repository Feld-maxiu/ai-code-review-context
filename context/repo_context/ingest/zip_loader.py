from pathlib import Path
from zipfile import ZipFile


def extract_zip(zip_path: str | Path, output_dir: str | Path) -> Path:
    """安全解压 zip 到指定目录，拒绝路径穿越条目。"""
    archive_path = Path(zip_path).expanduser().resolve()
    target_dir = Path(output_dir).expanduser().resolve()

    if not archive_path.exists():
        raise FileNotFoundError(f"Zip file does not exist: {archive_path}")
    if not archive_path.is_file():
        raise FileNotFoundError(f"Zip path is not a file: {archive_path}")

    target_dir.mkdir(parents=True, exist_ok=True)

    with ZipFile(archive_path) as archive:
        for member in archive.infolist():
            destination = (target_dir / member.filename).resolve()
            # 核心边界：任何解压目标都必须仍位于 output_dir 内。
            if not destination.is_relative_to(target_dir):
                raise ValueError(f"Unsafe zip entry path: {member.filename}")

        archive.extractall(target_dir)

    return target_dir
