## xero3.0 API Surfaces

Inventario das superficies expostas do `xero3.0` para documentacao e manutencao.

## Escopo

Este documento cobre:
- API HTTP local implementada no projeto
- comandos slash expostos via Discord
- views persistentes e `custom_id`s relevantes

Este documento nao cobre clientes HTTP de saida, webhooks consumidos por terceiros nem arquivos de teste excluidos do carregamento.

## API HTTP local

Arquivo principal: `app/session_api.py`

A `Session API` so sobe quando `SESSION_API_ENABLED=true`.
Por padrao ela escuta em `http://127.0.0.1:8765`.

Autenticacao:
- `GET /session*` e `POST /session*` exigem `Authorization: Bearer <SESSION_API_TOKEN>` apenas se `SESSION_API_TOKEN` estiver configurado
- `GET /auth` e `POST /discussion` usam um token de usuario gerado pelo comando `/create_auth_token`
- `GET /map*` nao exige autenticacao (equivalente publico ao `/map_info`)

### Endpoints

#### `GET /session`

Retorna a sessao ativa de uma categoria.

Entrada:
- query `categoryType` ou `category`

Possiveis erros:
- `400 missing_category`
- `401 unauthorized`
- `404 no_active_session`
- `404 thread_not_accessible`
- `500 failed_to_collect`

#### `GET /session/{category}`

Mesmo comportamento do endpoint acima, com categoria no path.

#### `GET /auth`

Resolve um token de usuario para dados de autenticacao.

Entrada:
- query `token=...`
- ou header `Authorization: Bearer <user_token>`

Possiveis erros:
- `400 missing_token`
- `404 invalid_token`
- `500 invalid_record`

Resposta esperada:
- `ok`
- `token`
- `user.id`
- `user.name`
- `user.username`
- `user.avatar`
- `user.roles`
- `record.created_at`
- `record.guild_id`

#### `POST /session/review`

Publica o resultado de uma review na sessao ativa.

Entrada:
- query opcional `categoryType`
- query opcional `autoNext=false`
- body JSON com `schemaVersion: 1`

Campos principais do body:
- `schemaVersion`
- `category` ou `categoryType`
- `reviewer` ou `reviewerName`
- `votecrew`
- `postAsPrivate`
- `session.category`
- `session.reviewerUserId`
- `items[]`

Observacao de comportamento:
- quando um item trouxer `decision` explicita (`left_as_is`, `p1ed`, `will_be_discussed` ou `ignored`), essa decisao tem prioridade sobre `importedIgnored`

Possiveis erros:
- `401 unauthorized`
- `400 invalid_json`
- `400 invalid_payload`
- `400 unsupported_schema`
- `400 missing_category`
- `404 no_active_session`
- `400 missing_items`
- `400 cannot_post`
- `500 failed_to_post`
- `500 votecrew_channel_not_configured`
- `500 votecrew_channel_unavailable`
- `500 failed_to_post_votecrew`

#### `POST /session/{category}/review`

Mesmo comportamento do endpoint acima, com categoria no path.

#### `POST /discussion`

Cria uma nova thread de discussao de mapa no forum interno.

Autenticacao obrigatoria:
- query `token=...`
- ou header `Authorization: Bearer <user_token>`

Body JSON:
- `mapCode` ou `code`
- `category`, `categoryType` ou `categoryCode`
- `discType` ou `disc_type` (`PERM`, `EDIT`, `DEPERM` ou `OTHER`)
- `description` ou `discDescription` (obrigatorio quando `discType` for `OTHER`)
- `notify` (booleano, opcional; notifica o servidor publico apenas para `PERM`)

O solicitante e resolvido a partir do token de usuario e aparece como autor no embed da discussao.

Possiveis erros:
- `400 missing_token`
- `400 invalid_json`
- `400 invalid_payload`
- `400 missing_map_code`
- `400 missing_category`
- `400 invalid_category`
- `400 missing_disc_type`
- `400 invalid_disc_type`
- `400 missing_description`
- `400 cannot_create_discussion`
- `401 invalid_token`
- `404 user_not_found`
- `500 invalid_record`

Resposta esperada:
- `ok`
- `threadId`
- `jumpUrl`
- `mapCode`
- `mapAuthor`
- `category`
- `discType`
- `notify`
- `requestedBy.id`
- `requestedBy.name`
- `requestedBy.username`

#### `GET /map/{mapcode}`

Retorna metadados do mapa (mesma base do comando `/map_info`).

Entrada:
- path `mapcode` (ex.: `@1234567` ou `1234567`; use `%40` na URL se necessario)

