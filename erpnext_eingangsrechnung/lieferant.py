"""
Lieferantendaten aus einer Rechnung lesen – ohne Frappe, ohne Internet.

Liefert einen Vorschlag (Name, Adresse, Steuer-IDs, Bank, Kontakt, Zahlungsziel),
den der Benutzer vor dem Anlegen prüft und korrigiert.
"""

import re
import xml.etree.ElementTree as ET
from datetime import date

# Land (wie es auf Rechnungen steht, klein geschrieben) -> Ländername in ERPNext
LAENDER = {
	"deutschland": "Germany", "germany": "Germany", "de": "Germany", "d": "Germany",
	"österreich": "Austria", "oesterreich": "Austria", "austria": "Austria", "at": "Austria", "a": "Austria",
	"schweiz": "Switzerland", "switzerland": "Switzerland", "ch": "Switzerland",
	"niederlande": "Netherlands", "netherlands": "Netherlands", "holland": "Netherlands", "nl": "Netherlands",
	"luxemburg": "Luxembourg", "luxembourg": "Luxembourg", "lu": "Luxembourg", "l": "Luxembourg",
	"belgien": "Belgium", "belgium": "Belgium", "be": "Belgium",
	"frankreich": "France", "france": "France", "fr": "France",
	"italien": "Italy", "italy": "Italy", "it": "Italy",
	"spanien": "Spain", "spain": "Spain", "es": "Spain",
	"irland": "Ireland", "ireland": "Ireland", "ie": "Ireland",
	"polen": "Poland", "poland": "Poland", "pl": "Poland",
	"dänemark": "Denmark", "denmark": "Denmark", "dk": "Denmark",
	"schweden": "Sweden", "sweden": "Sweden", "se": "Sweden",
	"tschechien": "Czech Republic", "czech republic": "Czech Republic", "cz": "Czech Republic",
	"großbritannien": "United Kingdom", "vereinigtes königreich": "United Kingdom", "united kingdom": "United Kingdom",
	"uk": "United Kingdom", "gb": "United Kingdom", "england": "United Kingdom",
	"kanada": "Canada", "canada": "Canada", "ca": "Canada",
	"usa": "United States", "united states": "United States", "vereinigte staaten": "United States",
	"united states of america": "United States", "us": "United States",
}
EU_LAENDER = {
	"Austria", "Belgium", "Bulgaria", "Croatia", "Cyprus", "Czech Republic", "Denmark", "Estonia", "Finland",
	"France", "Germany", "Greece", "Hungary", "Ireland", "Italy", "Latvia", "Lithuania", "Luxembourg", "Malta",
	"Netherlands", "Poland", "Portugal", "Romania", "Slovakia", "Slovenia", "Spain", "Sweden",
}
LAND_AUS_CODE = {
	"DE": "Germany", "AT": "Austria", "CH": "Switzerland", "NL": "Netherlands", "LU": "Luxembourg",
	"BE": "Belgium", "FR": "France", "IT": "Italy", "ES": "Spain", "IE": "Ireland", "PL": "Poland",
	"DK": "Denmark", "SE": "Sweden", "CZ": "Czech Republic", "GB": "United Kingdom", "CA": "Canada",
	"US": "United States",
}
KANADA_PROVINZEN = {"ON", "QC", "BC", "AB", "MB", "SK", "NS", "NB", "NL", "PE", "YT", "NT", "NU"}

