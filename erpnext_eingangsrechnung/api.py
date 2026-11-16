"""
Eingangsrechnungen aus einem Ordner in ERPNext übernehmen.

Ablauf:
1. pruefen()     – findet PDFs, die noch nicht nach dem Schema benannt sind, und
                   schlägt Datum / Lieferant / Rechnungsnummer vor.
2. umbenennen()  – benennt die vom Benutzer bestätigten Dateien um
                   (oder merkt sie als "keine Rechnung").
3. erfassen()    – legt für jede fertig benannte PDF einen ENTWURF einer
                   Eingangsrechnung mit PDF-Anhang an und verschiebt bezahlte
                   Rechnungen aus dem Ordner für offene Rechnungen.

Gebucht (Submit) wird nie automatisch. Es werden keine externen Dienste benutzt.
"""

import hashlib
import json
import re
import shutil
from pathlib import Path

import frappe
from frappe import _
from frappe.utils import cint, getdate, now_datetime

from erpnext_eingangsrechnung import erkennung as e

EINST = "Eingangsrechnung Import Einstellungen"
LOG = "Eingangsrechnung Datei"
RECHTSFORM_WOERTER = {"gmbh", "mbh", "gbr", "kg", "ohg", "ug", "ag", "partg", "co", "und", "the", "ltd", "inc"}


# ---------------------------------------------------------------- Hilfen

def _darf():
	if not frappe.has_permission("Purchase Invoice", "create"):
		frappe.throw(_("Keine Berechtigung zum Anlegen von Eingangsrechnungen."), frappe.PermissionError)


def _einstellungen():
	return frappe.get_single(EINST)


def _ordner(einst):
	if not einst.ordner:
		frappe.throw(_("In den Einstellungen ist kein Rechnungsordner eingetragen."))
	ordner = Path(einst.ordner)
	if not ordner.is_dir():
		frappe.throw(_("Der Rechnungsordner {0} ist nicht erreichbar.").format(einst.ordner))
	return ordner


def _sicherer_pfad(ordner, rel):
	"""Relativer Pfad aus dem Browser -> echte Datei, nur innerhalb des Ordners."""
	teile = [t for t in str(rel or "").split("/") if t]
	if not teile or any(t == ".." or e.ausgelassen(t) for t in teile):
		frappe.throw(_("Ungültiger Dateipfad."))
	pfad = ordner.joinpath(*teile)
	try:
		pfad.resolve().relative_to(ordner.resolve())
	except ValueError:
		frappe.throw(_("Ungültiger Dateipfad."))
	if pfad.suffix.lower() != ".pdf" or not pfad.is_file():
		frappe.throw(_("Datei nicht gefunden: {0}").format(rel))
	return pfad


def _rel(ordner, pfad):
	return Path(pfad).relative_to(ordner).as_posix()


def _hash(pfad):
	h = hashlib.sha256()
	with open(pfad, "rb") as datei:
		for block in iter(lambda: datei.read(1024 * 1024), b""):
			h.update(block)
	return h.hexdigest()


def _suche_datei(ordner, name):
	direkt = ordner / name
	if direkt.is_file():
		return direkt
	treffer = sorted(
		(p for p in ordner.rglob(name) if not any(e.ausgelassen(t) for t in p.relative_to(ordner).parts)),
		key=lambda p: (len(p.parts), str(p)),
	)
	return treffer[0] if treffer else None


def _regeln(einst, ordner):
	if (einst.lieferanten_liste or "").strip():
		bekannte, eigene = e.lade_lieferanten(zeilen=einst.lieferanten_liste.splitlines())
	else:
		bekannte, eigene = e.lade_lieferanten(_suche_datei(ordner, "lieferanten.txt"))
	# Die eigene Firma steht auf jeder Rechnung -> nie als Lieferant nehmen
	if einst.firma:
		for wort in re.findall(r"\w{5,}", einst.firma):
			if wort.lower() not in RECHTSFORM_WOERTER:
				eigene.append(wort.lower())
	return bekannte, eigene


