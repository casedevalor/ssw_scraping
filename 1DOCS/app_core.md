<h3># 📄 Referência: app/core/</h3>

Catálogo de referência para os cinco módulos de infraestrutura compartilhada que todo `bot_XXX.py` usa — login, upload pro SharePoint, o fluxo da tela 156, o pós-processamento de arquivos baixados e a conferência de colunas (sentinela). **Não é a documentação de uma rotina**; é o mapa de "o que já existe pronto" antes de escrever lógica nova dentro de um bot.

---

## 🔑 `login.py` — autenticação no SSW

| Função | Assinatura | O que faz |
| --- | --- | --- |
| `perform_login(page, routine)` | `async def perform_login(page: Page, routine: str) -> None` | Faz login (domínio + CPF + usuário + senha, todos de `settings`), com retry de até `MAX_LOGIN_ATTEMPTS` (5) tentativas, 5s entre elas. Levanta `RuntimeError` se todas falharem. |
| `check_login_error(page, routine)` | `async def check_login_error(page: Page, routine: str) -> bool` | Helper interno de `perform_login` — verifica se `#errormsglabel` apareceu após o submit; se sim, loga WARNING e dispensa o modal. |

Todo `run()` de bot chama só `perform_login(page, ROUTINE)` logo após abrir a página — não reimplemente login dentro de um bot.

---

## ☁️ `upload_to_sharepoint.py` — envio de arquivos

| Função | Assinatura | O que faz |
| --- | --- | --- |
| `upload_report_file(...)` | `async def upload_report_file(file_name, routine, domain, attempt, max_attempts, report=None) -> None` | Sobe um relatório já baixado (`output/downloads/<routine>/<file_name>`) para `Banco_de_Dados/<domain>/<pasta>` no SharePoint. Levanta `FileNotFoundError` se o arquivo local não existir. |
| `upload_log_file(domain, routine=None)` | `def upload_log_file(domain: str, routine: Optional[str] = None) -> None` (síncrona!) | Sobe `output/logs/<domain>.log` para `Banco_de_Dados/BOTLOGS`. Chamada de dentro de `main()` via `run_in_executor` porque é síncrona. Se o log não existir, só loga WARNING e retorna (não levanta). |
| `_resolve_remote_folder(routine, domain, report)` | privada | Decide a pasta remota. Regra: `455ABH` → pasta compartilhada `"455"` (não `"455ABH"`); `056` com `report` → subpasta `"056/<report>"`; qualquer outra rotina → a própria pasta com o nome da rotina. |

**Se uma rotina nova precisar de uma pasta remota diferente do padrão `Banco_de_Dados/<domain>/<ROUTINE>`**, é aqui — em `_resolve_remote_folder` — que se adiciona a exceção, não no bot.

---

## 📥 `downloads.py` — fluxo da tela 156

Usado por qualquer rotina cujo relatório é gerado assincronamente pelo SSW e precisa ser monitorado/baixado na tela 156 (em vez de um download direto na hora). Ponto de entrada único:

```python
await process_156_report(page, date, routine, domain, attempt, max_attempts, unit=None)
```

Internamente:
1. **Captura o número de sequência** da linha recém-criada na tabela da 156.
2. **`_wait_for_report()`** — atualiza a tela (`MAX_REFRESH_ATTEMPTS = 120`, 4s entre tentativas → até 8 min; subiu de 30 em 27/09/2026 porque algumas empresas demoram mais para liberar o arquivo) até a linha ficar "Concluído / Baixar". Cada atualização (`_refresh()`) clica em "Atualizar" e **espera o HTML da página carregar** (`domcontentloaded`, não `load` — o `load` espera toda imagem/script e pode nunca disparar); clique, carga e leitura da linha têm timeout curto (`STEP_TIMEOUT_MS = 10_000`) e, se a tela travar, a página é recarregada em vez de esperar os 120 s do contexto (se o recarregamento também travar, só segue para a próxima passada — `_refresh()` nunca levanta erro). Se completar sem dados (ex.: "NÃO SELECIONOU CTRCS"), retorna `False` e o download é pulado — não é erro. Se nunca completar, levanta `TimeoutError`.
3. **Baixa o arquivo** via `_download_report()`. O "Baixar" não é link de arquivo: é `ajaxEnvia('DOW<seq>')`, e o SSW **ignora o clique** com o alerta *"Ja existe uma requisicao em andamento."* (já existe uma requisição em andamento) se outra chamada AJAX da tela ainda estiver pendente — era essa a causa dos downloads que não começavam com o arquivo pronto (29/09/2026). Por isso `_click_and_catch_download()` só clica com a tela ociosa (sem requisição pendente + 1 s), e, se o alerta aparecer, espera a requisição pendente terminar e clica de novo (até `MAX_BUSY_RETRIES = 10`). Depois que "Baixar" aparece, **a tela nunca mais é atualizada** — um "Atualizar" dispararia justamente outra requisição. O download é escutado em qualquer página do navegador; se não começar em `DOWNLOAD_TIMEOUT_MS = 60_000`, loga um `WARN` e clica de novo, até `MAX_DOWNLOAD_CLICKS = 4` vezes.
4. **Nomeia e processa** via `ROUTINE_HANDLERS` + `_build_file_name()` (ver gotcha abaixo).
5. **Sobe** via `upload_report_file()`.
6. **Confere as colunas** do arquivo já processado via `check_file()` — só se a rotina tiver baseline em `config/sentinel/<ROUTINE>.yml` (`has_baseline`), ou uma por faixa de cabeçalho quando listada em `SENTINEL_BASELINES` (916: `916-MANIFESTO` e `916-CTRC`, as duas no mesmo arquivo). Roda depois do upload para a linha do sentinel fechar o bloco do arquivo no log; escreve uma linha no log do domínio e nunca levanta erro (ver `column_sentinel.py` abaixo).
7. **Sempre fecha a página** no `finally`, mesmo em erro.