RE_PLZ_DE = re.compile(r"^(?:(D|A|CH|L)-)?(\d{4,5})\s+([A-Za-zÄÖÜäöüß][\wÄÖÜäöüß .'\-]*?)\s*,?$")
RE_PLZ_NL = re.compile(r"^(\d{4}\s?[A-Z]{2})(?:\s+([A-Za-zÄÖÜäöüß][\w .'\-]*))?\s*,?$")
RE_PLZ_CA = re.compile(r"^(?:([A-Z]{2})\s+)?([A-Z]\d[A-Z]\s?\d[A-Z]\d)\s*,?$")
RE_PLZ_US = re.compile(r"^(?:([A-Za-z .]+?),?\s+)?([A-Z]{2})\s+(\d{5}(?:-\d{4})?)\s*,?$")
RE_PLZ_ALLEIN = re.compile(r"^(\d{4,5})\s*,?$")
RE_STRASSE = re.compile(r"(?=.*\d)(?=.*[A-Za-zÄÖÜäöüß]{3,})")
RE_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
RE_WWW = re.compile(r"\bwww\.[\w-]+(?:\.[\w-]+)+", re.IGNORECASE)
RE_TELEFON = re.compile(r"(?:Tel(?:efon)?|Phone|Fon)\.?\s*:?\s*(\+?\d[\d /()\-]{5,}\d)", re.IGNORECASE)
RE_IBAN = re.compile(r"IBAN\s*:?\s*([A-Z]{2}\d{2}(?:\s?[A-Z0-9]){10,30})")
RE_BIC = re.compile(r"(?:BIC|SWIFT)(?:/SWIFT)?\s*:?\s*([A-Z]{6}[A-Z0-9]{2}(?:[A-Z0-9]{3})?)\b")
RE_KONTOINHABER = re.compile(r"(?:Kontoinhaber|Account holder)\s*:?\s*(.+)", re.IGNORECASE)
RE_UST = re.compile(r"\b(DE\s?\d{3}\s?\d{3}\s?\d{3}|ATU\d{8}|LU\d{8}|NL\d{9}B\d{2}|BE0?\d{9,10}|FR[A-Z0-9]{2}\d{9}|IE\d{7}[A-Z]{1,2}|CHE-?\d{3}\.?\d{3}\.?\d{3})\b")
RE_TAX_ID = re.compile(r"(?:Tax\s*ID|VAT\s*(?:ID|No\.?|Number|Reg\.?\s*No\.?)|Ust\.?[\s-]*Id\.?[\s-]*Nr\.?|USt-?ID|MwSt\.?-?\s*Nr\.?)\s*[:#.]?\s*([A-Z0-9][A-Z0-9 \-]{5,20}[A-Z0-9])", re.IGNORECASE)
RE_STEUERNR = re.compile(r"(?:Steuer-?\s?(?:nummer|nr\.?)|St(?:r)?\.?\s?-?Nr\.?)\s*:?\s*(\d[\d/ ]{6,}\d)", re.IGNORECASE)
RE_REGISTER = re.compile(r"\b(HRB|HRA|PR|GnR|VR)\s*:?\s*((?:[A-Z]{1,3}\s)?\d{3,7})\b")
RE_REGISTERGERICHT = re.compile(r"(?:Amtsgericht|Registergericht)\s*:?\s*([A-ZÄÖÜ][\wäöüß]+(?:\s(?:am|an der|im)\s[A-ZÄÖÜ][\wäöüß]+)?)")
WEB_IGNORIEREN = ("tcpdf.org", "adobe.com", "w3.org", "pdf")
GENERISCHE_MAIL_DOMAINS = {"gmail", "gmx", "web", "t-online", "outlook", "hotmail", "yahoo", "icloud", "posteo", "paypal"}
TRENNER = re.compile(r"\s+[•|–·]\s+|\s+-\s+|\s*\|\s*|,\s+")


def _iban_gueltig(iban):
	if not re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]{10,30}", iban):
		return False
	umgestellt = iban[4:] + iban[:4]
	zahl = "".join(str(int(c, 36)) for c in umgestellt)
	return int(zahl) % 97 == 1


def _iban_suchen(roh):
	"""IBAN aus einem Treffer mit Leerzeichen; kürzt, bis die Prüfsumme stimmt."""
	kompakt = re.sub(r"\s", "", roh)
	for laenge in range(min(len(kompakt), 34), 14, -1):
		kandidat = kompakt[:laenge]
		if _iban_gueltig(kandidat):
			return kandidat
	return None


def _land(text):
	t = (text or "").strip().strip(",.").lower()
	return LAENDER.get(t)


def _ist_eigen(text, eigene):
	t = (text or "").lower()
	return any(e and e in t for e in eigene)


def _schluessel(name):
	woerter = [w for w in re.findall(r"[\wÄÖÜäöüß]+", (name or "").lower()) if len(w) >= 3]
	return woerter[0] if woerter else ""


# ---------------------------------------------------------------- Adresse

