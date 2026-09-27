"""
Fornitori AI e chiavi API per il consulente della Dashboard Finanziaria.

Le chiavi si gestiscono dalla dashboard (sezione "🔑 Chiavi API": aggiungi,
modifica, elimina, scegli quella in uso) e sono salvate in ai-config.json,
nella stessa cartella di questo file: escluso da git, permessi 600, separato
da dati.json così le chiavi non finiscono mai in export o backup. Formato:

    {
      "active_key_id": "k_1a2b3c4d5e6f",
      "keys": [
        {"id": "k_1a2b3c4d5e6f", "name": "Anthropic personale",
         "provider": "anthropic", "value": "sk-ant-...", "model": "",
         "expires": "2027-01-31", "notes": "...", "created": "..."}
      ]
    }

Il consulente usa solo la chiave attiva (active_key_id): se è scaduta, la
richiesta viene rifiutata con un messaggio chiaro. "model" vuoto = modello
predefinito del fornitore (solo Anthropic ne ha uno; per OpenAI e Gemini è
obbligatorio). Il file viene riletto a ogni richiesta: nessun riavvio.
Il valore di una chiave non viene MAI restituito alla pagina, solo le
ultime 4 cifre.

Ogni fornitore usa il proprio SDK ufficiale, importato solo quando serve:
basta installare quello del fornitore scelto nell'ambiente virtuale .venv
(es. .venv/bin/python -m pip install openai).

Per aggiungere un fornitore: una sottoclasse di Provider con ask() e una riga
in PROVIDERS. ask() deve restituire il testo della risposta, oppure sollevare
AdvisorError con un messaggio in italiano da mostrare in chat.
"""

import datetime
import json
import os
import secrets
import threading

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE_DIR, 'ai-config.json')
EXPIRY_WARNING_DAYS = 14
# Serializza lettura-modifica-scrittura di ai-config.json (il server è multi-thread).
CONFIG_LOCK = threading.RLock()
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


# ===== ai-config.json: elenco delle chiavi API =====

def load_config():
    if not os.path.exists(CONFIG_FILE):
        return {'active_key_id': None, 'keys': []}
    try:
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            config = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        raise AdvisorError('ai-config.json non è leggibile (%s): controlla la sintassi JSON.' % e, 500)
    if not isinstance(config, dict) or not isinstance(config.get('keys', []), list):
        raise AdvisorError('ai-config.json non ha il formato atteso (vedi ai-config.example.json).', 500)
    config.setdefault('keys', [])
    config.setdefault('active_key_id', None)
    return config


def save_config(config):
    # Scrittura atomica (file temporaneo + rename), leggibile solo dall'utente.
    tmp_path = CONFIG_FILE + '.tmp'
    fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as f:
        json.dump(config, f, ensure_ascii=False, indent=2)
    os.chmod(tmp_path, 0o600)
    os.replace(tmp_path, CONFIG_FILE)


def _expiry_date(key):
    try:
        return datetime.date.fromisoformat(key.get('expires') or '')
    except ValueError:
        return None


def _is_expired(key):
    # Una chiave che scade oggi vale ancora per tutto il giorno.
    expiry = _expiry_date(key)
    return expiry is not None and expiry < datetime.date.today()


def _mask(value):
    return '••••' + value[-4:] if len(value) >= 8 else '••••'


def _public_key(key, active_id):
    """Dati di una chiave per la pagina: tutto tranne il valore."""
    provider = PROVIDERS.get(key.get('provider'))
    expiry = _expiry_date(key)
    days_left = (expiry - datetime.date.today()).days if expiry else None
    return {
        'id': key['id'],
        'name': key.get('name', ''),
        'provider': key.get('provider'),
        'provider_label': provider.label if provider else key.get('provider'),
        'model': key.get('model', ''),
        'effective_model': key.get('model') or (provider.default_model if provider else ''),
        'expires': key.get('expires', ''),
        'notes': key.get('notes', ''),
        'created': key.get('created', ''),
        'masked': _mask(key.get('value', '')),
        'active': key['id'] == active_id,
        'expired': _is_expired(key),
        'expires_soon': days_left is not None and 0 <= days_left <= EXPIRY_WARNING_DAYS,
    }


def list_keys():
    with CONFIG_LOCK:
        config = load_config()
    active_id = config.get('active_key_id')
    return {
        'keys': [_public_key(k, active_id) for k in config['keys']],
        'active_key_id': active_id,
        'providers': [{'name': p.name, 'label': p.label, 'default_model': p.default_model,
                       'package': p.package, 'sdk_installed': p.sdk_installed()}
                      for p in PROVIDERS.values()],
    }


