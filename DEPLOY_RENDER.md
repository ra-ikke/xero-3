# Deploy no Render (passo a passo)

Este guia prepara o bot para rodar como **Background Worker** no Render.

## 1) Preparar o repositório Git

```bash
git init
git add .
git commit -m "chore: prepare render deployment"
```

Crie o repositório remoto (GitHub/GitLab) e faça o push:

```bash
git remote add origin https://github.com/SEU_USUARIO/xero3.0.git
git branch -M main
git push -u origin main
```

## 2) Criar o serviço no Render

1. Acesse https://render.com e conecte seu GitHub/GitLab.
2. Clique em **New** → **Background Worker**.
3. Selecione o repositório do bot.
4. Configure:
   - Build Command: `pip install -r requirements.txt`
   - Start Command: `python bot.py`
5. Defina as variáveis de ambiente:
   - `DISCORD_TOKEN` (obrigatória)
   - `PRIVATE_SERVER_IDS` (opcional)
   - `COMMAND_PREFIX` (opcional)
   - `DEBUG` (opcional)
   - `CYPHER_URL`, `MAPDRAW_URL`, `WEBHOOK_URL`, `STATUS_URL`, `MAPDRAW_STATUS_URL` (opcionais)
6. Clique em **Create Worker**.

## 3) Validar funcionamento

- Abra os logs do Render e verifique o login do bot.
- No Discord, execute `/ping` e confirme a resposta.

## Observações

- O free tier do Render pode hibernar. Para 24/7, use plano pago ou uma VM (ex.: Oracle Always Free).
