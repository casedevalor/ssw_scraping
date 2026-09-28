# 🧠 Softweb - SSW Reports via Apsesoft 

<br>

# Empresa:  INVICTA TRANSPORTES EIRELI
## Dominio: IVC

<br>
<h3># 📄 Rotina 930</h3>

Automação para extração, download e envio do relatório da rotina **930** do sistema SSW para o SharePoint, com resolução automática de ReCaptcha.

---

## 📌 Objetivo

Executar diariamente a consulta da rotina 930 no SSW, preenchendo os filtros com os parâmetros corretos (datas), resolvendo o ReCaptcha automaticamente, aguardando a geração do relatório e salvando-o em formato `.csv`, enviando-o para o SharePoint automaticamente.

---

## ⚙️ Funcionamento

Executa para os dois últimos dias, utilizando os períodos retornados por `Dates().get_last_two_days()`. O script resolve o ReCaptcha automaticamente e monitora a geração do arquivo na opção 156.

### 🕒 Agendamento
- A rotina pode ser programada para rodar em horário específico (geralmente via `cron`).
- Utiliza a função `Dates().get_last_two_days()` para definir os períodos do relatório.
- Limpa o log diariamente às 3h (se `hour == 3`).

### 📥 Entrada
- Login no sistema SSW com dados cadastrados no módulo de credenciais (`functions/credentials/login.py`)
- Filtro pré-configurado com:
  - Datas dos dois últimos dias
  - Resolução automática do ReCaptcha

### 📤 Saída
- Arquivo `.csv` salvo localmente em `downloads/930/`
- Upload do arquivo para o SharePoint na pasta: `/<DOMÍNIO>/930`
- Nome do arquivo: `20YY-<DOMÍNIO>-930.csv` (um para cada período processado)

---

## 🗂 Estrutura dos Arquivos

- `bot_930_r.py`: Código principal da rotina
- `functions/dates/date_treatment.py`: Define o range de datas (últimos dois dias)
- `functions/log/log.py`: Registro de logs de execução e erro
- `functions/playwright_functions/login.py`: Login com tratamento de erro
- `functions/playwright_functions/downloads.py`: Monitoramento e download de relatórios
- `functions/recaptcha/recaptcha.py`: Resolução automática do ReCaptcha
- `functions/garbage_colector/garbage.py`: Limpeza dos arquivos antigos

---

## 📌 Dependências

- `Apsesoft_Automatic` 
- `pandas`
- `SharePlum` 
- `asyncio` 
- `playwright`

---

## 📈 Exemplo de execução

```bash
source/envssw/activate
python botstart.py #marcando o import da rotina no script
```

[⬅ Voltar para o README principal](../README.md)