def _zahler(einst):
	zahler = [{"kuerzel": z.kuerzel, "bezeichnung": z.bezeichnung or z.kuerzel, "konto": z.konto}
		for z in (einst.zahler or [])]
	if not zahler:
		# Rückfall: gesellschafter.txt aus dem Rechnungsordner (nur Kürzel, ohne Konto)
		try:
			datei = _suche_datei(Path(einst.ordner), "gesellschafter.txt")
			zahler = [{"kuerzel": g["kuerzel"], "bezeichnung": g["name"], "konto": None}
				for g in e.lade_gesellschafter(datei)]
		except Exception:
			zahler = []
	return zahler


def _log_eintraege():
	zeilen = frappe.get_all(LOG, fields=["name", "pfad", "sha256", "groesse", "status", "rechnung"])
	return {z.sha256: z for z in zeilen}, {z.pfad: z for z in zeilen}


def _log_setzen(name=None, **werte):
	if name:
		frappe.db.set_value(LOG, name, werte)
		return name
	doc = frappe.get_doc({"doctype": LOG, **werte})
	doc.insert(ignore_permissions=True)
	return doc.name


# ---------------------------------------------------------------- 1. Prüfen

@frappe.whitelist()
def pruefen(manuell=0):
	"""Liste der PDFs, die noch umbenannt werden müssen (mit Vorschlägen)."""
	_darf()
	einst = _einstellungen()
	if not einst.aktiv:
		return {"aktiv": 0, "fehler": _("Der Ordner-Import ist in den Einstellungen nicht aktiv.")}
	if not cint(manuell) and not einst.beim_oeffnen_pruefen:
		return {"aktiv": 0}
	try:
		ordner = _ordner(einst)
	except frappe.ValidationError as fehler:
		frappe.clear_last_message()
		return {"aktiv": 1, "fehler": str(fehler)}

	bekannte, eigene = _regeln(einst, ordner)
	nach_hash, nach_pfad = _log_eintraege()
	cache = frappe.cache()
	liste = []
	for pfad in e.pdf_dateien(ordner):
		if e.ZIELMUSTER.match(pfad.name) or e.KEINE_RECHNUNG.search(pfad.name):
			continue
		rel = _rel(ordner, pfad)
		st = pfad.stat()
		bekannt = nach_pfad.get(rel)
		if bekannt and bekannt.status == "Ignoriert" and bekannt.groesse == st.st_size:
			continue
		schluessel = f"erpnext_eingangsrechnung|{rel}|{st.st_mtime}|{st.st_size}"
		info = cache.get_value(schluessel)
		if info is None:
			info = e.analysiere(pfad, bekannte, eigene)
			info["sha256"] = _hash(pfad)
			cache.set_value(schluessel, info, expires_in_sec=7 * 24 * 3600)
		if info["sha256"] in nach_hash and nach_hash[info["sha256"]].status == "Ignoriert":
			continue
		liste.append({
			"pfad": rel,
			"datei": pfad.name,
			"ordner": str(Path(rel).parent) if "/" in rel else "",
			"datum": info.get("datum") or "",
			"lieferant": info.get("lieferant") or "",
			"nummer": info.get("nummer") or "",
			"betrag": info.get("betrag"),
			"quelle": info.get("quelle") or "",
			"grund": info.get("grund") or "",
		})
	return {
		"aktiv": 1,
		"umbenennen": liste,
		"zahler": [{"kuerzel": z["kuerzel"], "bezeichnung": z["bezeichnung"]} for z in _zahler(einst)],
	}


# ---------------------------------------------------------------- 2. Umbenennen