def _clean_fields(fields, existing=None):
    """Valida i campi inviati dalla pagina; existing = chiave che si modifica."""
    def text(name, max_len):
        value = fields.get(name, '')
        if not isinstance(value, str):
            raise AdvisorError('campo "%s" non valido.' % name, 400)
        value = value.strip()
        if len(value) > max_len:
            raise AdvisorError('campo "%s" troppo lungo (max %d caratteri).' % (name, max_len), 400)
        return value

    name = text('name', 100)
    if not name:
        raise AdvisorError('il nome della chiave è obbligatorio.', 400)
    provider = text('provider', 40)
    if provider not in PROVIDERS:
        raise AdvisorError('fornitore "%s" non supportato.' % provider, 400)
    value = text('value', 500)
    if not value and existing is None:
        raise AdvisorError('il valore della chiave API è obbligatorio.', 400)
    if any(c.isspace() for c in value):
        raise AdvisorError('il valore della chiave API non può contenere spazi.', 400)
    model = text('model', 100)
    if not model and not PROVIDERS[provider].default_model:
        raise AdvisorError('per %s il modello è obbligatorio (prendi il nome dalla '
                           'documentazione del fornitore).' % PROVIDERS[provider].label, 400)
    expires = text('expires', 10)
    if expires:
        try:
            datetime.date.fromisoformat(expires)
        except ValueError:
            raise AdvisorError('data di scadenza non valida (formato AAAA-MM-GG).', 400)
    notes = text('notes', 2000)

    cleaned = {'name': name, 'provider': provider, 'model': model, 'expires': expires, 'notes': notes}
    # In modifica, valore vuoto = mantieni quello già salvato.
    cleaned['value'] = value or existing['value']
    return cleaned


def _find(config, key_id):
    for key in config['keys']:
        if key['id'] == key_id:
            return key
    raise AdvisorError('chiave non trovata (forse è già stata eliminata).', 404)


def manage_keys(request):
    """Azioni della sezione "Chiavi API": add, update, delete, activate."""
    action = request.get('action')
    with CONFIG_LOCK:
        config = load_config()
        if action == 'add':
            key = _clean_fields(request)
            key['id'] = 'k_' + secrets.token_hex(6)
            key['created'] = datetime.datetime.now().isoformat(timespec='seconds')
            config['keys'].append(key)
            # La prima chiave aggiunta diventa subito quella in uso.
            if not config.get('active_key_id'):
                config['active_key_id'] = key['id']
        elif action == 'update':
            key = _find(config, request.get('id'))
            key.update(_clean_fields(request, existing=key))
        elif action == 'delete':
            key = _find(config, request.get('id'))
            config['keys'].remove(key)
            if config.get('active_key_id') == key['id']:
                config['active_key_id'] = None
        elif action == 'activate':
            key = _find(config, request.get('id'))
            config['active_key_id'] = key['id']
        else:
            raise AdvisorError('azione non valida.', 400)
        save_config(config)
    return list_keys()


# ===== Consulente: usa la chiave attiva =====

def resolve():
    """Restituisce (provider, chiave attiva o None, modello)."""
    with CONFIG_LOCK:
        config = load_config()
    active_id = config.get('active_key_id')
    key = next((k for k in config['keys'] if k['id'] == active_id), None)
    if key is None:
        return None, None, None
    provider = PROVIDERS.get(key.get('provider'))
    if provider is None:
        raise AdvisorError('la chiave "%s" indica un fornitore non supportato (%s).'
                           % (key.get('name'), key.get('provider')), 500)
    return provider, key, key.get('model') or provider.default_model


def check_ready(provider, key, model):
    """Messaggio che spiega cosa manca, oppure None se è tutto pronto."""
    if key is None:
        return 'nessuna chiave API in uso: aggiungila (o scegli quella da usare) nella sezione "🔑 Chiavi API".'
    if _is_expired(key):
        return ('la chiave "%s" è scaduta il %s: aggiornala o scegline un\'altra nella sezione "🔑 Chiavi API".'
                % (key.get('name'), key.get('expires')))
    if not provider.sdk_installed():
        return ('SDK di %s non installato: esegui ".venv/bin/python -m pip install %s" e riavvia il server.'
                % (provider.label, provider.package))
    if not model:
        return 'nessun modello indicato per la chiave "%s": impostalo nella sezione "🔑 Chiavi API".' % key.get('name')
    return None


def ask(prompt):
    provider, key, model = resolve()
    problem = check_ready(provider, key, model)
    if problem:
        raise AdvisorError(problem, 503)
    return provider.ask(key['value'], model, prompt)


def info():
    """Stato del consulente, per la dashboard e per il messaggio di avvio."""
    try:
        provider, key, model = resolve()
    except AdvisorError as e:
        return {'label': None, 'model': None, 'key_name': None, 'ready': False, 'problem': str(e)}
    problem = check_ready(provider, key, model)
    return {'label': provider.label if provider else None, 'model': model,
            'key_name': key.get('name') if key else None,
            'ready': problem is None, 'problem': problem}
