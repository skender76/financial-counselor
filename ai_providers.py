"""
Fornitori AI per il consulente della Dashboard Finanziaria.

Il server chiama solo ask(prompt): quale fornitore usare, con che chiave e con
quale modello si decide in ai-config.json (stessa cartella di questo file,
escluso da git perché contiene le chiavi). Esempio completo in
ai-config.example.json:

    {
      "provider": "anthropic",
      "providers": {
        "anthropic": {"api_key": "sk-ant-...", "model": "claude-opus-5"},
        "openai":    {"api_key": "sk-...",     "model": "..."},
        "gemini":    {"api_key": "...",        "model": "..."}
      }
    }

Per cambiare fornitore basta cambiare "provider": la configurazione viene
riletta a ogni domanda, quindi non serve riavviare il server. Se "api_key" è
vuota si usa la variabile d'ambiente del fornitore (ANTHROPIC_API_KEY,
OPENAI_API_KEY, GEMINI_API_KEY).

Ogni fornitore usa il proprio SDK ufficiale, importato solo quando serve:
basta installare quello del fornitore scelto nell'ambiente virtuale .venv
(es. .venv/bin/python -m pip install openai).

Per aggiungere un fornitore: una sottoclasse di Provider con ask() e una riga
in PROVIDERS. ask() deve restituire il testo della risposta, oppure sollevare
AdvisorError con un messaggio in italiano da mostrare in chat.
"""

import json
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE_DIR, 'ai-config.json')
DEFAULT_PROVIDER = 'anthropic'
TRUNCATED_NOTE = '\n\n[risposta troncata: limite di lunghezza raggiunto]'


class AdvisorError(Exception):
    """Errore da mostrare in chat; status è il codice HTTP di /api/ask."""

    def __init__(self, message, status):
        super().__init__(message)
        self.status = status


class Provider:
    name = ''           # chiave in ai-config.json
    label = ''          # nome leggibile, mostrato nella dashboard
    package = ''        # pacchetto pip dell'SDK ufficiale
    env_var = ''        # variabile d'ambiente di ripiego per la chiave
    default_model = ''  # vuoto = il modello va indicato in ai-config.json

    def __init__(self):
        self._client = None
        self._client_key = None

    def sdk_installed(self):
        try:
            self.import_sdk()
            return True
        except ImportError:
            return False

    def import_sdk(self):
        raise NotImplementedError

    def make_client(self, api_key):
        raise NotImplementedError

    def client(self, api_key):
        # Il client viene ricreato solo se la chiave cambia.
        if self._client is None or api_key != self._client_key:
            self._client = self.make_client(api_key)
            self._client_key = api_key
        return self._client

    def ask(self, api_key, model, prompt):
        raise NotImplementedError


class AnthropicProvider(Provider):
    name = 'anthropic'
    label = 'Anthropic Claude'
    package = 'anthropic'
    env_var = 'ANTHROPIC_API_KEY'
    default_model = 'claude-opus-5'
    max_tokens = 16000

    def import_sdk(self):
        import anthropic
        return anthropic

    def make_client(self, api_key):
        return self.import_sdk().Anthropic(api_key=api_key)

    def ask(self, api_key, model, prompt):
        anthropic = self.import_sdk()
        try:
            response = self.client(api_key).beta.messages.create(
                model=model,
                max_tokens=self.max_tokens,
                thinking={'type': 'adaptive'},
                output_config={'effort': 'medium'},
                messages=[{'role': 'user', 'content': prompt}],
                # Se i filtri di sicurezza rifiutano la richiesta, l'API la
                # ripete lato server su un modello alternativo scelto da Anthropic.
                betas=['server-side-fallback-2026-07-01'],
                extra_body={'fallbacks': 'default'},
            )
        except anthropic.AuthenticationError:
            raise AdvisorError('chiave API Anthropic non valida.', 401)
        except anthropic.BadRequestError as e:
            raise AdvisorError('richiesta rifiutata dal modello (prompt troppo lungo?): ' + e.message, 400)
        except anthropic.RateLimitError:
            raise AdvisorError('troppe richieste in poco tempo, riprova tra un minuto.', 429)
        except anthropic.APIStatusError as e:
            raise AdvisorError('errore API Anthropic (%d): %s' % (e.status_code, e.message), 502)
        except anthropic.APIConnectionError:
            raise AdvisorError('impossibile raggiungere l\'API Anthropic: controlla la connessione internet.', 502)

        if response.stop_reason == 'refusal':
            raise AdvisorError('il modello ha rifiutato di rispondere a questa domanda.', 422)
        text = '\n'.join(b.text for b in response.content if b.type == 'text').strip()
        if response.stop_reason == 'max_tokens':
            text += TRUNCATED_NOTE
        return text