@frappe.whitelist(methods=["POST"])
def umbenennen(auftraege):
	"""auftraege: [{pfad, aktion: umbenennen|ignorieren|ueberspringen, datum, lieferant, nummer, kuerzel}]"""
	_darf()
	einst = _einstellungen()
	ordner = _ordner(einst)
	if isinstance(auftraege, str):
		auftraege = json.loads(auftraege)
	erlaubte_kuerzel = {z["kuerzel"] for z in _zahler(einst)}
	ergebnis = []
	for a in auftraege or []:
		aktion = a.get("aktion")
		if aktion not in ("umbenennen", "ignorieren"):
			continue
		try:
			pfad = _sicherer_pfad(ordner, a.get("pfad"))
		except frappe.ValidationError as fehler:
			frappe.clear_last_message()
			ergebnis.append({"pfad": a.get("pfad"), "ok": 0, "meldung": str(fehler)})
			continue

		if aktion == "ignorieren":
			h = _hash(pfad)
			vorhanden = frappe.db.get_value(LOG, {"sha256": h})
			_log_setzen(vorhanden, pfad=_rel(ordner, pfad), sha256=h, groesse=pfad.stat().st_size,
				status="Ignoriert", hinweis=_("Vom Benutzer als 'keine Rechnung' markiert."))
			ergebnis.append({"pfad": a.get("pfad"), "ok": 1, "meldung": _("ignoriert")})
			continue

		datum = (a.get("datum") or "").strip()
		lieferant = e.saeubern(a.get("lieferant") or "")
		nummer = e.saeubern(a.get("nummer") or "")
		kuerzel = (a.get("kuerzel") or "").strip()
		if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", datum) or not lieferant or not nummer:
			ergebnis.append({"pfad": a.get("pfad"), "ok": 0,
				"meldung": _("Datum (JJJJ-MM-TT), Lieferant und Rechnungsnummer sind nötig.")})
			continue
		if kuerzel and kuerzel not in erlaubte_kuerzel:
			ergebnis.append({"pfad": a.get("pfad"), "ok": 0, "meldung": _("Unbekanntes Kürzel {0}.").format(kuerzel)})
			continue
		ziel = e.freier_name(pfad.parent, e.zielname(datum, lieferant, nummer, kuerzel))
		try:
			pfad.rename(ziel)
		except OSError as fehler:
			ergebnis.append({"pfad": a.get("pfad"), "ok": 0,
				"meldung": _("Umbenennen fehlgeschlagen: {0}").format(fehler.strerror)})
			continue
		ergebnis.append({"pfad": a.get("pfad"), "ok": 1, "neu": _rel(ordner, ziel)})
	return ergebnis


# ---------------------------------------------------------------- 3. Erfassen

@frappe.whitelist(methods=["POST"])
def erfassen():
	_darf()
	return _erfassen(_einstellungen())


def stuendlich():
	einst = _einstellungen()
	if not (einst.aktiv and einst.stuendlich):
		return
	try:
		_erfassen(einst)
	except Exception:
		frappe.log_error(title="Eingangsrechnung Import: stündlicher Lauf")


def _erfassen(einst):
	ordner = _ordner(einst)
	if not einst.firma:
		frappe.throw(_("Bitte in den Einstellungen die Firma eintragen."))
	bekannte, eigene = _regeln(einst, ordner)
	zahler = _zahler(einst)
	kuerzel_liste = [z["kuerzel"] for z in zahler]
	nach_hash, nach_pfad = _log_eintraege()

	bericht = {"angelegt": [], "vorhanden": [], "verschoben": [], "fehler": []}
	for pfad in e.pdf_dateien(ordner):
		if not e.ZIELMUSTER.match(pfad.name) or e.KEINE_RECHNUNG.search(pfad.name):
			continue
		rel = _rel(ordner, pfad)
		groesse = pfad.stat().st_size
		eintrag = nach_pfad.get(rel)
		if not (eintrag and eintrag.groesse == groesse):
			h = _hash(pfad)
			eintrag = nach_hash.get(h)
			if eintrag and eintrag.pfad != rel:
				# Datei wurde verschoben oder umbenannt -> Pfad nachziehen
				frappe.db.set_value(LOG, eintrag.name, {"pfad": rel, "groesse": groesse})
				eintrag.pfad = rel
			if not eintrag:
				eintrag = frappe._dict(name=None, sha256=h, status=None, rechnung=None)
		if eintrag.status == "Ignoriert":
			continue
		if not eintrag.rechnung:
			try:
				eintrag = _neue_datei(einst, ordner, pfad, rel, eintrag, bekannte, eigene, kuerzel_liste, bericht)
			except Exception as fehler:
				frappe.db.rollback()
				frappe.clear_last_message()
				meldung = str(fehler) or fehler.__class__.__name__
				bericht["fehler"].append({"datei": rel, "meldung": meldung})
				try:
					_log_setzen(eintrag.name or frappe.db.get_value(LOG, {"sha256": eintrag.sha256}),
						pfad=rel, sha256=eintrag.sha256, groesse=groesse, status="Fehler", hinweis=meldung[:500])
				except Exception:
					pass
				frappe.db.commit()
				continue
		if einst.verschieben and eintrag and eintrag.rechnung:
			try:
				_verschieben_wenn_bezahlt(einst, ordner, pfad, rel, eintrag, zahler, kuerzel_liste, bericht)
			except Exception as fehler:
				frappe.clear_last_message()
				bericht["fehler"].append({"datei": rel, "meldung": _("Verschieben: {0}").format(fehler)})
		frappe.db.commit()

	zusammenfassung = _("{0} Entwurf/Entwürfe angelegt, {1} vorhanden, {2} verschoben, {3} Fehler").format(
		len(bericht["angelegt"]), len(bericht["vorhanden"]), len(bericht["verschoben"]), len(bericht["fehler"]))
	frappe.db.set_single_value(EINST, {"letzter_lauf": now_datetime(), "letztes_ergebnis": zusammenfassung})
	frappe.db.commit()
	bericht["zusammenfassung"] = zusammenfassung
	return bericht


