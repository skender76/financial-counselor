"""
Invio email (verifica indirizzo, reimpostazione password, avvisi di sicurezza).

Configurazione tramite variabili d'ambiente (qualunque fornitore SMTP):
    DASHBOARD_SMTP_HOST       es. smtp.brevo.com   (senza, l'invio è disattivato)
    DASHBOARD_SMTP_PORT       default 587
    DASHBOARD_SMTP_USER / DASHBOARD_SMTP_PASSWORD
    DASHBOARD_SMTP_FROM       es. "Dashboard Finanziaria <noreply@dash.example.com>"
    DASHBOARD_SMTP_SECURITY   starttls (default) | ssl | none (solo per un relay locale)

I link nelle email usano DASHBOARD_PUBLIC_URL e portano il token nel
"fragment" (#verify=... / #reset=...): il fragment non viene mai inviato al
server né finisce nei log o nell'header Referer.

L'invio avviene in un thread a parte: la risposta HTTP non dipende dall'esito,
quindi non rivela se un indirizzo esiste. Gli errori vanno solo nel log.
"""

import os
import smtplib
import ssl
import sys
import threading
from email.message import EmailMessage

SMTP_HOST = os.environ.get('DASHBOARD_SMTP_HOST', '')
SMTP_PORT = int(os.environ.get('DASHBOARD_SMTP_PORT', '587'))
SMTP_USER = os.environ.get('DASHBOARD_SMTP_USER', '')
SMTP_PASSWORD = os.environ.get('DASHBOARD_SMTP_PASSWORD', '')
SMTP_FROM = os.environ.get('DASHBOARD_SMTP_FROM', '')
SMTP_SECURITY = os.environ.get('DASHBOARD_SMTP_SECURITY', 'starttls')
PUBLIC_URL = os.environ.get('DASHBOARD_PUBLIC_URL', '').rstrip('/')

ENABLED = bool(SMTP_HOST and SMTP_FROM and PUBLIC_URL)
APP_NAME = 'Dashboard Finanziaria'


def _deliver(message):
    context = ssl.create_default_context()
    try:
        if SMTP_SECURITY == 'ssl':
            smtp = smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=20, context=context)
        else:
            smtp = smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20)
        with smtp:
            if SMTP_SECURITY == 'starttls':
                smtp.starttls(context=context)
            if SMTP_USER:
                smtp.login(SMTP_USER, SMTP_PASSWORD)
            smtp.send_message(message)
    except Exception as e:
        print('Errore inviando l\'email "%s": %r' % (message['Subject'], e), file=sys.stderr)


def send(to, subject, body):
    """Mette in coda un'email di testo semplice; non solleva mai eccezioni."""
    if not ENABLED:
        print('Email non inviata (SMTP non configurato): %s' % subject, file=sys.stderr)
        return
    message = EmailMessage()
    message['From'] = SMTP_FROM
    message['To'] = to
    message['Subject'] = subject
    message.set_content(body)
    threading.Thread(target=_deliver, args=(message,), daemon=True).start()


def send_verification(to, token):
    send(to, '%s: conferma il tuo indirizzo' % APP_NAME,
         'Ciao,\n\nper completare la registrazione apri questo link (valido 24 ore):\n\n'
         '%s/#verify=%s\n\nSe non sei stato tu, ignora questa email.\n' % (PUBLIC_URL, token))


def send_already_registered(to):
    send(to, '%s: indirizzo già registrato' % APP_NAME,
         'Ciao,\n\nqualcuno ha provato a registrarsi con questo indirizzo, che ha già un account.\n'
         'Se sei stato tu, accedi oppure usa "Password dimenticata" dalla pagina di accesso:\n\n'
         '%s/\n\nSe non sei stato tu, puoi ignorare questa email.\n' % PUBLIC_URL)


def send_password_reset(to, token):
    send(to, '%s: reimposta la password' % APP_NAME,
         'Ciao,\n\nper scegliere una nuova password apri questo link (valido 1 ora, utilizzabile una volta):\n\n'
         '%s/#reset=%s\n\nSe non l\'hai richiesto tu, ignora questa email: la tua password non cambia.\n'
         % (PUBLIC_URL, token))


def send_security_notice(to, what):
    send(to, '%s: avviso di sicurezza' % APP_NAME,
         'Ciao,\n\n%s\n\nSe non sei stato tu, reimposta subito la password dalla pagina di accesso '
         '("Password dimenticata") e controlla l\'accesso al tuo indirizzo email.\n' % what)