### ⚠️ Gotcha: registrar uma rotina nova na 156

`_build_file_name()` decide o nome do arquivo por um dispatch de `if routine == "XXX"`, e `ROUTINE_HANDLERS` decide qual função de pós-processamento rodar. **Os dois são independentes** — uma rotina não registrada em nenhum dos dois cai silenciosamente no fallback (`_handle_default` + branch "Default" de `_build_file_name`), que gera um nome **sem unidade nem data codificada**. Rotinas atualmente registradas em `ROUTINE_HANDLERS` (em **ordem crescente**, com `_handle_default` por último — mantenha assim): `019`, `076`, `306`, `324`, `441`, `455ABH`, `455CGK`, `455F`, `467`, `477`, `493`, `916`, `930`. Só `324`, `441`, `455ABH` e `455CGK` têm pós-processamento; as demais só gravam o arquivo com o nome certo.

Ao adicionar uma rotina nova que passa pela 156:
1. Adicione uma função `_handle_XXX` (copie `_handle_076` se for por unidade).
2. Adicione um branch em `_build_file_name()` se o nome do arquivo dessa rotina precisar de algo além do padrão `{ano}-{mês}-{domain}-{routine}.{suffix}`.
3. Registre em `ROUTINE_HANDLERS`, no lugar certo da ordem crescente (a função `_handle_XXX` também).
4. Se a rotina tiver baseline em `config/sentinel/`, nada mais a fazer: o `process_156_report` já roda o sentinel para ela. Se o arquivo tiver mais de uma faixa de cabeçalho (como a 916), crie uma baseline por faixa e liste os nomes em `SENTINEL_BASELINES`, logo abaixo de `ROUTINE_HANDLERS`.

(Mesmo gotcha já descrito no CLAUDE.md — repetido aqui porque é o erro mais fácil de esquecer ao criar uma rotina nova.)

---

## 🧮 `data_processing.py` — `Dataframe_Analytics`

Um método por rotina que precisa de limpeza/reshape do arquivo baixado (filtro de coluna, correção de encoding, renomeação). **Nem toda rotina tem um** — só existe quando o download bruto genuinamente precisa de tratamento.

| Método | Rotina | Chamado a partir de |
| --- | --- | --- |
| `processing_sswweb_056(**kwargs)` | 056 | `bot_056.py` diretamente |
| `processing_csv_103(file_path)` | 103 | `bot_103.py` diretamente (via `run_in_executor`, é síncrono/pandas) |
| `processing_sswweb_119(**kwargs)` | 119 | `bot_119.py` diretamente |
| `processing_sswweb_324(file_path)` | 324 | `downloads.py` → `_handle_324` |
| `processing_csv_441(file_path)` | 441 | `bot_441.py` (download direto) e `downloads.py` → `_handle_441` (tela 156) |
| `processing_csv_452(file_name)` | 452 | `bot_452.py` diretamente |
| `processing_csv_455ABH(file_path)` | 455ABH | `downloads.py` → `_handle_455ABH` |
| `processing_csv_455CGK(file_path)` | 455CGK | `downloads.py` → `_handle_455CGK` |

Padrão: recebe o caminho do arquivo (`file_path` ou `file_name` — a assinatura varia por método, confira antes de chamar um novo), processa com Pandas, e retorna o caminho final (que pode mudar de nome). Chamadas são sempre síncronas — se vier de dentro de um bot `async`, passe por `asyncio.get_event_loop().run_in_executor(None, ...)` como os exemplos acima fazem, para não bloquear o event loop.