# ---------------------------------------------------------------- Lieferant / Rechnung finden

def _finde_lieferant(name, ust_ids):
	"""Name aus dem Dateinamen (+ USt-IdNr. aus dem PDF) -> Supplier."""
	if ust_ids:
		treffer = frappe.get_all("Supplier", filters={"tax_id": ["in", list(set(ust_ids))], "disabled": 0}, pluck="name")
		if len(treffer) == 1:
			return treffer[0]
	if not name:
		return None
	for filt in (
		{"supplier_name": name},
		{"name": name},
		{"supplier_name": ["like", f"{name}%"]},
		{"supplier_name": ["like", f"%{name}%"]},
	):
		treffer = frappe.get_all("Supplier", filters={**filt, "disabled": 0}, pluck="name", limit=2)
		if len(treffer) == 1:
			return treffer[0]
	erstes_wort = re.findall(r"\w{4,}", name)
	if erstes_wort:
		treffer = frappe.get_all("Supplier", filters={"supplier_name": ["like", f"{erstes_wort[0]}%"], "disabled": 0},
			pluck="name", limit=2)
		if len(treffer) == 1:
			return treffer[0]
	return None


def _finde_rechnung(firma, lieferant, nummer):
	"""Vorhandene (nicht stornierte) Eingangsrechnung mit gleicher Rechnungsnummer."""
	schluessel = e.nummer_schluessel(nummer)
	if not schluessel:
		return None
	filters = {"company": firma, "docstatus": ["<", 2], "bill_no": ["is", "set"]}
	if lieferant:
		filters["supplier"] = lieferant
	for r in frappe.get_all("Purchase Invoice", filters=filters, fields=["name", "bill_no"], order_by="creation desc"):
		if e.nummer_schluessel(r.bill_no) == schluessel:
			return r.name
	return None


def _hat_anhang(rechnung):
	return frappe.db.exists("File", {"attached_to_doctype": "Purchase Invoice", "attached_to_name": rechnung})


def _anhaengen(rechnung, pfad):
	datei = frappe.get_doc({
		"doctype": "File",
		"file_name": pfad.name,
		"attached_to_doctype": "Purchase Invoice",
		"attached_to_name": rechnung,
		"is_private": 1,
		"content": pfad.read_bytes(),
	})
	datei.save(ignore_permissions=True)


def _vorlage(firma, lieferant):
	"""Aufwandskonto/Text/Kostenstelle von der letzten Rechnung dieses Lieferanten."""
	letzte = frappe.get_all("Purchase Invoice",
		filters={"company": firma, "supplier": lieferant, "docstatus": ["<", 2]},
		fields=["name"], order_by="docstatus desc, posting_date desc, creation desc", limit=1)
	if letzte:
		zeile = frappe.get_all("Purchase Invoice Item", filters={"parent": letzte[0].name},
			fields=["item_name", "expense_account", "cost_center"], order_by="idx asc", limit=1)
		if zeile and zeile[0].expense_account:
			return zeile[0]
	return frappe._dict(
		item_name=None,
		expense_account=frappe.get_cached_value("Company", firma, "default_expense_account"),
		cost_center=frappe.get_cached_value("Company", firma, "cost_center"),
	)