Resposta esperada:
- `status` (`received`)
- `content.map`
- `content.author`
- `content.category`
- `content.xml`
- `imageUrl` (URL da preview via Mapdraw, quando disponivel)
- `categoryEmoji`

Possiveis erros:
- `400 invalid_map_code`
- `404 map_not_found`
- `500 failed_to_fetch`

#### `GET /map/{mapcode}/image`

Retorna a preview renderizada do mapa.

Entrada:
- path `mapcode`
- query opcional `format=png` (padrao) ou `format=url`

Comportamento:
- `format=png`: corpo binario `image/png`
- `format=url`: texto plano com a URL da imagem (compativel com o legado do xero-ingame)

Possiveis erros:
- `400 invalid_map_code`
- `404 map_not_found`
- `500 failed_to_fetch`
- `500 failed_to_render`

### Postman

Collection pronta para importacao:
- `xero3_session_api.postman_collection.json`

Variaveis da collection:
- `baseUrl`
- `categoryCode`
- `sessionApiToken`
- `userToken`
- `reviewerName`
- `reviewerUserId`

## Comandos slash expostos

Os cogs de producao sao carregados por `app/cogs_loader.py`, que ignora arquivos `*.test.py` e `*.py.test`.

### Publicos globais

- `cogs/public/map_info.py`
  - comando: `/map_info`
  - descricao: consulta metadados e preview de um mapa (preview como anexo PNG)

- `cogs/public/report_map.py`
  - comando: `/report_map`
  - descricao: abre fluxo de denuncia de mapa para revisao

### Privados por guild

Esses comandos sao sincronizados apenas para os servidores listados em `PRIVATE_SERVER_IDS`.
O acesso nao deve depender de `default_permissions` nativas de moderacao, a menos que isso seja uma decisao explicita do produto.

- `cogs/private/map_submissions.py`
  - comando: `/setup_submissions`
  - descricao: cria ou atualiza paineis de submissao

- `cogs/private/announce_map.py`
  - comando: `/announce_map`
  - descricao: anuncia decisao publica de um mapa sem criar thread de discussao; aceita `notify` e `description` opcionais

- `cogs/private/announce_map.py`
  - comando: `/announce_map_move`
  - descricao: anuncia publicamente a mudanca de categoria de um mapa sem criar thread de discussao; aceita `notify` e `description` opcionais

- `cogs/private/reopen_discussion.py`
  - comando: `/reopen_discussion`
  - descricao: reabre a thread de discussao atual, restaura os controles e recria a poll se ela nao existir

- `cogs/private/auth_token.py`
  - comando: `/create_auth_token`
  - descricao: gera token para apps externos

- `cogs/private/perm_changes.py`
  - comando: `/post-perm-change`
  - descricao: publica resumo de mudancas de perm

- `cogs/private/map_xml.py`
  - comando: `/map_xml`
  - descricao: baixa e envia o XML de um mapa

- `cogs/private/create_discussion.py`
  - comando: `/create_discussion`
  - descricao: cria thread de discussao para mapa; **Racing** abre discussao no forum racing com poll PERM (Perm as P17/P27/P37, Low-Perm, Reject)

## Views persistentes e componentes expostos

As views persistentes sao registradas em `bot.py`.

### `ui/close_discussion_view.py`

- `close_discussion:close`
- `close_discussion:close_notify`
- `discussion_controls:refresh_info`
- `discussion_controls:add_public_review`
- `discussion_controls:update_mapcode`
- `discussion_controls:update_category`
- `discussion_controls:add_poll_option` — ao escolher `Perm map` ou `Move map`, exige selecao de categoria alvo; opcoes `PERM` sao gravadas como `Perm as Px`
- `public_review:edit`
- `public_review:delete`

### `ui/report_actions.py`

- `report_actions:discard`
- `report_actions:discuss`
- `report_actions:handle`
- modais:
  - `report_actions:discard_modal`
  - `report_actions:handle_modal`
  - `report_actions:discuss_modal`

### `ui/votecrew_review_view.py`

- `votecrew_review:approve`
- `votecrew_review:approve_manual`
- `votecrew_review:reject`

### `ui/map_submission_view.py`

- `map_submissions:{category}:start`
- `map_submissions:{category}:update_category`
- `map_submissions:{category}:download`
- `map_submissions:{category}:submit_review`
- `map_submissions:{category}:edit_last_review`
- `map_submissions:{category}:toggle_lock`

## Regras de manutencao desta documentacao

Sempre revisar esta documentacao quando houver:
- novo endpoint HTTP
- alteracao de autenticacao ou payload
- novo slash command
- nova view persistente ou novo `custom_id` relevante
- nova collection ou exemplo de uso para integracoes externas
