# Eingangsrechnungen aus Ordner für ERPNext (erpnext_eingangsrechnung)

*English summary below.*

Liest Eingangsrechnungen (PDF, auch ZUGFeRD/Factur-X) aus einem Ordner, benennt sie einheitlich um und legt **Entwürfe** von Eingangsrechnungen in ERPNext an – mit der PDF im Anhang. **Gebucht wird nie automatisch.**

Alles läuft auf dem eigenen Server. Es werden **keine Cloud- oder KI-Dienste** benutzt, keine Rechnungsdaten verlassen das System (DSGVO).

- Entwickelt von [AffiKSys](https://affiksys.com/), siehe [unten](#entwickelt-von).

## Was die App macht

1. **Beim Öffnen der Liste „Eingangsrechnung“** durchsucht ERPNext den eingestellten Ordner samt Unterordnern.
2. **Umbenennen:** PDFs, die noch nicht nach dem Schema
   `JJJJ-MM-TT - Rechnung - Lieferant - Rechnungsnummer[ - KÜRZEL].pdf`
   heißen, erscheinen in einem Fenster mit Vorschlag (Datum, Lieferant, Nummer aus dem PDF-Text). Man prüft, korrigiert, wählt bei privat bezahlten Rechnungen das Kürzel und klickt „Ausführen“. „Keine Rechnung“ blendet eine Datei dauerhaft aus.
3. **Entwürfe:** Für jede fertig benannte PDF ohne passende Eingangsrechnung wird ein Entwurf angelegt:
   Lieferant (über Name oder USt-IdNr.), Rechnungsnummer, Rechnungs- und Buchungsdatum, **Bruttobetrag** als eine Position,
   Aufwandskonto und Text von der letzten Rechnung dieses Lieferanten, gewählte Steuervorlage, PDF als privater Anhang.
4. **Vorhandene Rechnungen** (gleicher Lieferant + Rechnungsnummer) werden nicht doppelt angelegt; die PDF wird auf Wunsch angehängt, wenn noch keine da ist.
5. **Bezahlte Rechnungen verschieben (optional):** Liegt eine Rechnung im Ordner für offene Rechnungen und ist sie in ERPNext bezahlt, wird die Datei in den Zielordner verschoben – je nach Zahlung vom Firmenkonto oder privat (Buchungssatz auf das Konto eines „Zahlers“, z. B. Privateinlage). Bei privater Zahlung wird das Kürzel an den Dateinamen gehängt.

Jede verarbeitete Datei steht in der Liste **Eingangsrechnung Datei** (Status, Rechnung, Prüfsumme). Dateien werden über ihre SHA-256-Prüfsumme wiedererkannt, auch wenn sie verschoben oder umbenannt wurden.

### Erkennung (Regeln, ohne KI)

- E-Rechnungen (ZUGFeRD/Factur-X mit eingebetteter XML): Nummer, Datum, Lieferant, USt-IdNr. und Betrag direkt aus der XML.
- Sonst aus dem PDF-Text: Datum hinter „Rechnungsdatum“, Nummer hinter „Rechnungsnummer/Rechnung Nr.“, Betrag hinter „Zahlbetrag“, „Rechnungsbetrag“, „Endbetrag“, „Gesamt“ usw.
- Lieferant: zuerst die Suchbegriffe aus den Einstellungen (oder `lieferanten.txt` im Rechnungsordner), sonst erste Firma mit Rechtsform, sonst Web-Domain.
- Gescannte PDFs ohne Text: Angaben von Hand im Fenster eintragen; der Betrag bleibt im Entwurf 0 und ist im Hinweis markiert.

## Einstellungen

*Eingangsrechnung Import Einstellungen* (Menü der Liste Eingangsrechnung → „Einstellungen Rechnungsordner“):

| Feld | Bedeutung |
|---|---|
| Import aktiv | Schaltet alles ein. |
| Rechnungsordner | Absoluter Pfad, wie ERPNext ihn sieht, z. B. `/mnt/eingangsrechnungen`. Jederzeit änderbar. |
| Beim Öffnen der Liste prüfen | Automatische Prüfung beim Öffnen. Sonst nur über Menü „Rechnungsordner prüfen“. |
| Stündlich automatisch erfassen | Legt fertig benannte Rechnungen auch im Hintergrund an. |
| Firma | Firma der Entwürfe. |
| Einkaufs-Steuervorlage | Wird in jeden Entwurf eingetragen (z. B. Kleinunternehmer-Vorlage ohne Zeilen). Der Betrag ist immer brutto – bei Vorlagen mit Steuerzeilen dort „Im Basispreis enthalten“ setzen. |
| Gerundeten Gesamtbetrag abschalten | Empfohlen, wenn kein Rundungskonto gepflegt ist. |
| PDF auch an vorhandene Rechnungen anhängen | Hängt die PDF an bereits erfasste Rechnungen ohne Anhang. |
| Lieferanten-Suchbegriffe | `Suchbegriff = Name im Dateinamen`, `!EigeneFirma` wird nie als Lieferant genommen. Leer = `lieferanten.txt` im Ordner. |
| Zahler | Kürzel (z. B. `MHE`), Name, Konto (z. B. Privateinlage). |
| Ablage bezahlter Rechnungen | Unterordner für offene Rechnungen und Zielmuster mit `{jahr}`, `{monat}`, `{monatsname}`, `{kuerzel}` (nach Rechnungsdatum). |

## Installation (Docker / frappe_docker)

1. Den Rechnungsordner auf dem Docker-Host einbinden (z. B. SMB/NFS vom NAS) – mit Schreibrecht, wenn umbenannt/verschoben werden soll. Beispiel `/etc/fstab` (Leerzeichen als `\040`):
   ```
   //NAS-IP/Freigabe/Pfad\040mit\040Leerzeichen  /mnt/eingangsrechnungen  cifs  credentials=/root/.smb-erpnext,uid=1000,gid=1000,file_mode=0660,dir_mode=0770,iocharset=utf8,_netdev,nofail  0  0
   ```
   `uid=1000` ist der Benutzer `frappe` im Container.
2. Den Ordner in die Container **backend**, **queue-short**, **queue-long** und **scheduler** einbinden:
   ```yaml
   volumes:
     - /mnt/eingangsrechnungen:/mnt/eingangsrechnungen
   ```
3. App in `apps.json` aufnehmen und Image neu bauen:
   ```json
   {"url": "https://github.com/<konto>/erpnext_eingangsrechnung", "branch": "main"}
   ```
4. `bench --site <site> install-app erpnext_eingangsrechnung` und `bench --site <site> migrate`.
5. Einstellungen ausfüllen und „Import aktiv“ setzen.

Ohne Docker: `bench get-app https://github.com/<konto>/erpnext_eingangsrechnung` und `bench --site <site> install-app erpnext_eingangsrechnung`.

Voraussetzungen: Frappe/ERPNext 15 oder 16. `pypdf` ist bereits Teil von Frappe.

## Grenzen

- Eine Position je Rechnung (Bruttobetrag). Für Lagerartikel oder mehrere Steuersätze den Entwurf von Hand anpassen.
- Erkennung per Regeln: Ungewöhnliche Layouts brauchen Korrektur im Fenster oder im Entwurf.
- Lieferanten werden nicht automatisch angelegt; fehlt einer, erscheint ein Hinweis.

---

## English summary

Frappe/ERPNext app that watches a folder of incoming supplier invoices (PDF, incl. ZUGFeRD/Factur-X). When the *Purchase Invoice* list is opened, it offers to rename new files to `YYYY-MM-DD - Rechnung - Supplier - InvoiceNo[ - PAYER].pdf` (suggestions from rule-based text extraction), then creates **draft** Purchase Invoices (gross amount, expense account from the supplier's last invoice, PDF attached). Existing invoices are detected by supplier + invoice number. Optionally, paid invoices are moved from the "open invoices" folder to configurable target folders (company bank vs. paid privately by a partner). Nothing is ever submitted automatically, and no external/cloud/AI services are used.

---

## Entwickelt von

<a href="https://affiksys.com/" title="Zur Webseite von AffiKSys: affiksys.com"><img src="https://raw.githubusercontent.com/aksmhe/erpnext_girocode/main/docs/affiksys-karte.png" alt="AffiKSys – Affiliate KDP Systems" width="210"></a>

Entwickelt von der **[Lerner & Heide AffiKSys GbR](https://affiksys.com/)** für den eigenen Einsatz mit ERPNext und kostenlos für alle bereitgestellt.
Fehler oder Ideen? Gern als [Issue auf GitHub](https://github.com/aksmhe/erpnext_eingangsrechnung/issues) melden.

---

## Lizenz

MIT, siehe [license.txt](license.txt). Das englische Original ist rechtlich verbindlich.
Die deutsche Übersetzung zum besseren Verständnis steht in [license_de.txt](license_de.txt).