# ---------------------------------------------------------------- Entwurf anlegen

def _neue_datei(einst, ordner, pfad, rel, eintrag, bekannte, eigene, kuerzel_liste, bericht):
	teile = e.zerlege_namen(pfad.name, kuerzel_liste)
	if not teile:
		raise frappe.ValidationError(_("Dateiname passt nicht zum Schema."))
	info = e.analysiere(pfad, bekannte, eigene)
	lieferant = _finde_lieferant(teile["lieferant"], info.get("ust_ids"))

	# Originale Rechnungsnummer (z. B. mit "/") aus der E-Rechnung bevorzugen
	nummer = teile["nummer"]
	if info.get("nummer_roh") and e.nummer_schluessel(info["nummer_roh"]) == e.nummer_schluessel(nummer):
		nummer = info["nummer_roh"]

	vorhanden = _finde_rechnung(einst.firma, lieferant, nummer)
	if vorhanden:
		hinweis = _("Rechnung war schon in ERPNext.")
		if einst.pdf_an_vorhandene and not _hat_anhang(vorhanden):
			_anhaengen(vorhanden, pfad)
			hinweis += " " + _("PDF angehängt.")
		name = _log_setzen(eintrag.name, pfad=rel, sha256=eintrag.sha256, groesse=pfad.stat().st_size,
			status="Vorhanden", rechnung=vorhanden, hinweis=hinweis)
		bericht["vorhanden"].append({"datei": rel, "rechnung": vorhanden, "hinweis": hinweis})
		return frappe._dict(name=name, sha256=eintrag.sha256, status="Vorhanden", rechnung=vorhanden, pfad=rel)

	if not lieferant:
		raise frappe.ValidationError(
			_("Lieferant '{0}' nicht gefunden. Bitte in ERPNext anlegen (oder USt-IdNr. eintragen).").format(teile["lieferant"]))

	vorlage = _vorlage(einst.firma, lieferant)
	if not vorlage.expense_account:
		raise frappe.ValidationError(_("Kein Aufwandskonto gefunden (Firma: Standard-Aufwandskonto fehlt)."))

	datum = getdate(teile["datum"])
	betrag = info.get("betrag")
	hinweise = [_("Automatisch erfasst aus: {0}").format(rel), _("Erkannt aus: {0}").format(info.get("quelle"))]
	if betrag is None:
		hinweise.append(_("BETRAG NICHT ERKANNT – bitte eintragen."))
	zahler = next((z for z in _zahler(einst) if z["kuerzel"] == teile["kuerzel"]), None)
	if zahler:
		hinweise.append(_("Privat bezahlt von {0} ({1}).").format(zahler["bezeichnung"], zahler["kuerzel"]))

	pi = frappe.new_doc("Purchase Invoice")
	pi.company = einst.firma
	pi.supplier = lieferant
	pi.bill_no = nummer
	pi.bill_date = datum
	pi.set_posting_time = 1
	pi.posting_date = datum
	pi.disable_rounded_total = cint(einst.rundung_aus)
	pi.remarks = "\n".join(hinweise)
	zeile = {
		"item_name": vorlage.item_name or _("Rechnung {0} {1}").format(teile["lieferant"], nummer),
		"description": _("Rechnung {0} vom {1}").format(nummer, datum.strftime("%d.%m.%Y")),
		"qty": 1,
		"conversion_factor": 1,
		"rate": betrag or 0,
		"expense_account": vorlage.expense_account,
		"cost_center": vorlage.cost_center or frappe.get_cached_value("Company", einst.firma, "cost_center"),
	}
	if frappe.db.exists("UOM", "Nos"):
		zeile.update({"uom": "Nos", "stock_uom": "Nos"})
	pi.append("items", zeile)
	if einst.steuervorlage:
		from erpnext.controllers.accounts_controller import get_taxes_and_charges

		pi.taxes_and_charges = einst.steuervorlage
		pi.set("taxes", [])
		for zeile in get_taxes_and_charges("Purchase Taxes and Charges Template", einst.steuervorlage) or []:
			pi.append("taxes", zeile)
	pi.insert()
	_anhaengen(pi.name, pfad)

	name = _log_setzen(eintrag.name, pfad=rel, sha256=eintrag.sha256, groesse=pfad.stat().st_size,
		status="Entwurf angelegt", rechnung=pi.name,
		hinweis=_("Betrag nicht erkannt.") if betrag is None else "")
	bericht["angelegt"].append({"datei": rel, "rechnung": pi.name, "betrag": betrag, "lieferant": lieferant})
	return frappe._dict(name=name, sha256=eintrag.sha256, status="Entwurf angelegt", rechnung=pi.name, pfad=rel)


