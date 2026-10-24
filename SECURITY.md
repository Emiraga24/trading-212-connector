# Sicherheitsmodell und Grenzen

Ein persönliches Konto, ein Render-Webprozess mit vier Threads, persistente SQLite-Datei.
TLS wird von Render terminiert. Keine Debug-Ausgaben oder HTTP-Zugriffslogs mit OAuth-Queryparametern
im Startbefehl einschalten. Render-Protokolle und Datenträger als vertraulich behandeln.

## Vertrauensgrenzen

- Broker-Keys bleiben in Server-Umgebungsvariablen. MCP nimmt keine Keys oder Passwörter entgegen.
- Feste Broker-Hosts und Pfad-Allowlist; keine frei wählbaren URLs, keine HTTP-Weiterleitungen.
- OAuth: ein statischer vertraulicher Client, exakte Callback-URL, S256-PKCE, kurze Einmal-Codes,
  serverseitige Speicherung nur gehashter Zugriffs-/Refresh-Tokens, gebundene Scopes und Resource.
- OAuth-Login und Orderbestätigung benötigen Browser-Cookie und einmaliges CSRF-Token.
- Login-Limit: höchstens 15 Passwortversuche in 5 Minuten pro Prozess. Globale Grenze kann von einem
  Angreifer zur zeitweisen Anmeldeblockade genutzt werden; bei öffentlichem Missbrauch zusätzliche
  Rate-Limits/WAF auf Hostingebene erforderlich. Keine Mehrbenutzer-Isolation.
- Sitzungs-Cookies: Secure, HttpOnly, SameSite=Lax. Keine Drittanbieter-Skripte auf Bestätigungsseiten.
- Bestätigungslinks zeigen ohne separate Anmeldung keine Orderdetails.
- MCP kann nur lesen oder einen Entwurf erzeugen; allein ein persönlicher Browser-POST kann senden.
- Die Orderdaten bleiben nach Entwurf unveränderlich. Atomic SQLite-Claim vor Broker-POST verhindert
  zwei Sendungen desselben Entwurfs bei Doppelklick oder parallelen Requests.
- Broker-POSTs werden nicht wiederholt. Auch bei Verbindungsabbruch, Prozessabsturz oder HTTP-Fehler
  kann unklar sein, ob Trading 212 die Order angenommen hat. Unklare Tickets blockieren weitere
  Entwürfe für den Ticker. Es gibt keinen automatischen Reconciliation-/Entsperrmechanismus.
- Neue, getrennt bestätigte Entwürfe können weiterhin mehrfach dieselbe Aktie handeln; es gibt keine
  Tagesverlustgrenze, Portfolio-Risikoberechnung oder Handelsstrategie in diesem Connector.

## Betrieb

Persistent Disk unter `/var/data` nicht löschen: Sie enthält OAuth-Status und Order-Auditdaten.
Nicht horizontal skalieren. Session-/OAuth-Geheimnisse mit Passwortmanager erzeugen.
Bei Verdacht auf Kompromittierung zuerst Broker-Key widerrufen und Dienst suspendieren.
Ein Hosting-Administrator oder kompromittierter Server kann Broker-Schlüssel auslesen;
die Browserbestätigung schützt nicht gegen eine vollständige Serverkompromittierung.

Dies ist eine individuell erzeugte erste Version, keine Zertifizierung oder professionelle
Sicherheitsprüfung. Lokale Fake-Broker-Tests ersetzen keinen Demo-End-to-End-Test mit dem jeweiligen
ChatGPT-Konto und der tatsächlichen Broker-API.
