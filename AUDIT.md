# Auditoria da versão pública

- Primeiro commit independente, sem importar o histórico do projeto privado.
- Configurações, logs, tokens, estado e dados de backup não fazem parte da distribuição.
- Caminhos, nomes de mapeamentos e padrões de retenção definidos pelo operador no TOML.
- Retenção desativada no exemplo; exige confirmação de todos os backups recentes antes de excluir antigos para a lixeira.
- Testes usam SQLite temporário e Graph simulado; validação real do serviço e restauração dependem do ambiente do operador.
- Licença escolhida pelo mantenedor: GPL-3.0-only.

- 2026-11-04: corrigida poda de subpastas em filtros recursivos; sessões isoladas por caminho local, destino, tenant e aplicativo, com migração conservadora do SQLite. Mantidos os dois scans e esclarecidos seus limites, retenção por calendário e significado de `status`. Validação: 26 testes locais com Graph simulado, incluindo regressões e migração; sem operações em backups reais.