# ---------------------------------------------------------------- Bezahlte verschieben

def _aktuelle_rechnung(name):
	"""Bei stornierten und berichtigten Rechnungen der neuesten Fassung folgen."""
	for _i in range(10):
		werte = frappe.db.get_value("Purchase Invoice", name, ["name", "docstatus"], as_dict=True)
		if not werte:
			return None
		if werte.docstatus != 2:
			return werte.name
		neu = frappe.db.get_value("Purchase Invoice", {"amended_from": name}, "name")
		if not neu:
			return None
		name = neu
	return None


def _verschieben_wenn_bezahlt(einst, ordner, pfad, rel, eintrag, zahler, kuerzel_liste, bericht):
	offen = (einst.ordner_offen or "").strip("/")
	if not offen or not (rel + "/").startswith(offen + "/") or rel == offen:
		return
	rechnung = _aktuelle_rechnung(eintrag.rechnung)
	if not rechnung:
		return
	pi = frappe.db.get_value("Purchase Invoice", rechnung, ["docstatus", "status", "bill_date", "posting_date"], as_dict=True)
	if pi.docstatus != 1 or pi.status != "Paid":
		return

	# Privat bezahlt? -> Buchungssatz auf das Konto eines Zahlers
	gefunden = None
	buchungen = frappe.get_all("Journal Entry Account",
		filters={"reference_type": "Purchase Invoice", "reference_name": rechnung, "docstatus": 1}, pluck="parent")
	if buchungen:
		konten = set(frappe.get_all("Journal Entry Account",
			filters={"parent": ["in", buchungen], "docstatus": 1}, pluck="account"))
		gefunden = next((z for z in zahler if z["konto"] and z["konto"] in konten), None)

	datum = getdate(pi.bill_date or pi.posting_date)
	platzhalter = {
		"jahr": f"{datum.year}",
		"monat": f"{datum.month:02d}",
		"monatsname": e.MONATSNAMEN[datum.month],
		"kuerzel": gefunden["kuerzel"] if gefunden else "",
	}
	muster = einst.ziel_zahler if gefunden else einst.ziel_bank
	if not muster:
		return
	ziel_ordner_rel = muster.format(**platzhalter).strip("/")
	if ".." in ziel_ordner_rel.split("/"):
		return
	ziel_ordner = ordner / ziel_ordner_rel
	ziel_ordner.mkdir(parents=True, exist_ok=True)

	neuer_name = pfad.name
	if gefunden:
		teile = e.zerlege_namen(pfad.name, kuerzel_liste)
		if teile and not teile["kuerzel"]:
			neuer_name = e.zielname(teile["datum"], teile["lieferant"], teile["nummer"], gefunden["kuerzel"])
	ziel = e.freier_name(ziel_ordner, neuer_name)
	shutil.move(str(pfad), str(ziel))
	neu_rel = _rel(ordner, ziel)
	frappe.db.set_value(LOG, eintrag.name, {"pfad": neu_rel, "status": "Verschoben", "rechnung": rechnung,
		"hinweis": _("Bezahlt – verschoben aus {0}").format(rel)})
	bericht["verschoben"].append({"datei": rel, "nach": neu_rel, "rechnung": rechnung})


# ---------------------------------------------------------------- PDF ansehen

@frappe.whitelist()
def datei_anzeigen(pfad):
	"""Zeigt eine PDF aus dem Rechnungsordner im Browser (zum Prüfen vor dem Umbenennen)."""
	_darf()
	einst = _einstellungen()
	datei = _sicherer_pfad(_ordner(einst), pfad)
	frappe.local.response.filename = datei.name
	frappe.local.response.filecontent = datei.read_bytes()
	frappe.local.response.type = "pdf"