def _zeile_pruefen(zeile, adr):
	"""Ordnet eine Zeile einem Adressfeld zu. Rückgabe: True = gehört zur Adresse."""
	z = zeile.strip().rstrip(",").strip()
	if not z:
		return False
	land = _land(z)
	if land:
		adr.setdefault("land", land)
		return True
	m = RE_PLZ_DE.match(z)
	if m and not adr.get("plz"):
		adr["plz"], adr["ort"] = m.group(2), m.group(3).strip()
		if m.group(1):
			adr.setdefault("land", {"D": "Germany", "A": "Austria", "CH": "Switzerland", "L": "Luxembourg"}[m.group(1)])
		return True
	m = RE_PLZ_NL.match(z)
	if m and not adr.get("plz"):
		adr["plz"] = m.group(1)
		if m.group(2):
			adr["ort"] = m.group(2).strip()
		adr.setdefault("land_vermutet", "Netherlands")
		return True
	m = RE_PLZ_CA.match(z)
	if m and not adr.get("plz"):
		adr["plz"] = m.group(2)
		if m.group(1) in KANADA_PROVINZEN:
			adr["bundesland"] = adr.get("bundesland") or m.group(1)
		adr["land_sicher"] = "Canada"
		return True
	m = RE_PLZ_US.match(z)
	if m and not adr.get("plz"):
		adr["plz"] = m.group(3)
		if m.group(1):
			adr.setdefault("ort", m.group(1).strip())
		adr["bundesland"] = adr.get("bundesland") or m.group(2)
		adr.setdefault("land_vermutet", "United States")
		return True
	m = RE_PLZ_ALLEIN.match(z)
	if m and not adr.get("plz") and adr.get("strasse"):
		adr["plz"] = m.group(1)
		return True
	if ":" in z or "@" in z:
		return False
	if RE_STRASSE.match(z) and not adr.get("strasse") and len(z) <= 60:
		adr["strasse"] = z
		return True
	if not re.search(r"\d", z) and len(z) <= 40:
		if adr.get("strasse") and not adr.get("ort"):
			adr["ort"] = z
			return True
		if adr.get("ort") and not adr.get("bundesland") and not adr.get("plz"):
			adr["bundesland"] = z
			return True
	return False


def _bewertung(adr):
	return 2 * bool(adr.get("strasse")) + 2 * bool(adr.get("plz")) + bool(adr.get("ort")) + bool(adr.get("land"))


def _adresse_aus_block(zeilen, schluessel, eigene):
	beste, beste_name = {}, None
	for i, zeile in enumerate(zeilen):
		z = zeile.strip()
		if not schluessel or not z.lower().startswith(schluessel) or _ist_eigen(z, eigene) or len(z) > 80:
			continue
		if TRENNER.search(z) and len(TRENNER.split(z)) > 2:
			continue  # das ist eine Absenderzeile, siehe unten
		adr = {}
		for folgezeile in zeilen[i + 1: i + 8]:
			if _ist_eigen(folgezeile, eigene):
				break
			hatte_plz = bool(adr.get("plz"))
			if not _zeile_pruefen(folgezeile, adr):
				if adr:
					break
				continue
			if hatte_plz and not _land(folgezeile) and not adr.get("ort"):
				continue
		if _bewertung(adr) > _bewertung(beste):
			beste, beste_name = adr, z.rstrip(",")
	return beste, beste_name


def _adresse_aus_absenderzeile(zeilen, schluessel, eigene):
	beste, beste_name = {}, None
	for zeile in zeilen:
		z = zeile.strip()
		pos = z.lower().find(schluessel) if schluessel else -1
		if pos < 0:
			continue
		teile = [t.strip() for t in TRENNER.split(z[pos:]) if t.strip()]
		if len(teile) < 3:
			continue
		if _ist_eigen(teile[0], eigene):
			continue
		adr = {}
		name = teile[0]
		for teil in teile[1:]:
			if RE_EMAIL.fullmatch(teil):
				continue
			if not _zeile_pruefen(teil, adr) and not adr.get("strasse") and not adr.get("plz"):
				name = f"{name}, {teil}"  # z. B. "Amazon EU S.à r.l., Niederlassung Deutschland"
		if _bewertung(adr) > _bewertung(beste):
			beste, beste_name = adr, name
	return beste, beste_name


# ---------------------------------------------------------------- E-Rechnung (XML)

