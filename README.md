# Nuvola Registro Elettronico – Home Assistant

Integrazione custom non ufficiale per Nuvola Registro Elettronico (Madisoft).

## Installazione con HACS

Aggiungere questo repository GitHub come repository personalizzato di tipo **Integration** e installare **Nuvola Registro Elettronico**.

## Installazione manuale

Copiare `custom_components/nuvola` nella cartella `/config/custom_components/` di Home Assistant e riavviare Home Assistant.

## Autenticazione

La versione 0.6.4 prova direttamente il flusso web `/login_check` e successivamente `/api-studente/v1/login-from-web`, preservando la sessione HTTP. Se il tenant della scuola è stato migrato esclusivamente al nuovo OIDC/Keycloak, il log dell'integrazione segnalerà esplicitamente che il vecchio endpoint non è più disponibile.

Le API ufficiali Madisoft richiedono un token di accesso nell'header della richiesta.

## Funzioni

- studenti
- frazioni temporali
- voti
- assenze
- note
- compiti
- sensori Home Assistant

Progetto non ufficiale e non affiliato a Madisoft S.r.l.