**Ao adicionar um processor novo**: siga o padrão de nome `processing_<csv|sswweb>_<ROUTINE>`, adicione o método em `Dataframe_Analytics`, e chame-o do bot (ou registre em `downloads.py` se a rotina passa pela 156).

---

## 🛡️ `column_sentinel.py` — conferência de colunas

Compara o cabeçalho de um arquivo baixado com a baseline fixa da rotina (`config/sentinel/<ROUTINE>.yml`: `posição: 'nome'`, começando em 1) e escreve **uma linha** no log do domínio: `OK` (nível próprio `SNTL`, entre INFO e WARN), `DRIFT` (WARNING, com `ADDED` / `REMOVED` / `RENAMED` / `MOVED` / `DUPLICATED` e a letra da coluna no Excel) ou `ERROR` (arquivo ilegível, cabeçalho não encontrado, baseline quebrada). **Nunca bloqueia** — o upload segue normalmente.

| Função | Assinatura | O que faz |
| --- | --- | --- |
| `check_file(file_path, routine, domain)` | `def check_file(file_path: Path, routine: str, domain: str) -> SentinelStatus` (síncrona) | Ponto de entrada. Lê só o cabeçalho, compara com a baseline e loga o resultado. Não levanta exceção. |
| `has_baseline(routine)` | `def has_baseline(routine: str) -> bool` | `True` se existe `config/sentinel/<routine>.yml`. Rotina sem baseline não é conferida (sem erro, sem linha no log). |
| `label_separator` (YAML) | opcional | Células do cabeçalho no formato `rótulo: valor` são comparadas só pelo rótulo (totais da 916, `qtde_ctrc:  2077`). |
| `load_baseline(routine)` | `def load_baseline(routine: str) -> Optional[ColumnBaseline]` | Carrega e valida o YAML com Pydantic (numeração 1..N sem buracos, sem nome vazio/duplicado). Em cache por processo. |
| `compare_columns(expected, actual)` | `def compare_columns(expected: List[str], actual: List[str]) -> ColumnReport` | Classifica as diferenças. Uma coluna inserida vira um único `ADDED` (as seguintes não viram `MOVED`). |

**Onde é chamada:**
- `downloads.py → process_156_report`, depois do processamento e logo após o upload (a linha do sentinel fecha o bloco do arquivo no log) — cobre toda rotina da tela 156 que tenha baseline (019, 076, 306, 324, 441, 455ABH, 455CGK, 455F, 467, 477, 493, 916, 930).
- Direto nos bots, nos caminhos de download sem a 156: `bot_002.py`, `bot_019.py`, `bot_036.py`, `bot_073.py` (um check por csv do zip: `073-FROTA` / `073-TERCEIROS`), `bot_076.py`, `bot_081.py`, `bot_103.py`, `bot_119.py`, `bot_200.py`, `bot_306.py`, `bot_322.py`, `bot_411.py`, `bot_435.py`, `bot_441.py`, `bot_442.py`, `bot_452.py`, `bot_458.py`, `bot_477.py`.

**Formatos:** lê CSV (separador/encoding da baseline) e `.xlsx` (primeira aba — usado pela 119 e pela 324, cujo processamento gera Excel, e pela 458, que monta o Excel a partir da tabela raspada).

**Baselines dormentes (103, 119, 324, 441, 455CGK, 458):** o processamento dessas rotinas trava o cabeçalho de saída (lista fixa no `config.yml` ou nomes fixos no código), então hoje a conferência sempre dá `OK` — não valida nada. As baselines ficam prontas para quando esse método mudar (decisão de 27/09/2026). Detalhes em [column_sentinel.md](./column_sentinel.md#dormant-baselines).

**Regra:** a conferência roda **depois** do processamento (`data_processing.py`). A baseline descreve o arquivo que sai do processamento — o layout que o Power BI espera. Renomeações conhecidas do SSW se ajustam no processamento; a baseline só muda quando o layout esperado muda.

Documentação completa (formato do YAML, leitura do log, como adicionar uma rotina, limitações): [column_sentinel.md](./column_sentinel.md).

---

## 🗂 Ver também

- [datetime_utils.md](./datetime_utils.md) — catálogo equivalente para `app/utils/datetime_utils.py` (`Dates`).
- [CLAUDE.md](../CLAUDE.md) — seção "Bot file pattern" para como esses módulos se encaixam no fluxo de um bot inteiro.

[⬅ Voltar ao README principal](../README.md)
