import os

import frappe
from frappe import _
from frappe.model.document import Document


class EingangsrechnungImportEinstellungen(Document):
	def validate(self):
		if self.ordner:
			self.ordner = self.ordner.strip()
			if not os.path.isabs(self.ordner):
				frappe.throw(_("Der Rechnungsordner muss ein absoluter Pfad sein, z. B. /mnt/eingangsrechnungen."))

		for feld in ("ordner_offen", "ziel_bank", "ziel_zahler"):
			wert = (self.get(feld) or "").strip().strip("/")
			if ".." in wert.split("/"):
				frappe.throw(_("{0}: '..' ist nicht erlaubt.").format(_(self.meta.get_label(feld))))
			self.set(feld, wert)

		gesehen = set()
		for zeile in self.zahler or []:
			zeile.kuerzel = (zeile.kuerzel or "").strip()
			if zeile.kuerzel.upper() in gesehen:
				frappe.throw(_("Kürzel {0} ist doppelt.").format(zeile.kuerzel))
			gesehen.add(zeile.kuerzel.upper())
			if self.firma and zeile.konto:
				firma = frappe.db.get_value("Account", zeile.konto, "company")
				if firma != self.firma:
					frappe.throw(_("Konto {0} gehört nicht zur Firma {1}.").format(zeile.konto, self.firma))

		if self.aktiv and self.ordner and not os.path.isdir(self.ordner):
			frappe.msgprint(
				_("Der Ordner {0} ist für ERPNext gerade nicht erreichbar. Ist er im Container eingebunden?").format(self.ordner),
				indicator="orange",
			)
