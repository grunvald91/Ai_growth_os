#!/usr/bin/env python3
"""Create deployment archive without local secrets or scratch files."""
import fnmatch
import os
import tarfile

ARCHIVE_NAME = "deploy_update.tar.gz"
BASE_DIR = r"C:\Users\User\booking_bot"

# Directories and files to include
INCLUDE = [
    "bot/",
    "database/",
    "migrations/",
    "webapp/",
    "requirements.txt"
]

# Files to exclude
EXCLUDE = [
    ".env",
    ".env.*",
    "__pycache__",
    "*.pyc",
    "*.backup",
    "tmpclaude-*",
    "*.tar.gz",
    "*.b64",
    "venv"
]

def should_exclude(name):
    """Check if file or directory should be excluded."""
    normalized = name.replace("\\", "/")
    basename = os.path.basename(normalized)
    for pattern in EXCLUDE:
        if fnmatch.fnmatch(normalized, pattern) or fnmatch.fnmatch(basename, pattern):
            return True
    return False

def create_archive():
    print(f"Creating deployment archive: {ARCHIVE_NAME}")

    with tarfile.open(ARCHIVE_NAME, "w:gz") as tar:
        for item in INCLUDE:
            full_path = os.path.join(BASE_DIR, item)
            if os.path.exists(full_path):
                if os.path.isdir(full_path):
                    # Add directory recursively
                    for root, dirs, files in os.walk(full_path):
                        # Filter out excluded directories
                        dirs[:] = [d for d in dirs if not should_exclude(d)]

                        for file in files:
                            if not should_exclude(file):
                                file_path = os.path.join(root, file)
                                arcname = os.path.relpath(file_path, BASE_DIR)
                                tar.add(file_path, arcname=arcname)
                                print(f"  + {arcname}")
                else:
                    # Add single file
                    if not should_exclude(item):
                        arcname = os.path.relpath(full_path, BASE_DIR)
                        tar.add(full_path, arcname=arcname)
                        print(f"  + {arcname}")
            else:
                print(f"  ! {item} not found, skipping")

    print(f"\nArchive created: {ARCHIVE_NAME}")
    print(f"  Size: {os.path.getsize(ARCHIVE_NAME) / 1024:.1f} KB")

if __name__ == "__main__":
    os.chdir(BASE_DIR)
    create_archive()
