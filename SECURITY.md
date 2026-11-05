# Segurança

Não envie tokens, códigos OAuth, URLs de sessão de upload, configurações reais ou backups em issues. `token.json` e as URLs guardadas em `state.db` permitem acesso; mantenha `~/.config/odsync` privado e não versionado.

Antes de tornar um repositório público, revise também todos os commits, branches e logs de automação. `.gitignore` protege novos arquivos; não remove segredos do histórico. Se uma credencial foi exposta, revogue-a antes de publicar.

Relate vulnerabilidades pelo canal privado **Security → Report a vulnerability** quando habilitado pelo mantenedor. Se indisponível, peça um canal privado sem publicar detalhes de exploração ou dados sensíveis.

Use um diretório remoto dedicado para backups com retenção. O programa não é um cofre imutável, não valida o conteúdo dos bancos e não sincroniza alterações remotas. Faça testes de restauração e mantenha cópias independentes conforme sua necessidade.
