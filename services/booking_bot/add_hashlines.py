#!/usr/bin/env python3
"""
Hashline Generator - добавляет хеши к строкам кода
Использование: python add_hashlines.py <file>
"""

import sys
import hashlib
from pathlib import Path


def generate_hash(line_num: int, content: str) -> str:
    """Генерирует 3-символьный хеш для строки"""
    hash_input = f"{line_num}{content.rstrip()}".encode('utf-8')
    return hashlib.md5(hash_input).hexdigest()[:3]


def add_hashlines(file_path: str) -> str:
    """Добавляет hashline-префиксы к каждой строке"""
    path = Path(file_path)

    if not path.exists():
        raise FileNotFoundError(f"Файл не найден: {file_path}")

    with open(path, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    hashed_lines = []
    max_line_digits = len(str(len(lines)))

    for i, line in enumerate(lines, 1):
        content = line.rstrip('\n')
        hash_code = generate_hash(i, content)

        # Форматируем с выравниванием
        prefix = f"{i:>{max_line_digits}}:{hash_code}"
        hashed_lines.append(f"{prefix} {content}\n")

    return ''.join(hashed_lines)


def remove_hashlines(content: str) -> str:
    """Удаляет hashline-префиксы из содержимого"""
    import re
    lines = content.split('\n')
    clean_lines = []

    for line in lines:
        # Убираем префикс формата "N:XXX "
        cleaned = re.sub(r'^\s*\d+:[a-f0-9]{3}\s+', '', line)
        clean_lines.append(cleaned)

    return '\n'.join(clean_lines)


def main():
    if len(sys.argv) < 2:
        print("Использование: python add_hashlines.py <file>")
        print("Или: python add_hashlines.py <file> --remove (убрать хеши)")
        sys.exit(1)

    file_path = sys.argv[1]
    remove_mode = '--remove' in sys.argv

    if remove_mode:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        result = remove_hashlines(content)
        action = "Хеши удалены"
    else:
        result = add_hashlines(file_path)
        action = "Хеши добавлены"

    # Выводим в stdout (можно перенаправить в файл)
    print(result)
    print(f"\n# {action}: {file_path}", file=sys.stderr)


if __name__ == '__main__':
    main()
