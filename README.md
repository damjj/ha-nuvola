## 0.8.6
- Corretto conteggio assenze usando `valori` dell'endpoint `/assenze`.
- Aggiunti i dati delle assenze agli attributi del sensore.
- Recupero Circolari esteso oltre il limite precedente di 25.
- Rimosso il recupero Compiti non presente nell'interfaccia Nuvola.

# Nuvola Registro Elettronico – Home Assistant

Integrazione custom non ufficiale per Nuvola Registro Elettronico (Madisoft).

## Installazione con HACS

Aggiungere questo repository GitHub come repository personalizzato di tipo **Integration** e installare **Nuvola Registro Elettronico**.

## Installazione manuale

Copiare `custom_components/nuvola` nella cartella `/config/custom_components/` di Home Assistant e riavviare Home Assistant.

## Autenticazione

La versione 0.8.0 prova direttamente il flusso web `/login_check` e successivamente `/api-studente/v1/login-from-web`, preservando la sessione HTTP. Se il tenant della scuola è stato migrato esclusivamente al nuovo OIDC/Keycloak, il log dell'integrazione segnalerà esplicitamente che il vecchio endpoint non è più disponibile.

Le API ufficiali Madisoft richiedono un token di accesso nell'header della richiesta.

## Funzioni


- studenti
- frazioni temporali
- voti
- assenze
- note
- compiti
- sensori Home Assistant
- monitoraggio Bacheca Nuvola
- numero e stato di lettura delle comunicazioni
- ultima comunicazione e relativi allegati

Progetto non ufficiale e non affiliato a Madisoft S.r.l.


## Versione 0.8.4 correttiva + diagnostica

Questa versione è temporanea per diagnosticare il recupero dei dati. Scrive nel log a livello WARNING messaggi con prefisso `[0.8.5 DIAG]`, senza registrare password o token API.

Dopo il riavvio di Home Assistant, cercare `0.8.5 DIAG` nei registri dell'integrazione `nuvola`.
