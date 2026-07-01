"""
Radar BR - Agente de notícias pessoal
Busca (Tavily) -> Seleção/Insight (Groq) -> Envio (Telegram)
"""

import os
import sys
import json
import time
import re
from datetime import datetime, timedelta, timezone

import requests

# ---------- Config ----------

TAVILY_API_KEY = os.environ["TAVILY_API_KEY"]
GROQ_API_KEY = os.environ["GROQ_API_KEY"]
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

STATE_FILE = "sent_articles.json"
RETENTION_DAYS = 7
MAX_RETRIES = 3
RETRY_BACKOFF_SECONDS = [5, 15, 45]

GROQ_MODEL = "llama-3.3-70b-versatile"
TOTAL_NEWS_MIN = 7
TOTAL_NEWS_MAX = 10
DESTAQUES = 3

TOPICS = [
    "política Brasil",
    "economia Brasil",
    "mercado financeiro Brasil",
    "causas sociais Brasil",
    "mudanças climáticas",
    "geopolítica conflitos armados crises internacionais eleições",
    "mercado de tecnologia inteligência artificial",
    "mercado de PMEs pequenas e médias empresas Brasil",
]

NOME_USUARIO = "Samuel"


# ---------- Utilidades ----------

def log(msg):
    print(f"[{datetime.now(timezone.utc).isoformat()}] {msg}", flush=True)


def with_retries(func, service_name, *args, **kwargs):
    """Executa func com até MAX_RETRIES tentativas. Lança RuntimeError se todas falharem."""
    last_error = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            last_error = e
            log(f"[{service_name}] Tentativa {attempt}/{MAX_RETRIES} falhou: {e}")
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF_SECONDS[attempt - 1])
    raise RuntimeError(f"{service_name} falhou após {MAX_RETRIES} tentativas: {last_error}")


# ---------- Estado (deduplicação) ----------

def load_state():
    if not os.path.exists(STATE_FILE):
        return []
    with open(STATE_FILE, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return []


def prune_state(state):
    cutoff = datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)
    pruned = []
    for entry in state:
        try:
            entry_date = datetime.fromisoformat(entry["date"])
        except Exception:
            continue
        if entry_date >= cutoff:
            pruned.append(entry)
    return pruned


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


# ---------- Tavily ----------

