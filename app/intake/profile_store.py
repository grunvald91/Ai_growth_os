"""Seed the shared runtime volume without replacing owner-approved profiles."""
import fcntl
import os
import shutil
import tempfile
from pathlib import Path


def seed_products(source: Path, destination: Path):
    source, destination = Path(source).resolve(), Path(destination)
    if destination.is_symlink():
        raise ValueError("Profile store cannot be a symlink")
    destination.mkdir(parents=True, exist_ok=True)
    destination = destination.resolve()
    if source == destination or source in destination.parents or destination in source.parents:
        raise ValueError("Seed and runtime stores must be separate")
    descriptor = os.open(destination / ".seed.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        for item in sorted(source.rglob("*")):
            if item.is_symlink():
                raise ValueError("Packaged products cannot contain symlinks")
            if not item.is_file() or item.name.endswith(".lock"):
                continue
            target = destination / item.relative_to(source)
            if target.is_symlink() or any(parent.is_symlink() for parent in target.parents):
                raise ValueError("Runtime product paths cannot contain symlinks")
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.name == "company-profile.json" and target.exists():
                continue
            fd, temporary = tempfile.mkstemp(dir=target.parent, prefix=".seed-")
            try:
                with os.fdopen(fd, "wb") as output, item.open("rb") as original:
                    shutil.copyfileobj(original, output)
                    output.flush()
                    os.fsync(output.fileno())
                if target.name == "company-profile.json":
                    # An owner may apply a previously absent profile during deploy.
                    # Exclusive link creation cannot overwrite that concurrent apply.
                    try:
                        os.link(temporary, target)
                    except FileExistsError:
                        pass
                else:
                    os.replace(temporary, target)
            finally:
                Path(temporary).unlink(missing_ok=True)
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


if __name__ == "__main__":
    seed_products(Path("/app/products"), Path("/app/company_products"))
