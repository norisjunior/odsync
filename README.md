# odsync

O **odsync** é uma ferramenta de backup por upload unidirecional de servidores Linux para o OneDrive for Business. Foi pensado para rotinas que precisam preservar arquivos documentais e manter dumps diários de bancos de dados com retenção configurável.

Seu principal diferencial é reunir o envio dos arquivos e a política de retenção dos dumps no mesmo fluxo:

- **Arquivos assentados:** só considera arquivos para upload quando tamanho e data de modificação permanecem iguais entre duas verificações.
- **Destinos configuráveis:** mapeia diferentes pastas locais para destinos distintos no OneDrive.
- **Reutilização do acervo:** a reconciliação registra arquivos remotos iguais e evita seu reenvio.
- **Retenção por data do backup:** mantém os dumps dos últimos dias pela data no nome e só remove os antigos após confirmar todos os backups recentes esperados.
- **Código simples de inspecionar e adaptar:** cinco módulos Python e apenas `requests` como dependência externa direta.

Essas regras prontas atendem especialmente à rotina de preservar documentos e manter, por exemplo, três dias de dumps de bancos de dados.

Funciona pelo terminal, com Python 3.11+, OAuth com PKCE, estado em SQLite e sessões retomáveis para arquivos grandes. Diretórios e padrões são definidos no TOML. A retenção é opcional e restrita aos nomes configurados; arquivos locais nunca são excluídos.

## Por que executar o scan duas vezes?

**Um arquivo novo precisa ser observado em dois scans antes do upload.** O primeiro registra tamanho e data de modificação; o segundo envia se esses valores permanecerem iguais. Se mudarem, o arquivo aguarda outra passagem estável. Isso reduz o risco de enviar um arquivo ainda sendo gravado, mas não substitui a geração atômica do backup.

Depois de instalar, configurar e fazer login, execute na pasta do projeto, com o `venv` ativo:

```bash
python cli.py scan
sleep 300
python cli.py scan
```

O exemplo aguarda cinco minutos entre as passagens e processa todos os mapeamentos. Para limitar a operação, use o mesmo `--mapping NOME` nos dois comandos. É normal o primeiro scan de arquivos novos terminar com `0 upload(s)`; arquivos observados em execuções anteriores já podem ser enviados nessa passagem. Arquivos já registrados e inalterados são pulados.

O cron documentado abaixo já executa os dois scans. O modo `watch` também faz as passagens automaticamente.

## Instalação

Requisitos: Linux, Git, Python 3.11+ com `venv` e uma conta OneDrive for Business.

```bash
git clone https://github.com/norisjunior/odsync.git
cd odsync
python3 -m venv venv
source venv/bin/activate
python -m pip install -r requirements.txt
mkdir -p ~/.config/odsync
chmod 700 ~/.config/odsync
cp config.example.toml ~/.config/odsync/config.toml
chmod 600 ~/.config/odsync/config.toml
```

Edite a cópia local de [config.example.toml](config.example.toml). Não sobrescreva uma configuração existente ao atualizar. Use seu usuário convencional, o mesmo que fará login e executará o cron.

## Aplicativo no Microsoft Entra

