# Trading 212 – persönliche ChatGPT-Verbindung

Eine selbst gehostete Verbindung für **einen Kontoinhaber**. Kontostand und Positionen lesen,
Instrumente suchen, Marktorders zum Kauf oder Verkauf vorbereiten. **Nur dein persönlicher
Bestätigungsklick im Browser sendet eine Order.** Ein ChatGPT-Tool allein kann keine Order absenden.

Status: lokal mit simuliertem Broker getestet. Noch nicht auf deinem Render-Konto bereitgestellt,
noch nicht mit deinem Trading-212-Konto oder deinem ChatGPT-Konto end-to-end geprüft.
Kein offizielles Produkt von Trading 212 oder OpenAI und kein unabhängig sicherheitsgeprüftes Produkt.

## 1. Dateien am iPhone auf GitHub hochladen

1. ZIP herunterladen und in der Dateien-App antippen: Dadurch entsteht ein entpackter Ordner.
2. In Safari `https://github.com/Emiraga24/trading212-connector` öffnen und anmelden.
3. **Add file → Upload files** wählen. Wenn es mobil nicht sichtbar ist, Desktop-Website anfordern.
4. Die Dateien aus dem entpackten Ordner auswählen. **Die ZIP selbst nicht hochladen.**
5. `app.py`, `wsgi.py`, `requirements.txt` und `render.yaml` müssen direkt auf der obersten
   Repository-Ebene liegen; keine zusätzliche Unterordnerebene.
6. Auch `test_app.py`, `README.md`, `SECURITY.md` und nach Möglichkeit `.gitignore` hochladen.
   Die vorhandene README darf durch diese ersetzt werden. **Commit changes** bestätigen.

Keine Schlüssel, Passwörter oder `.env`-Dateien zu GitHub hochladen.
Falls echte Zugangsdaten zuvor als Screenshot geteilt wurden, zuerst das alte API-Schlüsselpaar widerrufen.

## 2. Render einrichten – mit dauerhaftem Speicher

Dieser Dienst benötigt für verlässliche Orderzustände einen **Persistent Disk**.
Das beiliegende Blueprint verwendet einen **kostenpflichtigen Starter-Webdienst plus 1-GB-Disk**.
Den tatsächlich angezeigten Preis vor der Erstellung bei Render prüfen.
Ein kostenloser, einschlafender Dienst ohne dauerhaften Speicher ist für diese Order-Version nicht vorgesehen.

Einfacher Weg: Render-Dashboard → **New → Blueprint** → GitHub verbinden →
`Emiraga24/trading212-connector` auswählen → `render.yaml` laden.
GitHub-Zugriff bei Render ist eine eigene Freigabe; die nicht funktionierende GitHub-Verbindung
von ChatGPT ist dafür nicht erforderlich.

Alternativ **New → Web Service → Git Provider → GitHub**:

| Feld | Wert |
|---|---|
| Name | `trading212-connector` (oder ein verfügbarer Name) |
| Branch | `main` |
| Root Directory | leer, wenn Dateien im Repository-Hauptverzeichnis liegen |
| Runtime / Language | Python 3 |
| Build Command | `pip install -r requirements.txt` |
| Start Command | `gunicorn --workers 1 --threads 4 --bind 0.0.0.0:$PORT --timeout 60 wsgi:app` |
| Health Check Path | `/health` |
| Region | Frankfurt, sofern verfügbar |
| Disk Mount Path | `/var/data` |
| Disk Size | 1 GB |

Genau **einen Worker/eine Instanz** verwenden. Das Zustandsmodell ist für ein persönliches Konto ausgelegt.

## 3. Zugangsdaten ausschließlich in Render eingeben

Unter **Environment → Add Environment Variable** steht links der Name (KEY), rechts der Wert (VALUE).
Beim Blueprint werden manche Werte automatisch gesetzt/generiert; nicht zweimal anlegen.

| KEY | VALUE |
|---|---|
| `T212_API_KEY` | Dein neuer API-Key des echten Invest-Kontos |
| `T212_API_SECRET` | Das dazugehörige API-Secret |
| `T212_ENV` | `live` für dein echtes Konto; `demo` für ein separates Demokonto |
| `ENABLE_TRADING` | `true` für persönlich bestätigte Käufe/Verkäufe; `false` zum Abschalten |
| `APP_PASSWORD` | Ein neues, nur hierfür verwendetes zufälliges Passwort mit mindestens 32 Zeichen |
| `OAUTH_CLIENT_SECRET` | Ein anderes zufälliges Geheimnis mit mindestens 32 Zeichen; Blueprint generiert es |
| `SESSION_SECRET` | Ein drittes zufälliges Geheimnis mit mindestens 32 Zeichen; Blueprint generiert es |
| `OAUTH_REDIRECT_URI` | Die **exakte OAuth-Rückrufadresse**, die ChatGPT für diese Verbindung anzeigt |
| `STATE_DB` | `/var/data/connector.sqlite` |
| `PYTHON_VERSION` | `3.12.12` |

