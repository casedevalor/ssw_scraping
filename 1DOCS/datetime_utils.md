<h3># 📄 Referência: Dates() (app/utils/datetime_utils.py)</h3>

Catálogo de referência para a classe `Dates` — a biblioteca compartilhada de cálculo de datas usada pelos `_build_date_range()`/`_build_date_ranges()` de quase todas as rotinas. **Não é a documentação de uma rotina** (não tem `bot_*.py` correspondente); é um mapa de "qual método usar" e "quem já usa qual", para não reinventar cálculo de data na mão dentro de um bot novo.

Todas as datas retornadas são strings no formato **`'ddmmyy'`** (ex.: `15/03/2025` → `'150325'`), exceto onde indicado.

---

## 📌 Métodos de data única

| Método | Retorna | Exemplo (hoje = 15/11/2024) | Usado por | Status |
| --- | --- | --- | --- | --- |
| `current_day()` | `str` — hoje | `'151124'` | `bot_019`, `yesterday_and_today()` | Ativo |
| `last_day()` | `str` — ontem | `'141124'` | `yesterday_and_today()` | Ativo (indireto) |
| `fifteen_days_ago()` | `str` — hoje − 15 dias | `'311024'` | — | **Morto** |

---

## 📌 Métodos de período único (`Tuple[str, str]`)

| Método | Retorna (start, end) | Exemplo (hoje = 15/01/2022) | Usado por | Status |
| --- | --- | --- | --- | --- |
| `month_to_date_range()` | 1º do mês atual → hoje | `('010122', '150122')` | — | **Morto** — nenhum bot chama hoje (as rotinas migraram para `previous_and_current_month_ranges()`); mantido caso uma rotina futura precise só do mês corrente |
| `previous_month_range()` | Mês anterior completo (1º → último dia) | `('011221', '311221')` se hoje é janeiro/2022 | — | **Morto** — mesmo motivo de `month_to_date_range()` |
| `year_to_date_range()` | 1º de janeiro do ano atual → hoje | `('010122', '150122')` | `bot_571` | Ativo |
| `last_two_days_range()` | Os dois dias **anteriores** a hoje (hoje **não** entra) | hoje=15/11/2024 → `('131124', '141124')` | — | **Morto** desde 27/09/2026 (a 930 passou a usar `yesterday_and_today()`) — ⚠️ nome pode confundir: soa como "últimos dois dias incluindo hoje", mas exclui hoje. |
| `all_time_range()` | `('010101', <hoje>)` — janela fixa desde 2001 | `('010101', '150125')` | — | **Morto** |

---

## 📌 Métodos de múltiplas datas (`List[str]`)

| Método | Retorna | Exemplo (hoje = 15/11/2024) | Usado por | Status |
| --- | --- | --- | --- | --- |
| `yesterday_and_today()` | `[ontem, hoje]` — duas datas soltas, mais antiga primeiro | `['141124', '151124']` | `bot_458`, `bot_930` | Ativo (desde 27/09/2026) |

---

## 📌 Métodos de múltiplos períodos (`List[Tuple[str, str]]`)

| Método | Retorna | Ordem | Usado por | Status |
| --- | --- | --- | --- | --- |
| `previous_and_current_month_ranges()` | `[mês anterior completo, mês atual até hoje]` — sempre os dois, sem gate (nas 455, fora do backfill de fim de semana) | cronológica | `bot_002`, `bot_036`, `bot_073`, `bot_076`, `bot_119`, `bot_200`, `bot_324`, `bot_411`, `bot_442`, `bot_452`, `bot_467`, `bot_493` | Ativo |
| `previous_and_current_month_to_yesterday_ranges()` | `[mês anterior completo, mês atual até **ontem**]` — hoje nunca entra; no dia 1 devolve só o mês anterior (o mês atual ainda não tem dia fechado) | cronológica | `bot_916` | Ativo (desde 20/10/2026) |
| `last_three_months_ranges()` | 2 meses completos anteriores + mês atual até hoje (3 no total) | cronológica (mais antigo primeiro) | `bot_103` | Ativo |
| `previous_twelve_months_ranges()` | 12 meses completos anteriores — **exclui** o mês atual | cronológica | `bot_455ABH` (sáb 15h), `bot_455CGK` (dom 15h), `bot_455F` (sáb 15h) | Ativo — backfill de fim de semana das 455 (gate de dia + `BACKFILL_HOUR`); fora desse horário elas usam `previous_and_current_month_ranges()` |
| `previous_and_current_fortnight_ranges()` | `[quinzena anterior completa, quinzena atual até hoje]` | cronológica | `bot_306` | Ativo |
| `previous_and_current_year_ranges()` | `[ano anterior completo, ano atual até hoje]` — sempre os dois, sem gate | cronológica | `bot_322` | Ativo |
| `last_three_years_to_date_ranges()` | 2 anos-calendário completos + ano atual **até hoje** | cronológica | `bot_441`, `bot_477` | Ativo |
| `last_three_full_calendar_years_ranges()` | 2 anos-calendário completos + ano atual **até 31/12** (pode cair no futuro) | cronológica | — | **Morto** — irmã de `last_three_years_to_date_ranges()`, mantida caso uma rotina futura precise do ano corrente não recortado |
| `days_of_month_to_date()` | Lista de cada dia individual do mês, do dia 1 até **ontem** (hoje não entra) | cronológica | — | **Morto** |

---

## 🔧 Helpers privados (uso interno da classe, não chame de fora)

- `_month_start(d)` — devolve o dia 1 do mês de `d`.
- `_months_back_ranges(today, n, clip_current_to_today)` — andador genérico de meses pra trás; base de `previous_twelve_months_ranges()` e `last_three_months_ranges()`. `clip_current_to_today=True` inclui o mês atual (crescente); `False` o exclui.
- `_year_ranges(current_year, n_years, clip_current_to)` — base de `previous_and_current_year_ranges()`, `last_three_years_to_date_ranges()` e `last_three_full_calendar_years_ranges()`. `clip_current_to=<data>` corta o ano atual ali; `None` deixa ir até 31/12.

Se for adicionar um novo método de "olhar N meses/anos pra trás", prefira reusar esses dois helpers em vez de escrever o loop de novo — foi exatamente a duplicação que eles resolveram (ver histórico de mudanças no CLAUDE.md/memória do projeto).

---

## 🗂 Ver também

- [CLAUDE.md](../CLAUDE.md) — seção "Bot file pattern", item 3 (regra de quando um bot precisa de `_build_date_range(s)()`).
- Qualquer `1DOCS/bot_XXX.md` da tabela acima para ver o método em uso real dentro de uma rotina.

[⬅ Voltar ao README principal](../README.md)
