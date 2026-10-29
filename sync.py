# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 norisjunior

import base64
import fnmatch
import hashlib
import os
import re
import time
from datetime import datetime, timedelta
from pathlib import Path

import graph
import state


def log(message):
    print(f"{datetime.now().astimezone().isoformat(timespec='seconds')} {message}", flush=True)


def _hash(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _sha1(path):
    digest = hashlib.sha1()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _quickxor(path):
    result = bytearray(20)
    size = 0
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            for byte in block:
                bit = (size * 11) % 160
                index, shift = divmod(bit, 8)
                result[index] ^= (byte << shift) & 255
                if shift:
                    result[(index + 1) % 20] ^= byte >> (8 - shift)
                size += 1
    for index, byte in enumerate(size.to_bytes(8, "little"), 12):
        result[index] ^= byte
    return base64.b64encode(result).decode("ascii")


def _same_remote(path, size, item):
    if item.get("size") != size or "file" not in item:
        return False
    hashes = item["file"].get("hashes", {})
    if hashes.get("sha1Hash"):
        return hashes["sha1Hash"].lower() == _sha1(path)
    if hashes.get("quickXorHash"):
        return hashes["quickXorHash"] == _quickxor(path)
    return False


def _ignored(relative, patterns):
    return any(fnmatch.fnmatch(relative, pattern) or
               fnmatch.fnmatch(Path(relative).name, pattern) for pattern in patterns)


def _included(relative, patterns):
    return any(fnmatch.fnmatch(relative, pattern) for pattern in patterns)


def _needed_directory(relative, patterns):
    prefix = relative.rstrip("/") + "/"
    return any(pattern.startswith(prefix) or fnmatch.fnmatch(relative, pattern) for pattern in patterns)


def _walk_error(error):
    raise error


def _files(mapping, ignored):
    root = Path(mapping["local_dir"]).expanduser().resolve()
    if not root.is_dir():
        raise RuntimeError(f"Pasta local inexistente: {root}")
    if not mapping["recursive"]:
        for path in sorted(root.iterdir()):
            relative = path.name
            if path.is_file() and not path.is_symlink() and _included(relative, mapping["include"]):
                if not _ignored(relative, ignored):
                    yield path, relative
        return
    for directory, subdirs, files in os.walk(root, followlinks=False, onerror=_walk_error):
        subdirs[:] = sorted(name for name in subdirs if (
            _needed_directory((Path(directory) / name).relative_to(root).as_posix(),
                              mapping["include"])
            and not _ignored((Path(directory) / name).relative_to(root).as_posix(), ignored)
        ))
        for name in sorted(files):
            path = Path(directory) / name
            relative = path.relative_to(root).as_posix()
            if (not path.is_symlink() and _included(relative, mapping["include"])
                    and not _ignored(relative, ignored)):
                yield path, relative


def _mappings(config, selected):
    return [item for item in config["mapping"] if not selected or item["name"] in selected]


def _dump_date(name, mapping):
    for pattern in mapping.get("retention_patterns", []):
        expression = re.escape(pattern).replace(re.escape("{date}"), r"([0-9]{8})")
        match = re.fullmatch(expression, name)
        if match:
            try:
                return datetime.strptime(match[1], "%Y%m%d").date()
            except ValueError:
                return None
    return None


def _retention_cutoff(mapping, today):
    days = mapping.get("retention_days", 0)
    if days and mapping.get("retention_patterns") and not mapping["recursive"]:
        return today - timedelta(days=days - 1)
    return None


def _expired_dumps(mapping, remote, today):
    cutoff = _retention_cutoff(mapping, today)
    if cutoff is None:
        return []
    return sorted(name for name in remote
                  if _dump_date(name, mapping) and _dump_date(name, mapping) < cutoff)


def _prune_dumps(config, mapping, connection, today):
    remote = graph.remote_tree(mapping["remote_dir"], config["tenant_id"],
                               config["client_id"], recursive=False)
    expired = _expired_dumps(mapping, remote, today)
    if not expired:
        return
    root = Path(mapping["local_dir"]).expanduser().resolve()
    # Só liberar os antigos após confirmar todos os padrões em todos os dias.
    for age in range(mapping["retention_days"]):
        day = (today - timedelta(days=age)).strftime("%Y%m%d")
        for pattern in mapping["retention_patterns"]:
            name = pattern.replace("{date}", day)
            item = remote.get(name)
            saved = state.uploaded(connection, f"{mapping['remote_dir']}/{name}")
            if not item or not saved or saved[0] <= 0 or item.get("size") != saved[0]:
                log(f"Retenção {mapping['name']} adiada: backup não confirmado: {name}")
                return
            path = root / name
            if path.exists():
                current = path.stat()
                if path.is_symlink() or (current.st_size, current.st_mtime_ns) != saved[:2]:
                    log(f"Retenção {mapping['name']} adiada: arquivo local alterado: {name}")
                    return
    for name in expired:
        graph.delete_file(remote[name], config["tenant_id"], config["client_id"])
        state.forget_file(connection, f"{mapping['remote_dir']}/{name}", str(root / name))
        log(f"Retenção {mapping['name']}: enviado à lixeira: {name}")


def _compare(config, selected=None, record=False):
    total = {"equal": 0, "missing": 0, "conflict": 0, "changing": 0, "errors": 0}
    total_bytes = {key: 0 for key in total}
    connection = state.connect() if record else None
    label = "Reconciliação" if record else "Plano"
    today = datetime.now().date()
    try:
        for mapping in _mappings(config, selected):
            log(f"{label}: iniciando {mapping['name']}")
            remote = graph.remote_tree(mapping["remote_dir"], config["tenant_id"],
                                       config["client_id"], mapping["recursive"])
            cutoff = _retention_cutoff(mapping, today)
            for name in _expired_dumps(mapping, remote, today):
                log(f"{label} retenção {mapping['name']}: candidato à lixeira após confirmação "
                    f"dos backups recentes: {name}")
            counts = {key: 0 for key in total}
            sizes = {key: 0 for key in total}
            examples = []
            seen = set()
            processed = 0
            for path, relative in _files(mapping, config["ignore"]):
                dump_day = _dump_date(relative, mapping) if cutoff else None
                if dump_day and dump_day < cutoff:
                    continue
                seen.add(relative)
                try:
                    before = path.stat()
                    item = remote.get(relative)
                    if item is None:
                        result = "missing"
                    else:
                        result = "equal" if _same_remote(path, before.st_size, item) else "conflict"
                    after = path.stat()
                    if (after.st_size, after.st_mtime_ns) != (before.st_size, before.st_mtime_ns):
                        result = "changing"
                    if record and result == "equal":
                        sha256 = _hash(path)
                        after = path.stat()
                        if (after.st_size, after.st_mtime_ns) != (before.st_size, before.st_mtime_ns):
                            result = "changing"
                        else:
                            destination = "/".join(filter(None, (mapping["remote_dir"], relative)))
                            state.observe(connection, destination, before.st_size, before.st_mtime_ns)
                            state.mark_uploaded(connection, destination, before.st_size,
                                                before.st_mtime_ns, sha256)
                    counts[result] += 1
                    sizes[result] += before.st_size
                    if result != "equal" and len(examples) < 10:
                        examples.append(f"{result}: {relative}")
                    processed += 1
                    if processed % 1000 == 0:
                        log(f"{label} {mapping['name']}: {processed} arquivos processados")
                except (OSError, RuntimeError, ValueError, KeyError) as error:
                    counts["errors"] += 1
                    if len(examples) < 10:
                        examples.append(f"erro: {relative}: {error}")
            for example in examples:
                log(f"{label} {mapping['name']}: {example}")
            remote_only = len(set(remote) - seen)
            log(f"{label} {mapping['name']}: iguais={counts['equal']}, "
                f"ausentes={counts['missing']}, conflitos={counts['conflict']}, "
                f"alterando={counts['changing']}, erros={counts['errors']}, "
                f"somente_remotos={remote_only}, bytes_ausentes={sizes['missing']}")
            for key in total:
                total[key] += counts[key]
                total_bytes[key] += sizes[key]
        log(f"{label} total: iguais={total['equal']}, ausentes={total['missing']}, "
            f"conflitos={total['conflict']}, alterando={total['changing']}, "
            f"erros={total['errors']}, bytes_ausentes={total_bytes['missing']}")
    finally:
        if connection:
            connection.close()


def plan(config, selected=None):
    _compare(config, selected, record=False)


def reconcile(config, selected=None):
    _compare(config, selected, record=True)


def scan(config, selected=None):
    connection = state.connect()
    today = datetime.now().date()
    try:
        graph._known_folders.clear()
        count = 0
        reused = 0
        errors = 0
        for mapping in _mappings(config, selected):
            cutoff = _retention_cutoff(mapping, today)
            mapping_errors = errors
            for path, relative in _files(mapping, config["ignore"]):
                dump_day = _dump_date(relative, mapping) if cutoff else None
                if dump_day and dump_day < cutoff:
                    continue
                remote = "/".join(filter(None, (mapping["remote_dir"], relative)))
                try:
                    before = path.stat()
                    if not path.is_file():
                        continue
                    previous = state.observation(connection, remote)
                    state.observe(connection, remote, before.st_size, before.st_mtime_ns)
                    if previous != (before.st_size, before.st_mtime_ns):
                        continue
                    old = state.uploaded(connection, remote)
                    if old and old[:2] == (before.st_size, before.st_mtime_ns):
                        continue
                    sha256 = _hash(path)
                    after = path.stat()
                    if (after.st_size, after.st_mtime_ns) != (before.st_size, before.st_mtime_ns):
                        continue
                    if old and old[2] == sha256:
                        if old[:2] != (before.st_size, before.st_mtime_ns):
                            state.mark_uploaded(connection, remote, before.st_size, before.st_mtime_ns, sha256)
                        state.clear_session(connection, str(path))
                        continue
                    if not old:
                        saved = state.session(connection, str(path))
                        resumable = saved and saved[:3] == (
                            before.st_size, before.st_mtime_ns, sha256
                        )
                        existing = graph.remote_file(remote, config["tenant_id"], config["client_id"])
                        if existing:
                            same = _same_remote(path, before.st_size, existing)
                            after = path.stat()
                            if (after.st_size, after.st_mtime_ns) != (before.st_size, before.st_mtime_ns):
                                continue
                            if not same:
                                if not (resumable and existing.get("size") == 0):
                                    raise RuntimeError(f"Conflito remoto; não sobrescrito: {remote}")
                            else:
                                state.mark_uploaded(connection, remote, before.st_size,
                                                    before.st_mtime_ns, sha256)
                                state.clear_session(connection, str(path))
                                reused += 1
                                log(f"Já existe igual: {relative}")
                                continue
                    graph.upload(path, remote, before.st_size, before.st_mtime_ns, sha256,
                                 connection, config["tenant_id"], config["client_id"])
                    after = path.stat()
                    if (after.st_size, after.st_mtime_ns) != (before.st_size, before.st_mtime_ns):
                        log(f"Arquivo alterado durante upload; será reenviado: {relative}")
                        continue
                    state.mark_uploaded(connection, remote, before.st_size, before.st_mtime_ns, sha256)
                    count += 1
                    log(f"Upload: {relative} ({before.st_size} bytes)")
                except (OSError, RuntimeError, ValueError, KeyError) as error:
                    errors += 1
                    log(f"Erro: {relative}: {error}")
            if cutoff and errors == mapping_errors:
                try:
                    _prune_dumps(config, mapping, connection, today)
                except (OSError, RuntimeError, ValueError, KeyError) as error:
                    errors += 1
                    log(f"Erro na retenção {mapping['name']}: {error}")
            elif cutoff:
                log(f"Retenção {mapping['name']} adiada: scan com erros")
        log(f"Scan concluído: {count} upload(s), {reused} já existente(s), {errors} erro(s)")
        if errors:
            raise RuntimeError(f"{errors} arquivo(s) falharam; serão tentados novamente")
    finally:
        connection.close()


def watch(config, selected=None):
    while True:
        try:
            scan(config, selected)
        except RuntimeError as error:
            log(f"Erro no scan: {error}")
        time.sleep(config["interval"])
