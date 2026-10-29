# Lições aprendidas

- Executar pelo Python do `venv`, inclusive no cron; usar o mesmo usuário que realizou o login.
- PKCE com callback loopback atende clientes públicos; em servidor headless, o navegador precisa alcançar o callback por túnel SSH.
- IDs do tenant/aplicativo são recuperados no Overview do registro no Entra. `offline_access` é solicitado no login; não criar client secret.
- Sessões usam chave local e uploads concluídos usam caminho remoto: limpar a sessão explicitamente após concluir.
- Comparar acervo remoto antes de enviar evita reenvios; hashes de arquivos grandes podem tornar o plano demorado.
- Retenção usa a data no nome, exige confirmação dos backups recentes e ignora originais antigos para não recriá-los.
- Nomes, diretórios e padrões de retenção pertencem à configuração de cada instalação.
- Tornar o repositório público também expõe seu histórico. Remover dados do README atual não os remove de commits antigos.