class OpenAIProvider(Provider):
    name = 'openai'
    label = 'OpenAI'
    package = 'openai'
    env_var = 'OPENAI_API_KEY'

    def import_sdk(self):
        import openai
        return openai

    def make_client(self, api_key):
        return self.import_sdk().OpenAI(api_key=api_key)

    def ask(self, api_key, model, prompt):
        openai = self.import_sdk()
        try:
            response = self.client(api_key).responses.create(model=model, input=prompt)
        except openai.AuthenticationError:
            raise AdvisorError('chiave API OpenAI non valida.', 401)
        except openai.BadRequestError as e:
            raise AdvisorError('richiesta rifiutata dal modello (prompt troppo lungo o modello inesistente?): ' + e.message, 400)
        except openai.RateLimitError:
            raise AdvisorError('troppe richieste o credito esaurito su OpenAI, riprova più tardi.', 429)
        except openai.APIStatusError as e:
            raise AdvisorError('errore API OpenAI (%d): %s' % (e.status_code, e.message), 502)
        except openai.APIConnectionError:
            raise AdvisorError('impossibile raggiungere l\'API OpenAI: controlla la connessione internet.', 502)

        text = (response.output_text or '').strip()
        details = getattr(response, 'incomplete_details', None)
        if details is not None and getattr(details, 'reason', None) == 'max_output_tokens':
            text += TRUNCATED_NOTE
        if not text:
            raise AdvisorError('il modello non ha restituito testo (risposta vuota o rifiutata).', 422)
        return text


class GeminiProvider(Provider):
    name = 'gemini'
    label = 'Google Gemini'
    package = 'google-genai'
    env_var = 'GEMINI_API_KEY'

    def import_sdk(self):
        from google import genai
        return genai

    def make_client(self, api_key):
        return self.import_sdk().Client(api_key=api_key)

    def ask(self, api_key, model, prompt):
        from google.genai import errors, types
        try:
            response = self.client(api_key).models.generate_content(
                model=model,
                contents=prompt,
                # Nessuno strumento/funzione: disattiva esplicitamente la
                # chiamata automatica di funzioni (evita un avviso dell'SDK).
                config=types.GenerateContentConfig(
                    automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
                ),
            )
        except errors.APIError as e:
            code = getattr(e, 'code', None) or 0
            # Gemini segnala una chiave non valida come 400 INVALID_ARGUMENT
            # con motivo API_KEY_INVALID, non come 401.
            if code in (401, 403) or 'API_KEY_INVALID' in str(e):
                raise AdvisorError('chiave API Gemini non valida o senza permessi.', 401)
            if code == 429:
                raise AdvisorError('troppe richieste o quota esaurita su Gemini, riprova più tardi.', 429)
            if code == 400 or code == 404:
                raise AdvisorError('richiesta rifiutata da Gemini (prompt troppo lungo o modello inesistente?): %s' % e, 400)
            raise AdvisorError('errore API Gemini (%s): %s' % (code, e), 502)

        text = (response.text or '').strip()
        finish = ''
        if response.candidates:
            finish = str(getattr(response.candidates[0], 'finish_reason', '') or '')
        if not text:
            raise AdvisorError('il modello non ha restituito testo (risposta vuota o bloccata dai filtri di sicurezza).', 422)
        if 'MAX_TOKENS' in finish:
            text += TRUNCATED_NOTE
        return text


PROVIDERS = {p.name: p for p in (AnthropicProvider(), OpenAIProvider(), GeminiProvider())}


def load_config():
    if not os.path.exists(CONFIG_FILE):
        return {}
    try:
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            config = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        raise AdvisorError('ai-config.json non è leggibile (%s): controlla la sintassi JSON.' % e, 500)
    if not isinstance(config, dict):
        raise AdvisorError('ai-config.json deve contenere un oggetto JSON.', 500)
    return config


def resolve():
    """Restituisce (provider, api_key, model) secondo ai-config.json."""
    config = load_config()
    name = config.get('provider') or DEFAULT_PROVIDER
    provider = PROVIDERS.get(name)
    if provider is None:
        raise AdvisorError('fornitore AI "%s" non supportato in ai-config.json (disponibili: %s).'
                           % (name, ', '.join(PROVIDERS)), 500)
    settings = (config.get('providers') or {}).get(name) or {}
    api_key = (settings.get('api_key') or '').strip() or os.environ.get(provider.env_var) or None
    model = (settings.get('model') or '').strip() or provider.default_model
    return provider, api_key, model


def check_ready(provider, api_key, model):
    """Messaggio che spiega cosa manca, oppure None se è tutto pronto."""
    if not provider.sdk_installed():
        return ('SDK di %s non installato: esegui ".venv/bin/python -m pip install %s" e riavvia il server.'
                % (provider.label, provider.package))
    if not api_key:
        return ('nessuna chiave API per %s: inseriscila in ai-config.json (providers.%s.api_key) '
                'oppure nella variabile d\'ambiente %s.' % (provider.label, provider.name, provider.env_var))
    if not model:
        return 'nessun modello indicato per %s: impostalo in ai-config.json (providers.%s.model).' % (
            provider.label, provider.name)
    return None


def ask(prompt):
    provider, api_key, model = resolve()
    problem = check_ready(provider, api_key, model)
    if problem:
        raise AdvisorError(problem, 503)
    return provider.ask(api_key, model, prompt)


def info():
    """Stato del consulente, per la dashboard e per il messaggio di avvio."""
    try:
        provider, api_key, model = resolve()
    except AdvisorError as e:
        return {'provider': None, 'label': None, 'model': None, 'ready': False, 'problem': str(e)}
    problem = check_ready(provider, api_key, model)
    return {'provider': provider.name, 'label': provider.label, 'model': model,
            'ready': problem is None, 'problem': problem}
