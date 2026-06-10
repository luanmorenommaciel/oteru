#!/usr/bin/env python3
"""Guarda de PII: escaneia as capturas commitadas em busca de identidade real.

O risco nº 1 documentado do repo é commitar uma captura OTLP não-redigida
(elas carregam e-mail, IDs de conta/organização e caminhos do usuário).
Este script é o único artefato de verificação — reusado pelo Makefile
(``make pii-guard``), pelo hook de pre-commit e pelo CI.

Somente stdlib; roda com o Python do sistema, antes de qualquer ``make setup``.

Regras (violação -> ``arquivo:linha`` + descrição, exit 1):
1. E-mails fora dos domínios reservados ``example.com/org/net``.
2. Caminhos de usuário: ``C:\\Users\\<nome>`` (incl. JSON-escaped),
   ``/home/<nome>``, ``/Users/<nome>``.
3. Key-aware: ``user.id``, ``user.account_uuid``, ``user.account_id`` e
   ``organization.id`` precisam ter valor-placeholder (hex todo-zero, UUID de
   dígito repetido, ``user_REDACTED...``). ``session.id``/``request_id`` reais
   são permitidos: são correlação, não identidade.
4. ``host.name`` com valor não-placeholder, se presente.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Diretórios com capturas/fixtures commitadas — os únicos lugares onde uma
# captura pode legitimamente viver no repo.
SCAN_DIRS = (
    REPO_ROOT / "oteru-emitter" / "samples",
    REPO_ROOT / "oteru-emitter" / "tests" / "fixtures",
)

ALLOWED_EMAIL_DOMAINS = ("example.com", "example.org", "example.net")

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@([A-Za-z0-9.-]+\.[A-Za-z]{2,})")

# C:\Users\<nome> cru ou JSON-escaped (C:\\Users\\<nome>), e /home|/Users POSIX.
USER_PATH_RES = (
    re.compile(r"[A-Za-z]:[\\/]{1,4}Users[\\/]{1,4}[A-Za-z0-9._-]+"),
    re.compile(r"/home/[A-Za-z0-9._-]+"),
    re.compile(r"/Users/[A-Za-z0-9._-]+"),
)

# Chaves de identidade do principal: o valor PRECISA ser placeholder.
IDENTITY_KEYS = ("user.id", "user.account_uuid", "user.account_id", "organization.id")

ATTR_RE = re.compile(
    r'"key"\s*:\s*"(?P<key>[^"]+)"\s*,\s*"value"\s*:\s*\{\s*"stringValue"\s*:\s*"(?P<value>[^"]*)"'
)

PLACEHOLDER_RES = (
    re.compile(r"^0+$"),  # hex/dígitos todo-zero
    re.compile(r"^(\d)\1{7}-\1{4}-\1{4}-\1{4}-\1{12}$"),  # UUID de dígito repetido
    re.compile(r"^user_REDACTED"),
    re.compile(r"REDACTED"),
)


def _is_placeholder(value: str) -> bool:
    return any(rx.search(value) for rx in PLACEHOLDER_RES)


def _check_line(line: str) -> list[str]:
    """Retorna as descrições de violação encontradas na linha."""
    violations: list[str] = []

    for match in EMAIL_RE.finditer(line):
        domain = match.group(1).lower()
        if not any(domain == d or domain.endswith("." + d) for d in ALLOWED_EMAIL_DOMAINS):
            violations.append(f"e-mail fora de example.com/org/net: {match.group(0)!r}")

    for rx in USER_PATH_RES:
        for match in rx.finditer(line):
            violations.append(f"caminho de usuário: {match.group(0)!r}")

    for match in ATTR_RE.finditer(line):
        key, value = match.group("key"), match.group("value")
        if key in IDENTITY_KEYS and not _is_placeholder(value):
            violations.append(f"identidade não-redigida em {key!r}: {value!r}")
        elif key == "host.name" and not _is_placeholder(value):
            violations.append(f"host.name não-placeholder: {value!r}")

    return violations


def main() -> int:
    files: list[Path] = []
    for scan_dir in SCAN_DIRS:
        if scan_dir.is_dir():
            files.extend(sorted(p for p in scan_dir.rglob("*") if p.is_file()))

    if not files:
        print("pii-guard: nenhum arquivo para escanear (diretórios vazios?).", file=sys.stderr)
        return 1

    total = 0
    for path in files:
        rel = path.relative_to(REPO_ROOT)
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            print(f"{rel}: arquivo binário em diretório de capturas — verifique manualmente.")
            total += 1
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            for violation in _check_line(line):
                print(f"{rel}:{lineno}: {violation}")
                total += 1

    if total:
        print(f"\npii-guard: {total} violação(ões) — NÃO commite capturas não-redigidas.")
        return 1

    print(f"pii-guard: OK ({len(files)} arquivo(s) limpos).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