def _xml_verkaeufer(xml_bytes):
	try:
		wurzel = ET.fromstring(xml_bytes)
	except ET.ParseError:
		return {}

	def lokal(tag):
		return tag.rsplit("}", 1)[-1]

	def erstes(knoten, name):
		for k in knoten.iter():
			if lokal(k.tag) == name and (k.text or "").strip():
				return k.text.strip()
		return ""

	def kind(knoten, name):
		for k in knoten.iter():
			if lokal(k.tag) == name:
				return k
		return None

	v = {}
	if lokal(wurzel.tag) == "CrossIndustryInvoice":
		seller = kind(wurzel, "SellerTradeParty")
		if seller is None:
			return {}
		v["name"] = erstes(seller, "Name")
		adresse = kind(seller, "PostalTradeAddress")
		if adresse is not None:
			v["strasse"] = erstes(adresse, "LineOne")
			v["adresszusatz"] = erstes(adresse, "LineTwo")
			v["plz"] = erstes(adresse, "PostcodeCode")
			v["ort"] = erstes(adresse, "CityName")
			v["land"] = LAND_AUS_CODE.get(erstes(adresse, "CountryID"), "")
		v["email"] = erstes(seller, "URIID")
		v["telefon"] = erstes(seller, "CompleteNumber")
		for reg in seller.iter():
			if lokal(reg.tag) == "ID" and reg.get("schemeID") == "VA" and reg.text:
				v["tax_id"] = reg.text.strip()
			if lokal(reg.tag) == "ID" and reg.get("schemeID") == "FC" and reg.text:
				v["steuernummer"] = reg.text.strip()
		v["iban"] = erstes(wurzel, "IBANID")
		v["bic"] = erstes(wurzel, "BICID")
		v["kontoinhaber"] = ""
		faellig = kind(wurzel, "DueDateDateTime")
		ausgestellt = kind(wurzel, "IssueDateTime")
		if faellig is not None and ausgestellt is not None:
			try:
				f, a = erstes(faellig, "DateTimeString"), erstes(ausgestellt, "DateTimeString")
				tage = (date(int(f[:4]), int(f[4:6]), int(f[6:8])) - date(int(a[:4]), int(a[4:6]), int(a[6:8]))).days
				v["zahlungsziel_tage"] = max(tage, 0)
			except (ValueError, IndexError):
				pass
	elif lokal(wurzel.tag) == "Invoice":
		partei = kind(wurzel, "AccountingSupplierParty")
		if partei is None:
			return {}
		v["name"] = erstes(partei, "RegistrationName") or erstes(partei, "Name")
		v["strasse"] = erstes(partei, "StreetName")
		v["adresszusatz"] = erstes(partei, "AdditionalStreetName")
		v["plz"] = erstes(partei, "PostalZone")
		v["ort"] = erstes(partei, "CityName")
		v["land"] = LAND_AUS_CODE.get(erstes(partei, "IdentificationCode"), "")
		v["email"] = erstes(partei, "ElectronicMail")
		v["telefon"] = erstes(partei, "Telephone")
		v["tax_id"] = erstes(partei, "CompanyID")
		konto = kind(wurzel, "PayeeFinancialAccount")
		v["iban"] = erstes(konto, "ID") if konto is not None else ""
	return {k: w for k, w in v.items() if w not in ("", None)}


# ---------------------------------------------------------------- Zahlungsziel

def _zahlungsziel(text, rechnungsdatum=None):
	t = text
	if re.search(r"Vorkasse|sofort fällig|zahlbar sofort|Amount\s+Paid|bereits bezahlt|wurde bezahlt|"
	             r"buchen den Betrag|abgebucht|Payment Type\s*\n?\s*PayPal|bezahlt mit|Zahlungsart:?\s*(PayPal|Kreditkarte)",
	             t, re.IGNORECASE):
		return 0
	m = re.search(r"Zahlungsziel\s*:?\s*(\d{1,3})\s*Tage", t, re.IGNORECASE) or re.search(
		r"(\d{1,3})\s*Tage\s*(?:netto|rein netto|ohne Abzug)", t, re.IGNORECASE) or re.search(
		r"(?:within|net)\s*(\d{1,3})\s*days", t, re.IGNORECASE)
	if m:
		return int(m.group(1))
	m = re.search(r"bis\s*(?:zum|spätestens|einschließlich|einschliesslich)?\s*(?:zum\s*)?(\d{1,2})\.(\d{1,2})\.(\d{4})", t)
	if m and rechnungsdatum:
		try:
			faellig = date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
			return max((faellig - rechnungsdatum).days, 0)
		except ValueError:
			pass
	return None


# ---------------------------------------------------------------- Gesamt

