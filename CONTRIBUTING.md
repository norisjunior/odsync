# Contribuição

Python 3.11+, `requests` e biblioteca padrão. Preserve os cinco módulos de aplicação e prefira funções pequenas. Evite SDKs, camadas extras e dados específicos de uma instalação.

```bash
python3 -m venv venv
source venv/bin/activate
python -m pip install -r requirements.txt
bash tests.sh
git diff --check
```

Os testes usam arquivos temporários e respostas Graph simuladas; não exigem conta Microsoft nem modificam o OneDrive. Alterações em upload/retenção precisam cobrir falhas, arquivos pendentes e preservação de arquivos fora do escopo. Atualize o exemplo TOML e o README quando mudar a configuração.

Em issues e pull requests, descreva o problema, versão do Python, resultado esperado e log sem dados sensíveis. Consulte [SECURITY.md](SECURITY.md) para vulnerabilidades.