def tavily_search_topic(topic):
    url = "https://api.tavily.com/search"
    headers = {"Authorization": f"Bearer {TAVILY_API_KEY}", "Content-Type": "application/json"}
    payload = {
        "query": topic,
        "topic": "news",
        "days": 2,
        "max_results": 5,
        "search_depth": "basic",
        "include_answer": False,
    }
    resp = requests.post(url, headers=headers, json=payload, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    return data.get("results", [])


def collect_candidates(already_sent_urls):
    candidates = []
    seen_urls = set()
    for topic in TOPICS:
        try:
            results = with_retries(tavily_search_topic, "Tavily", topic)
        except RuntimeError as e:
            raise RuntimeError(f"Tavily ({topic}): {e}")

        for r in results:
            article_url = r.get("url")
            if not article_url or article_url in seen_urls or article_url in already_sent_urls:
                continue
            seen_urls.add(article_url)
            content = (r.get("content") or "")[:700]
            candidates.append({
                "topic": topic,
                "title": r.get("title", "")[:200],
                "url": article_url,
                "content": content,
            })
    return candidates


# ---------- Groq ----------

def build_groq_prompt(candidates):
    items_text = "\n\n".join(
        f"[{i}] Tema: {c['topic']}\nTítulo: {c['title']}\nURL: {c['url']}\nConteúdo: {c['content']}"
        for i, c in enumerate(candidates)
    )

    instructions = f"""Você é um curador de notícias para um executivo brasileiro chamado {NOME_USUARIO}.
Abaixo está uma lista de notícias candidatas, numeradas.

Sua tarefa:
1. Selecione entre {TOTAL_NEWS_MIN} e {TOTAL_NEWS_MAX} notícias no total, as mais relevantes, sem cota fixa por tema.
2. Entre as selecionadas, marque as {DESTAQUES} mais importantes como destaque (rank 1, 2, 3).
3. Para cada notícia selecionada, escreva um "insight": não é um resumo da notícia, é uma leitura crítica
   do que aquilo significa na prática — implicações, contexto, ou o motivo de importar agora.
   Insight deve ter entre 2 e 4 frases, em português, tom executivo e direto.

Responda SOMENTE com um JSON válido, sem texto antes ou depois, no formato:
{{
  "selecionadas": [
    {{"indice": 0, "insight": "...", "destaque": true, "rank": 1}},
    {{"indice": 5, "insight": "...", "destaque": false, "rank": null}}
  ]
}}

Notícias candidatas:
{items_text}
"""
    return instructions


def call_groq(prompt):
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
    payload = {
        "model": GROQ_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.3,
        "max_tokens": 3000,
    }
    resp = requests.post(url, headers=headers, json=payload, timeout=60)
    resp.raise_for_status()
    data = resp.json()
    return data["choices"][0]["message"]["content"]


def select_and_generate_insights(candidates):
    if not candidates:
        return []

    prompt = build_groq_prompt(candidates)
    try:
        raw_response = with_retries(call_groq, "Groq", prompt)
    except RuntimeError as e:
        raise RuntimeError(str(e))

    # Remove possíveis fences de markdown (```json ... ```)
    cleaned = re.sub(r"^```(json)?|```$", "", raw_response.strip(), flags=re.MULTILINE).strip()

    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Groq retornou JSON inválido: {e}")

    selecionadas = parsed.get("selecionadas", [])
    result = []
    for item in selecionadas:
        idx = item.get("indice")
        if idx is None or idx >= len(candidates) or idx < 0:
            continue
        candidate = candidates[idx]
        result.append({
            "title": candidate["title"],
            "url": candidate["url"],
            "insight": item.get("insight", "").strip(),
            "destaque": bool(item.get("destaque")),
            "rank": item.get("rank"),
        })

    result = result[:TOTAL_NEWS_MAX]
    return result


# ---------- Telegram ----------

def send_telegram_message(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "disable_web_page_preview": False,
    }
    resp = requests.post(url, json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()


def send_telegram_with_retries(text):
    return with_retries(send_telegram_message, "Telegram", text)


def split_message(text, limit=4000):
    if len(text) <= limit:
        return [text]
    parts = []
    while text:
        parts.append(text[:limit])
        text = text[limit:]
    return parts


def greeting():
    hour = datetime.now(timezone.utc) - timedelta(hours=3)  # BRT
    h = hour.hour
    if h < 12:
        return "Bom dia"
    elif h < 18:
        return "Boa tarde"
    return "Boa noite"


def build_message(selected):
    destaques = sorted([s for s in selected if s["destaque"]], key=lambda x: x.get("rank") or 99)[:DESTAQUES]
    outras = [s for s in selected if s not in destaques]

    linhas = [f"{greeting()}, {NOME_USUARIO}!", ""]
    linhas.append(f"Aqui estão as notícias e insights de hoje, com destaque para essas {len(destaques):02d}:")
    linhas.append("")

    for i, item in enumerate(destaques, start=1):
        linhas.append(f"{i}. {item['insight']}")
        linhas.append(f"🔗 {item['url']}")
        linhas.append("")

    if outras:
        linhas.append("Outras notícias relevantes:")
        for item in outras:
            linhas.append(f"• {item['insight']} — {item['url']}")

    return "\n".join(linhas).strip()


# ---------- Main ----------

def main():
    log("Iniciando execução do Radar BR")

    state = load_state()
    state = prune_state(state)
    already_sent_urls = {entry["url"] for entry in state}

    # 1. Busca (Tavily)
    try:
        candidates = collect_candidates(already_sent_urls)
    except RuntimeError as e:
        log(f"Falha crítica: {e}")
        try:
            send_telegram_with_retries(f"⚠️ Radar BR: falha ao buscar notícias.\nServiço: Tavily\nDetalhe: {e}")
        except RuntimeError as telegram_error:
            log(f"Falha ao notificar erro via Telegram: {telegram_error}")
        sys.exit(1)

    if not candidates:
        log("Nenhuma notícia nova encontrada (todas já enviadas nos últimos dias).")
        send_telegram_with_retries(
            f"{greeting()}, {NOME_USUARIO}! Não encontrei notícias novas hoje (tudo já enviado recentemente)."
        )
        sys.exit(0)

    # 2. Seleção + insight (Groq)
    try:
        selected = select_and_generate_insights(candidates)
    except RuntimeError as e:
        log(f"Falha crítica: {e}")
        try:
            send_telegram_with_retries(f"⚠️ Radar BR: falha ao gerar insights.\nServiço: Groq\nDetalhe: {e}")
        except RuntimeError as telegram_error:
            log(f"Falha ao notificar erro via Telegram: {telegram_error}")
        sys.exit(1)

    if not selected:
        log("Groq não retornou seleção válida.")
        sys.exit(1)

    # 3. Envio (Telegram)
    message = build_message(selected)
    parts = split_message(message)

    try:
        for part in parts:
            send_telegram_with_retries(part)
    except RuntimeError as e:
        log(f"Falha crítica ao enviar mensagem via Telegram: {e}")
        log("Sem fallback configurado. Erro registrado apenas neste log.")
        sys.exit(1)

    # 4. Atualiza estado só após envio confirmado
    now_iso = datetime.now(timezone.utc).isoformat()
    for item in selected:
        state.append({"url": item["url"], "date": now_iso})
    save_state(state)

    log(f"Execução concluída. {len(selected)} notícias enviadas.")


if __name__ == "__main__":
    main()
