# odsync

Agradecimento ao [Chacal-Rocha](https://github.com/Chacal-Rocha), parceiro na estruturação do hardware utilizado na validação do odsync.

## Introdução

O **odsync** é uma ferramenta de backup por upload unidirecional de servidores Linux para o OneDrive for Business. Foi pensado para rotinas que precisam preservar arquivos documentais e manter dumps diários de bancos de dados com retenção configurável.

Seu principal diferencial é reunir o envio dos arquivos e a política de retenção dos dumps no mesmo fluxo:

- **Arquivos assentados:** só considera arquivos para upload quando tamanho e data de modificação permanecem iguais entre duas verificações.
- **Destinos configuráveis:** mapeia diferentes pastas locais para destinos distintos no OneDrive.
- **Reutilização do acervo:** a reconciliação registra arquivos remotos iguais e evita seu reenvio.
- **Retenção por data do backup:** mantém os dumps dos últimos dias pela data no nome e só remove os antigos após confirmar todos os backups recentes esperados.
- **Código simples de inspecionar e adaptar:** cinco módulos Python e apenas `requests` como dependência externa direta.

Essas regras prontas atendem especialmente à rotina de preservar documentos e manter, por exemplo, três dias de dumps de bancos de dados.

Funciona pelo terminal, com Python 3.11+, OAuth com PKCE, estado em SQLite e sessões retomáveis para arquivos grandes. Diretórios e padrões são definidos no TOML. A retenção é opcional e restrita aos nomes configurados; arquivos locais nunca são excluídos.

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

`include` filtra caminhos relativos; `ignore` filtra caminhos e nomes. Os padrões usam `fnmatch`: `*` também pode abranger `/`; use `recursive = false` para selecionar apenas arquivos diretamente na origem. Com `recursive = true`, `include = ["*.pdf"]` inclui PDFs também em subpastas; `include = ["2026/**"]` restringe a esse ano. Links simbólicos de arquivos são ignorados e diretórios simbólicos não são percorridos.

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

`Último registro no estado local` inclui uploads, reconciliações e atualização de metadados. Não é necessariamente a data da última transferência. `0 erro(s)` também não garante ausência de pendências: arquivos novos ou mudando podem aguardar outra passagem.

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

Com três dias, mantém hoje e os dois dias anteriores, pelo relógio local do servidor. Os mais antigos vão para a lixeira somente após um scan sem erros nesse mapeamento e confirmação de todos os backups esperados: registro de conclusão no SQLite, tamanho remoto positivo e igual ao registrado e arquivo local inalterado quando presente. Se algum estiver pendente, ausente ou vazio, a limpeza é adiada e arquivos com mais de três dias podem permanecer. A regra usa dias de calendário, não os três últimos backups disponíveis. Dumps locais antigos são ignorados para não serem reenviados. Nomes fora dos padrões e arquivos locais são preservados.

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

Verifique os dois resumos `Scan concluído` do dia e eventuais mensagens `Erro` ou `Retenção ... adiada`. Registro antigo no `status` pode significar que não houve novidades; esse comando não comprova a execução diária nem a integridade remota. Logs confirmam a execução, mas não garantem restauração: teste periodicamente a recuperação dos arquivos e bancos. O agendamento não impede que um upload longo continue fora do horário de menor uso do link.

## Ambiente validado

O odsync foi utilizado e validado pelo mantenedor no ambiente abaixo. Esta é a configuração de referência; não representa requisitos mínimos de hardware.

| Componente | Configuração |
| --- | --- |
| Placa-mãe | HP 1998 — chipset Intel Q87 Express |
| Processador | Intel Core i5-4590 @ 3,30 GHz |
| Memória | 4 módulos DDR3 de 8 GB — 32 GB no total |
| Armazenamento | 3 SSDs CeaMere de 512 GB, modelo CMSSDA |
| Sistema operacional | Debian GNU/Linux 12 (bookworm) |

Arquitetura de armazenamento informada pelo mantenedor:

- **RAID 1 — `/dev/md5`:** três partições de aproximadamente 976 MB, destinado à tolerância a falhas na inicialização.
- **RAID 5 — `/dev/md0`:** três partições de aproximadamente 476 GB, com 951,7 GB utilizáveis, montado em `/`.

## Limites e manutenção

- O estado é local, em `~/.config/odsync/state.db`. Preserve-o para evitar repetir a comparação do acervo.
- A estabilidade de tamanho/mtime reduz o risco de enviar arquivo incompleto, mas não substitui a geração atômica do backup: produza `.part` e renomeie ao terminar.
- Uploads interrompidos retomam sessões válidas apenas para o mesmo arquivo, destino, tenant e aplicativo; sessões indisponíveis podem exigir recomeçar. Há retries para rede, 429 e 5xx.
- Acesso delegado pode exigir novo login por revogação, política do tenant ou expiração. Monitore os erros do cron.
- Não publique `config.toml`, tokens, estado, logs ou arquivos de backup. Veja [SECURITY.md](SECURITY.md).

## Atualização

Aguarde os scans terminarem e pare `watch`, se estiver em uso. Atualize fora do horário do cron, com o mesmo usuário do login:

```bash
cd ~/odsync
source venv/bin/activate
cp -p ~/.config/odsync/state.db ~/.config/odsync/state.db.before-update
git pull --ff-only
python -m pip install -r requirements.txt
python cli.py check-config
python cli.py status
```

A cópia do banco pressupõe uma instalação já em uso. Preserve `config.toml`, `token.json` e `state.db`; não é necessário refazer a reconciliação. A migração do SQLite ocorre automaticamente e preserva observações e arquivos registrados.

**Sessões de versões anteriores:** não armazenavam o destino e não podem ser retomadas com segurança. Seus registros são preservados, mas não reutilizados; um upload incompleto pode precisar recomeçar. Se houver um arquivo remoto vazio ou divergente sem sessão identificada, o scan mantém o conflito para análise, sem sobrescrever automaticamente. Se possível, conclua uploads pendentes antes de atualizar. Sessões criadas nesta versão ficam separadas por caminho local, destino, tenant e aplicativo.

Depois, mantenha o cron existente e confira seus resumos. Mudanças de filtro podem revelar arquivos antes omitidos: use `plan --mapping NOME` antes de enviá-los, se estiver usando padrões como `*.pdf` com recursão.

Para contribuir ou executar testes: [CONTRIBUTING.md](CONTRIBUTING.md).

## Licença

Copyright (C) 2026 norisjunior. Distribuído sob a [GNU GPL versão 3](LICENSE) (`GPL-3.0-only`).

## Finalidade do modo `scan`

**Um arquivo novo ou modificado precisa ser observado em dois scans antes do upload.** O primeiro registra tamanho e data de modificação; o segundo envia se esses valores permanecerem iguais. Se mudarem, o arquivo aguarda outra passagem estável. Isso reduz o risco de enviar um arquivo ainda sendo gravado, mas não substitui a geração atômica do backup.

Depois de instalar, configurar e fazer login, execute na pasta do projeto, com o `venv` ativo:

```bash
python cli.py scan
sleep 300
python cli.py scan
```

O exemplo aguarda cinco minutos entre as passagens e processa todos os mapeamentos. Para limitar a operação, use o mesmo `--mapping NOME` nos dois comandos. É normal o primeiro scan de arquivos novos terminar com `0 upload(s)`; arquivos observados em execuções anteriores já podem ser enviados nessa passagem. Arquivos já registrados e inalterados são pulados.

Os dois scans são uma precaução do odsync, não uma exigência do OneDrive. O código não impõe intervalo mínimo: os cinco minutos são definidos pelo agendamento. Um gravador pode pausar e depois continuar; tamanho e mtime estáveis não comprovam conclusão. Um arquivo que apareça apenas na segunda passagem aguardará outro scan, possivelmente no dia seguinte.

Sempre que possível, gere o backup com sufixo `.part` (ignorado pela configuração de exemplo), confirme o sucesso da geração, feche o arquivo e renomeie para o nome final no mesmo sistema de arquivos. Isso permite publicação atômica, mas não substitui testar a restauração do banco.

O cron documentado neste README já executa os dois scans. O modo `watch` também faz as passagens automaticamente. São duas varreduras; arquivos já registrados e inalterados não são reenviados.