Die drei Passwörter/Geheimnisse sind unterschiedlich. Mit einem Passwortmanager erzeugen und dort sichern.
**APP_PASSWORD ist kein Trading-212-Login-Passwort.**
Render-Admins und der laufende Dienst können auf die hinterlegten Werte zugreifen;
Umgebungsvariablen sind kein Schutz vor einem kompromittierten Hosting-Konto.

`PUBLIC_BASE_URL` ist auf Render normalerweise **nicht nötig**: Der Dienst verwendet die von Render
gesetzte `RENDER_EXTERNAL_URL`. Bei eigener Domain `PUBLIC_BASE_URL=https://deine-domain.example`
setzen, ohne abschließenden Pfad oder `/mcp`.

Trading-212-API-Berechtigungen: Konto, Positionen/Portfolio und offene Orders lesen;
für echte Marktorders zusätzlich die entsprechende Berechtigung zum Platzieren von Orders.
Keine Zugangsdaten im Chat teilen. Keine pauschale Freigabe weiterer API-Berechtigungen erforderlich.

## 4. Die Rückrufadresse und ChatGPT-Verbindung

Du hast das Formular **Benutzerdefinierten MCP-Server hinzufügen** bereits gefunden.

1. Render zeigt beim angelegten Dienst eine Adresse wie `https://DEIN-NAME.onrender.com`.
   Den **tatsächlich von Render vergebenen Namen** verwenden, nicht dieses Beispiel kopieren.
2. In ChatGPT: Name `Trading 212`, Server-URL `https://DEIN-NAME.onrender.com/mcp`, Authentifizierung **OAuth**.
3. **Erweiterte OAuth-Einstellungen** öffnen und die Option für **manuell/statisch vorgegebene
   Client-Zugangsdaten** verwenden, nicht automatische Registrierung/CIMD.
4. Client-ID: `trading212-chatgpt`.
5. Client-Secret: exakt der Wert von `OAUTH_CLIENT_SECRET` aus Render (in dieses geschützte Formular).
6. Die von ChatGPT angezeigte **Redirect-/Callback-URL** kopieren und bei Render als
   `OAUTH_REDIRECT_URI` speichern. Manche Konten zeigen sie erst nach dem ersten Anlegen der Verbindung.
   Falls Render vorher einen Wert verlangt, kann zum Start
   `https://chatgpt.com/connector_platform/oauth_redirect` eingetragen werden. **Das ist nur ein
   möglicher Standard, keine Zusage für dein Konto.** Vor der Anmeldung den angezeigten tatsächlichen
   Wert übernehmen und Render neu bereitstellen.
7. Render speichern/bereitstellen, warten bis der Dienst läuft. `/health` muss `{"status":"ok",...}` liefern.
8. Falls ChatGPT manuelle Endpunkte verlangt: Autorisierung `https://DEIN-NAME.onrender.com/authorize`,
   Token `https://DEIN-NAME.onrender.com/token`, Scopes `portfolio:read orders:prepare`.
9. Verbindung in ChatGPT erstellen bzw. erneut verbinden. Die Anmeldeseite auf **deiner Render-Domain**
   öffnet sich. Dort dein `APP_PASSWORD` eingeben und die Verbindung erlauben.
10. In einem neuen Chat das Plugin auswählen und zuerst nur „Zeige meinen Kontostand“ testen.

Wenn die Oberfläche keine statischen OAuth-Clientdaten zulässt oder eine andere Callback-Domain als
`chatgpt.com`/`chat.openai.com` zeigt, **nicht auf „Keine Authentifizierung“ ausweichen**.
Diese Variante braucht dann eine Anpassung; der beiliegende Dienst unterstützt bewusst nur statische
Clientdaten und eine exakt registrierte HTTPS-Rückrufadresse.

## 5. Käufe und Verkäufe