def lieferant_vorschlag(text, xml_bytes, name_hinweis, eigene, rechnungsdatum=None):
	"""Vorschlag für einen neuen Lieferanten aus Rechnungstext (+ optional E-Rechnungs-XML)."""
	eigene = [e for e in (eigene or []) if e]
	zeilen = text.splitlines()
	schluessel = _schluessel(name_hinweis)
	v = {"name": "", "strasse": "", "adresszusatz": "", "plz": "", "ort": "", "bundesland": "", "land": "",
		"tax_id": "", "steuernummer": "", "register": "", "email": "", "telefon": "", "website": "",
		"iban": "", "bic": "", "bank": "", "kontoinhaber": "", "zahlungsziel_tage": None, "quelle": "PDF-Text"}

	# 1. Adresse aus Block oder Absenderzeile
	adr_b, name_b = _adresse_aus_block(zeilen, schluessel, eigene)
	adr_a, name_a = _adresse_aus_absenderzeile(zeilen, schluessel, eigene)
	adr, name = (adr_b, name_b) if _bewertung(adr_b) >= _bewertung(adr_a) else (adr_a, name_a)
	v["name"] = (name or name_b or name_a or name_hinweis or "").strip(" ,")
	for feld in ("strasse", "plz", "ort", "bundesland"):
		v[feld] = adr.get(feld, "")
	v["land"] = adr.get("land_sicher") or adr.get("land") or adr.get("land_vermutet") or ""

	# 2. Steuer-IDs
	ust = [u.replace(" ", "") for u in RE_UST.findall(text)]
	ust = [u for u in ust if not _ist_eigen(u, eigene)]
	if ust:
		v["tax_id"] = ust[0]
	else:
		m = RE_TAX_ID.search(text)
		if m:
			v["tax_id"] = m.group(1).strip()
	m = RE_STEUERNR.search(text)
	if m:
		v["steuernummer"] = m.group(1).strip()
	m = RE_REGISTER.search(text)
	if m:
		gericht = RE_REGISTERGERICHT.search(text)
		v["register"] = f"{m.group(1)} {m.group(2)}" + (f" (Amtsgericht {gericht.group(1)})" if gericht else "")

	# 3. Kontakt
	mails = [e.rstrip(".") for e in RE_EMAIL.findall(text) if not _ist_eigen(e, eigene)]
	if mails:
		passend = [e for e in mails if schluessel and schluessel in e.lower()]
		v["email"] = (passend or mails)[0]
	m = RE_TELEFON.search(text)
	if m:
		v["telefon"] = re.sub(r"\s+", " ", m.group(1)).strip()
	webs = [w.lower() for w in RE_WWW.findall(text)
		if not _ist_eigen(w, eigene) and not any(g in w.lower() for g in WEB_IGNORIEREN)]
	mail_domain = v["email"].split("@", 1)[1].lower() if v["email"] else ""
	passend = [w for w in webs if (schluessel and schluessel in w) or (mail_domain and mail_domain in w)]
	if passend or webs:
		v["website"] = "https://" + (passend or webs)[0]
	elif v["email"]:
		domain = v["email"].split("@", 1)[1].lower()
		if domain.split(".")[0] not in GENERISCHE_MAIL_DOMAINS:
			v["website"] = "https://" + domain

	# 4. Bank
	for i, zeile in enumerate(zeilen):
		m = RE_IBAN.search(zeile)
		if not m:
			continue
		iban = _iban_suchen(m.group(1))
		if not iban:
			continue
		v["iban"] = iban
		mb = RE_BIC.search(zeile) or (RE_BIC.search(zeilen[i + 1]) if i + 1 < len(zeilen) else None)
		if mb:
			v["bic"] = mb.group(1)
		vorher = re.sub(r"(?i)bankverbindung\s*:?", "", zeile[: m.start()]).strip(" |:-")
		nachher = zeile[m.end():]
		if len(re.sub(r"[^A-Za-zÄÖÜäöüß]", "", vorher)) >= 3:
			v["bank"] = vorher
		elif "|" in nachher and not re.search(r"(?i)bank", vorher):
			teile = [t.strip() for t in nachher.split("|") if t.strip() and not RE_BIC.search(t)]
			if teile:
				v["bank"] = teile[0]
		if not v["bank"] and i > 0:
			davor = zeilen[i - 1].strip()
			if davor and not re.fullmatch(r"(?i)bankverbindung:?", davor) and ":" not in davor and len(davor) <= 50:
				v["bank"] = davor
		break
	m = RE_KONTOINHABER.search(text)
	if m:
		v["kontoinhaber"] = m.group(1).strip()

	# 5. Zahlungsziel
	v["zahlungsziel_tage"] = _zahlungsziel(text, rechnungsdatum)

	# 6. E-Rechnung hat Vorrang (exakte Daten)
	if xml_bytes:
		x = _xml_verkaeufer(xml_bytes)
		if x:
			v["quelle"] = "E-Rechnung (ZUGFeRD/XRechnung)"
			for k, w in x.items():
				if w not in ("", None):
					v[k] = w

	# 7. Land aus USt-IdNr., falls nichts gefunden
	if not v["land"] and v["tax_id"][:2] in LAND_AUS_CODE:
		v["land"] = LAND_AUS_CODE[v["tax_id"][:2]]
	return v


def ist_eu(land):
	return land in EU_LAENDER
