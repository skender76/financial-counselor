# Proxy d'uscita del sandbox (ITEM-005): unico ponte verso Internet per le
# sessioni avviate con `--net`. Niente codice del progetto: solo tinyproxy e
# la sua configurazione statica. Il file di filtro (l'allowlist, per sessione)
# è montato a runtime da sandbox.sh, non copiato qui.
FROM alpine:3.20

RUN apk add --no-cache tinyproxy

COPY tinyproxy.conf /etc/tinyproxy/tinyproxy.conf

EXPOSE 8888
USER tinyproxy
CMD ["tinyproxy", "-d", "-c", "/etc/tinyproxy/tinyproxy.conf"]