1. Zuerst ein Instrument suchen lassen. Der exakte API-Ticker lautet z. B. `AAPL_US_EQ`, nicht nur `AAPL`.
2. Einen Entwurf mit **Kauf/Verkauf, Ticker und Stückzahl** anfordern. Eine Stückzahl ist kein Eurobetrag.
3. Der Dienst liefert einen 10 Minuten gültigen Bestätigungslink. **Noch keine Order wurde gesendet.**
4. Link selbst öffnen, mit `APP_PASSWORD` anmelden, Instrument/ISIN, Richtung und Menge kontrollieren.
5. Innerhalb von 2 Minuten auf **Diese Order jetzt an Trading 212 senden** tippen.
6. Den tatsächlichen Order- und Ausführungsstatus in Trading 212 prüfen.

Unterstützt werden **Marktorders für Aktien/ETFs**. Ausführung nur reguläre Handelszeiten
(`extendedHours=false`); außerhalb davon kann der Broker die Order vormerken. Der Ausführungspreis
steht vorher nicht fest. Keine Options-/CFD-/Margin-Logik, keine Limit-/Stop-/Bracket-Orders,
keine automatische Stop-Loss-Überwachung. Bestehende Aufträge in der Trading-212-App verwalten.
Die Plattform entscheidet über Guthaben, verfügbare Stücke, Handelsfähigkeit und Annahme der Order.

Der Dienst wiederholt Broker-Schreibanfragen **niemals automatisch**. Bei Timeout/unklarer Antwort
bleibt der Entwurf `unknown` oder `sending`; ein weiterer Entwurf für diesen Ticker wird blockiert.
Erst bei Trading 212 feststellen, ob die Order existiert. Solche Zustände erfordern anschließend eine
gezielte technische Aufklärung; nicht einfach den Speicher löschen oder eine zweite Order anlegen.

## 6. Betrieb, Abmelden und Grenzen

- `ENABLE_TRADING=false` + erneutes Bereitstellen deaktiviert Orderentwürfe und Bestätigungen.
- Zum vollständigen Abschalten den Render-Dienst suspendieren und den API-Key in Trading 212 widerrufen.
- Die Änderung von `APP_PASSWORD`, `OAUTH_CLIENT_SECRET`, `SESSION_SECRET`, Broker-Schlüsseln oder
  Umgebung macht vorhandene OAuth-Tokens und Orderentwürfe ungültig. Danach neu verbinden.
- Zugriffstokens: 1 Stunde. Rotierende Refresh-Tokens: 7 Tage. Nach längerer Pause erneut anmelden.
- Der Dienst zwischenspeichert Konto-/Positionsdaten bis zu 6 Sekunden, Instrumente bis zu 1 Stunde.
- `/health` testet nur den Dienst, **nicht** die Broker-Anmeldung.
- Nicht als Mehrbenutzer-Service einsetzen oder öffentlich zur Anmeldung anbieten.
- Die elf Day-Trader-Skills sind **nicht** Teil dieses Pakets. Diese Verbindung stellt Kontodaten und
  persönlich bestätigte Orders bereit; ein eigenständiger Handelsroboter oder dauernder Scanner ist sie nicht.
- Deine bestehende ChatGPT-Marktüberwachung wird durch die Bereitstellung nicht automatisch auf
  Depotüberwachung umgestellt. Das muss nach erfolgreichem Kontotest separat eingerichtet werden.
- Hostingkosten und Verfügbarkeit hängen vom gewählten Render-Tarif ab. Kein Garantieversprechen für
  mobile OAuth-Anmeldung oder Zugriff über deinen konkreten ChatGPT-Tarif.

## Tests

```sh
pip install -r requirements.txt
python -m unittest -v test_app
```

Validierung am 24.10.2026: 30 lokale Tests bestanden. Zusätzlich mit dem offiziellen Python-MCP-Client (mcp 1.30.0) Initialisierung, Tool-Auflistung und Kontostandabruf gegen einen lokalen Fake-Broker geprüft.

Die Tests verwenden ausschließlich einen Fake-Broker und Testpasswörter. Keine echten Orders.
Vor produktiver Verwendung zunächst separat mit Demo-Schlüsseln end-to-end testen; danach neue Live-Schlüssel
setzen und mit `T212_ENV=live` neu bereitstellen. Ein bloßer Kontostand-Test beweist keine sichere Orderausführung.

## Quellen

- https://docs.trading212.com/api
- https://docs.trading212.com/api/orders/orders
- https://developers.openai.com/plugins/build/auth
- https://developers.openai.com/plugins/deploy/connect-chatgpt
- https://render.com/docs/deploy-flask
- https://render.com/docs/configure-environment-variables
- https://render.com/docs/disks

Erstellt am 24.10.2026. Änderungen der APIs oder OAuth-Oberfläche können Anpassungen erfordern.
