# Radar BR

Agente pessoal de notícias. Busca (Tavily) → seleciona e gera insight (Groq) → envia (Telegram), diariamente às 7h BRT via GitHub Actions.

## Secrets necessários

Cadastrar em `Settings → Secrets and variables → Actions → New repository secret`:

| Nome | Descrição |
|---|---|
| `TAVILY_API_KEY` | Chave da API da Tavily |
| `GROQ_API_KEY` | Chave da API da Groq |
| `TELEGRAM_BOT_TOKEN` | Token do bot (@Radarbr_bot) |
| `TELEGRAM_CHAT_ID` | ID do chat de destino |

## Testar manualmente

Aba **Actions** → workflow "Radar BR - Envio Diário" → **Run workflow**. Não precisa esperar o horário agendado.

## Arquivos

- `news_agent.py` — script principal (busca, seleção, envio)
- `.github/workflows/daily_news.yml` — agendamento (cron diário + disparo manual)
- `sent_articles.json` — histórico de deduplicação (retenção de 7 dias, atualizado automaticamente pelo workflow)
- `requirements.txt` — dependência única: `requests`

## Comportamento em falhas

- Cada serviço (Tavily, Groq, Telegram) tem até 3 tentativas com backoff.
- Falha total em Tavily ou Groq → aviso enviado via Telegram.
- Falha total em Telegram → sem fallback. Erro fica registrado apenas no log da execução (aba Actions).

## Temas de busca

Política, Economia, Mercado financeiro, Causas sociais, Mudanças climáticas, Geopolítica/conflitos/eleições, Mercado de tecnologia (IA), Mercado de PMEs. Seleção final: 7 a 10 notícias/dia, por relevância, sem cota fixa por tema.