1. Em [Entra](https://entra.microsoft.com), abra **App registrations → New registration** e escolha **Single tenant**.
2. Em **Overview**, copie **Directory (tenant) ID** para `tenant_id` e **Application (client) ID** para `client_id`. Não use Object ID.
3. Em **Authentication**, adicione a plataforma **Mobile and desktop applications** com redirect `http://localhost:53682/`.
4. Em **API permissions → Microsoft Graph → Delegated permissions**, configure `Files.ReadWrite` e `User.Read`. O login solicita também `offline_access` para renovação. O consentimento depende da política da organização.

O aplicativo é um cliente público e não precisa de client secret. O fluxo usa Authorization Code com PKCE e valida `state`. Políticas de Acesso Condicional continuam valendo. [Referência Microsoft](https://learn.microsoft.com/en-us/entra/identity-platform/v2-oauth2-auth-code-flow).

```bash
python cli.py check-config
python cli.py login
```

O login abre um servidor temporário em `127.0.0.1:53682`. Em servidor sem interface gráfica, abra um túnel a partir da máquina com navegador:

```bash
ssh -L 53682:127.0.0.1:53682 usuario@servidor
```

Na sessão SSH, execute o login dentro do projeto e do `venv`; abra a URL impressa no navegador da sua máquina. O túnel deve continuar aberto até concluir. Token e refresh token ficam em `~/.config/odsync/token.json`, modo `600`.

## Configuração e comandos

Cada `[[mapping]]` tem nome único, `local_dir`, `remote_dir`, `recursive` e `include`. O caminho relativo é preservado: `/srv/backup/files/2026/10/documento.pdf` vira `Backup/files/2026/10/documento.pdf` no exemplo. `remote_dir` é relativo à raiz do OneDrive. `~` é aceito em `local_dir`.

`include` filtra caminhos relativos; `ignore` filtra caminhos e nomes. Os padrões usam `fnmatch`: `*` também pode abranger `/`; use `recursive = false` para selecionar apenas arquivos diretamente na origem. Links simbólicos de arquivos são ignorados e diretórios simbólicos não são percorridos.

```bash
python cli.py plan --mapping files
python cli.py reconcile --mapping files
python cli.py scan --mapping files
sleep 300
python cli.py scan --mapping files
python cli.py status
```

| Comando | Comportamento |
| --- | --- |
| `check-config` | Valida o TOML e as regras, sem acessar o OneDrive. |
| `plan` | Compara local/remoto e mostra candidatos à retenção; não envia nem exclui. |
| `reconcile` | Registra arquivos já iguais no SQLite; não envia nem exclui. |
| `scan` | Uma passagem de uploads e, se habilitada, retenção. |
| `watch` | Repete scans, aguardando `interval` segundos após cada passagem. |
| `status` | Resume o estado local de todos os mapeamentos; não verifica o remoto. |

`--mapping` pode ser repetido. Sem ele, os comandos de sincronização usam todos os mapeamentos. O primeiro scan observa arquivos novos; o seguinte só envia se tamanho e mtime não mudaram. Arquivos grandes podem ficar sem mensagens enquanto são calculados os hashes ou enviados os chunks.

Antes de enviar um arquivo ainda não rastreado, o programa consulta o mesmo caminho remoto: tamanho e hash iguais permitem reutilizá-lo; divergência gera conflito. Arquivos já rastreados podem ser atualizados pelo conteúdo local. Alterações feitas diretamente no OneDrive não são monitoradas. A comparação usa SHA-1 ou QuickXorHash remoto (não criptográfico); SHA-256 é armazenado localmente.

## Retenção opcional de bancos

Exemplo para dois dumps diários: `20261029-app.sql.gz` e `20261029-auth.sql.gz`:

```toml
[[mapping]]
name = "databases"
local_dir = "/srv/backup/databases"
remote_dir = "Backup/databases"
recursive = false
include = ["*.sql.gz"]
retention_days = 3
retention_patterns = ["{date}-app.sql.gz", "{date}-auth.sql.gz"]
```

`retention_days` aceita de 1 a 365; `0` ou ausência desativa exclusões. `{date}` representa `AAAAMMDD` e deve iniciar cada padrão, seguido de um sufixo literal: sem curingas ou subdiretórios. Configure todos os tipos de dump que precisam existir diariamente. O destino é livre, mas não pode se sobrepor a outro mapeamento quando tem retenção.

Com três dias, mantém hoje e os dois dias anteriores, pelo relógio local do servidor. Os mais antigos vão para a lixeira somente após um scan sem erros nesse mapeamento e confirmação de todos os backups esperados: registro de conclusão no SQLite, tamanho remoto positivo e igual ao registrado e arquivo local inalterado quando presente. Se algum estiver pendente, ausente ou vazio, a limpeza é adiada. Dumps locais antigos são ignorados para não serem reenviados. Nomes fora dos padrões e arquivos locais são preservados.

`plan` mostra os candidatos, mas pode levar bastante tempo por calcular hashes. A exclusão usa ID e `If-Match` para recusar itens modificados após a listagem. [Referência Microsoft](https://learn.microsoft.com/en-us/graph/api/driveitem-delete?view=graph-rest-1.0).

**Atualização de instalações anteriores:** se já utiliza `retention_days = 3`, acrescente `retention_patterns` com os sufixos reais dos seus dumps antes de atualizar. A nova versão recusa retenção sem padrões explícitos. Os caminhos e nomes existentes dos mapeamentos podem ser mantidos.

## Cron diário às 06:00

Depois de validar os uploads, execute `crontab -e` como o usuário do login. O exemplo pressupõe projeto em `$HOME/odsync` e os nomes `files` e `databases`; ajuste esses valores conforme sua instalação. Adicione como uma única linha:

```cron
0 6 * * * /usr/bin/flock -n "$HOME/.config/odsync/scan.lock" /bin/bash -c 'cd "$HOME/odsync" || exit 1; ./venv/bin/python cli.py scan --mapping files --mapping databases; sleep 300; ./venv/bin/python cli.py scan --mapping files --mapping databases' >> "$HOME/.config/odsync/cron.log" 2>&1
```

O cron usa diretamente o Python do `venv`. Faz dois scans com cinco minutos entre eles. O horário é o do servidor, consultável com `date`. `flock` evita sobreposição entre execuções que usam esse mesmo lock; não rode `watch` ou scans manuais simultaneamente. `interval` só afeta `watch`.

```bash
crontab -l
tail -50 ~/.config/odsync/cron.log
python cli.py status
```

Verifique os dois resumos `Scan concluído` do dia e eventuais mensagens `Erro` ou `Retenção ... adiada`. Upload antigo no `status` pode significar que não houve novidades. Logs confirmam a execução, mas não garantem restauração: teste periodicamente a recuperação dos arquivos e bancos. O agendamento não impede que um upload longo continue fora do horário de menor uso do link.

## Limites e manutenção

- O estado é local, em `~/.config/odsync/state.db`. Preserve-o para evitar repetir a comparação do acervo.
- A estabilidade de tamanho/mtime reduz o risco de enviar arquivo incompleto, mas não substitui a geração atômica do backup: produza `.part` e renomeie ao terminar.
- Uploads interrompidos retomam sessões válidas; sessões indisponíveis podem exigir recomeçar. Há retries para rede, 429 e 5xx.
- Acesso delegado pode exigir novo login por revogação, política do tenant ou expiração. Monitore os erros do cron.
- Não publique `config.toml`, tokens, estado, logs ou arquivos de backup. Veja [SECURITY.md](SECURITY.md).

Para atualizar, sem scan em execução: `git pull --ff-only` e `python -m pip install -r requirements.txt` dentro do `venv`. Para contribuir ou executar testes: [CONTRIBUTING.md](CONTRIBUTING.md).

## Licença

Copyright (C) 2026 norisjunior. Distribuído sob a [GNU GPL versão 3](LICENSE) (`GPL-3.0-only`).
