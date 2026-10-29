# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 norisjunior

import argparse
import tomllib
from pathlib import Path

import auth
import state
import sync


CONFIG_PATH = Path.home() / ".config" / "odsync" / "config.toml"


def load_config():
    with CONFIG_PATH.open("rb") as stream:
        config = tomllib.load(stream)
    for key in ("tenant_id", "client_id"):
        if not isinstance(config.get(key), str) or not config[key]:
            raise ValueError(f"Configuração inválida: {key}")
    config["interval"] = config.get("interval", 60)
    config["ignore"] = config.get("ignore", [])
    if not isinstance(config["interval"], (int, float)) or config["interval"] <= 0:
        raise ValueError("interval deve ser positivo")
    if not isinstance(config["ignore"], list) or not all(isinstance(x, str) for x in config["ignore"]):
        raise ValueError("ignore deve ser uma lista de padrões")
    mappings = config.get("mapping")
    if not isinstance(mappings, list) or not mappings:
        raise ValueError("configure ao menos um [[mapping]]")
    names = set()
    for mapping in mappings:
        for key in ("name", "local_dir", "remote_dir"):
            if not isinstance(mapping.get(key), str) or not mapping[key]:
                raise ValueError(f"Mapeamento inválido: {key}")
        if mapping["name"] in names:
            raise ValueError(f"Mapeamento duplicado: {mapping['name']}")
        names.add(mapping["name"])
        mapping["remote_dir"] = mapping["remote_dir"].strip("/")
        if (not mapping["remote_dir"] or "\\" in mapping["remote_dir"] or
                any(part in ("", ".", "..") for part in mapping["remote_dir"].split("/"))):
            raise ValueError("remote_dir deve indicar uma pasta, sem segmentos vazios, . ou ..")
        mapping["recursive"] = mapping.get("recursive", True)
        mapping["include"] = mapping.get("include", ["*"])
        if not isinstance(mapping["recursive"], bool):
            raise ValueError("recursive deve ser true ou false")
        if not isinstance(mapping["include"], list) or not all(
            isinstance(item, str) for item in mapping["include"]
        ):
            raise ValueError("include deve ser uma lista de padrões")
        days = mapping.get("retention_days", 0)
        if type(days) is not int or not 0 <= days <= 365:
            raise ValueError("retention_days deve ser inteiro entre 0 (desativado) e 365")
        patterns = mapping.get("retention_patterns", [])
        if not isinstance(patterns, list) or not all(isinstance(x, str) for x in patterns):
            raise ValueError("retention_patterns deve ser uma lista de nomes com {date}")
        if days and (mapping["recursive"] or not patterns):
            raise ValueError(f"Retenção em {mapping['name']} exige recursive=false e "
                             "retention_patterns explícitos; consulte config.example.toml")
        for pattern in patterns:
            suffix = pattern.removeprefix("{date}")
            if (not pattern.startswith("{date}") or not suffix or
                    any(char in suffix for char in '{}/*?[]\\:<>|"') or
                    pattern.endswith((".", " ")) or len(patterns) != len(set(patterns))):
                raise ValueError("Cada retention_pattern deve começar com {date}, seguido "
                                 "de um sufixo literal único, sem diretórios ou curingas")
            example = pattern.replace("{date}", "20000101")
            if days and (not sync._included(example, mapping["include"]) or
                         sync._ignored(example, config["ignore"])):
                raise ValueError(f"Padrão de retenção não selecionado para upload: {pattern}")
    for mapping in mappings:
        if mapping.get("retention_days", 0):
            folder = mapping["remote_dir"].casefold()
            for other in mappings:
                target = other["remote_dir"].casefold()
                if other is not mapping and (folder == target or
                        folder.startswith(target + "/") or target.startswith(folder + "/")):
                    raise ValueError("Destino com retenção não pode se sobrepor a outro mapeamento")
    return config


def main():
    parser = argparse.ArgumentParser(prog="odsync")
    parser.add_argument("command", choices=("login", "check-config", "plan", "reconcile", "scan", "watch", "status"))
    parser.add_argument("--mapping", action="append", dest="mappings")
    args = parser.parse_args()
    try:
        if args.command == "status":
            connection = state.connect()
            try:
                count, total, last = state.status(connection)
                print(f"Arquivos: {count}\nTamanho total: {total} bytes\nÚltimo upload: {last + ' UTC' if last else '-'}")
            finally:
                connection.close()
            return
        config = load_config()
        if args.mappings:
            known = {item["name"] for item in config["mapping"]}
            unknown = set(args.mappings) - known
            if unknown:
                raise ValueError(f"Mapeamento inexistente: {', '.join(sorted(unknown))}")
        if args.command == "check-config":
            sync.log(f"Configuração válida: {len(config['mapping'])} mapeamento(s)")
        elif args.command == "login":
            auth.login(config["tenant_id"], config["client_id"])
            sync.log("Login concluído")
        elif args.command == "plan":
            sync.plan(config, args.mappings)
        elif args.command == "reconcile":
            sync.reconcile(config, args.mappings)
        elif args.command == "scan":
            sync.scan(config, args.mappings)
        else:
            sync.watch(config, args.mappings)
    except KeyboardInterrupt:
        parser.exit(130, "Interrompido pelo usuário\n")
    except (OSError, RuntimeError, ValueError, KeyError) as error:
        parser.exit(1, f"Erro: {error}\n")


if __name__ == "__main__":
    main()
