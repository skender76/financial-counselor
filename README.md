# Dashboard Finanziaria

Dashboard di finanza personale per un singolo utente, con consulente AI integrato. Gira in locale: nessun build, nessun package manager, nessun servizio esterno obbligatorio.

## Funzionalità

- Patrimonio netto liquido (esclusi fondo pensione/TFR e immobili), investimenti, immobili, mutuo, spese, trasferimenti e flussi.
- Addebiti differiti della carta di credito applicati al saldo alla data di scadenza.
- Mutuo ricorrente applicato automaticamente.
- Esportazione CSV e backup CSV versionato, con importazione protetta da doppia conferma.
- Chat con consulente AI: il modello propone un piano di calcolo, le espressioni sono valutate localmente (niente aritmetica fatta dall'LLM) e poi viene generata la risposta finale.
- Gestione delle chiavi API dalla pagina ("🔑 Chiavi API"), con supporto a più fornitori: Anthropic, OpenAI, Gemini, Mistral, DeepSeek, xAI, OpenRouter.
- Tema chiaro/scuro.

## Requisiti

- Python 3
- Un browser moderno (Chart.js è caricato da CDN)
- L'SDK del fornitore AI scelto, solo se vuoi usare la chat: `anthropic`, `openai` o `google-genai`

## Avvio

```bash
python3 -m venv .venv
.venv/bin/python -m pip install anthropic   # oppure openai / google-genai
.venv/bin/python server.py
```

Il server si avvia su `http://localhost:8765`, apre il browser e scrive i dati in `./dati.json`.
Su macOS puoi anche fare doppio clic su `avvia-dashboard.command`.

Per chiudere usa il pulsante "⏻ Chiudi dashboard", oppure Ctrl+C. Se la porta 8765 è già occupata, viene aperta l'istanza già in esecuzione.

> Aprendo direttamente `financial-dashboard.html` dal disco la dashboard non funziona: servono il server e `dati.json`.

## Configurazione dell'AI

1. Avvia la dashboard e apri la sezione "🔑 Chiavi API".
2. Aggiungi una chiave, scegli il modello e attivala.

Le chiavi sono salvate in `ai-config.json` (permessi 600), mai in `dati.json`, quindi non finiscono in esportazioni o backup. La pagina non riceve mai il valore completo di una chiave. Vedi `ai-config.example.json` per il formato. Solo Anthropic ha un modello predefinito: per gli altri fornitori indica il modello a mano.

## Struttura del progetto

| File | Ruolo |
| --- | --- |
| `financial-dashboard.html` | Tutta la dashboard (HTML, CSS e JS in un unico file) |
| `server.py` | Server locale: serve la pagina ed espone `GET/POST /api/data` su `dati.json` |
| `ai_providers.py` | Livello AI indipendente dal fornitore e gestione delle chiavi |
| `avvia-dashboard.command` | Launcher per macOS |
| `dati.json` | Dati finanziari reali (ignorato da git) |
| `ai-config.json` | Chiavi API (ignorato da git) |

## Dati e sicurezza

- `dati.json` è l'unica fonte di verità: la pagina non usa `localStorage`. Ogni modifica viene inviata al server, con controllo di concorrenza (`ETag` / `If-Match`) per evitare sovrascritture da più schede.
- Il server accetta solo richieste con `Host` `localhost:8765` o `127.0.0.1:8765` e POST con `Content-Type: application/json`, per bloccare DNS rebinding e richieste da altri siti.
- `dati.json` e `ai-config.json` sono in `.gitignore`: non vanno mai committati.
- Dopo aver modificato il codice Python, riavvia il server (la pagina HTML viene invece riletta a ogni richiesta).

## Aggiungere un fornitore AI

Crea una sottoclasse di `Provider` in `ai_providers.py` implementando `import_sdk`, `make_client` e `ask`, registrala in `PROVIDERS` e aggiungi il prefisso della chiave in `detectApiKeyProvider()` nella pagina. Per i fornitori compatibili con OpenAI basta una sottoclasse di `OpenAICompatibleProvider` con il suo `base_url`.

Per maggiori dettagli sull'architettura vedi `CLAUDE.md`.
