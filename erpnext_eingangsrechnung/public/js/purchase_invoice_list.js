// Eingangsrechnung Import: beim Öffnen der Liste "Eingangsrechnung" den
// Rechnungsordner prüfen, Umbenennen anbieten und Entwürfe anlegen.
frappe.provide("erpnext_eingangsrechnung");

(function () {
	const einstellungen = (frappe.listview_settings["Purchase Invoice"] =
		frappe.listview_settings["Purchase Invoice"] || {});
	const altes_onload = einstellungen.onload;

	einstellungen.onload = function (listview) {
		if (altes_onload) altes_onload.apply(this, arguments);
		if (!frappe.model.can_create("Purchase Invoice")) return;

		listview.page.add_menu_item(__("Rechnungsordner prüfen"), () =>
			erpnext_eingangsrechnung.start(listview, true)
		);
		if (frappe.user.has_role(["System Manager", "Accounts Manager"])) {
			listview.page.add_menu_item(__("Einstellungen Rechnungsordner"), () =>
				frappe.set_route("Form", "Eingangsrechnung Import Einstellungen")
			);
		}
		erpnext_eingangsrechnung.start(listview, false);
	};
})();

erpnext_eingangsrechnung.start = function (listview, manuell) {
	if (erpnext_eingangsrechnung.laeuft) return;
	erpnext_eingangsrechnung.laeuft = true;
	frappe.call({
		method: "erpnext_eingangsrechnung.api.pruefen",
		args: { manuell: manuell ? 1 : 0 },
		freeze: manuell,
		freeze_message: __("Rechnungsordner wird geprüft …"),
		callback(r) {
			const d = r.message || {};
			if (!d.aktiv) {
				erpnext_eingangsrechnung.laeuft = false;
				if (manuell && d.fehler) frappe.msgprint(d.fehler);
				return;
			}
			if (d.fehler) {
				erpnext_eingangsrechnung.laeuft = false;
				if (manuell) frappe.msgprint(d.fehler);
				else frappe.show_alert({ message: d.fehler, indicator: "orange" }, 8);
				return;
			}
			if (d.umbenennen && d.umbenennen.length) {
				erpnext_eingangsrechnung.dialog(listview, d, manuell);
			} else {
				erpnext_eingangsrechnung.erfassen(listview, manuell);
			}
		},
		error() {
			erpnext_eingangsrechnung.laeuft = false;
		},
	});
};

erpnext_eingangsrechnung.dialog = function (listview, daten, manuell) {
	const esc = frappe.utils.escape_html;
	const zahler_optionen = ['<option value="">—</option>']
		.concat(
			(daten.zahler || []).map(
				(z) => `<option value="${esc(z.kuerzel)}">${esc(z.kuerzel)} – ${esc(z.bezeichnung)}</option>`
			)
		)
		.join("");

	const zeilen = daten.umbenennen
		.map((f, i) => {
			const link =
				"/api/method/erpnext_eingangsrechnung.api.datei_anzeigen?pfad=" + encodeURIComponent(f.pfad);
			const betrag = f.betrag != null ? format_currency(f.betrag, "EUR") : "";
			return `
			<tr data-i="${i}">
				<td style="max-width:260px">
					<a href="${link}" target="_blank" rel="noopener">${esc(f.datei)}</a>
					<div class="text-muted small">${esc(f.ordner)}</div>
					${f.grund ? `<div class="small" style="color:var(--red-600)">${esc(f.grund)}</div>` : ""}
					${betrag ? `<div class="text-muted small">${__("Betrag")}: ${betrag}</div>` : ""}
				</td>
				<td><select class="form-control input-xs eri-aktion">
					<option value="umbenennen">${__("Umbenennen")}</option>
					<option value="ueberspringen">${__("Später")}</option>
					<option value="ignorieren">${__("Keine Rechnung")}</option>
				</select></td>
				<td><input type="date" class="form-control input-xs eri-datum" value="${esc(f.datum)}"></td>
				<td><input type="text" class="form-control input-xs eri-lieferant" value="${esc(f.lieferant)}"></td>
				<td><input type="text" class="form-control input-xs eri-nummer" value="${esc(f.nummer)}"></td>
				<td><select class="form-control input-xs eri-kuerzel">${zahler_optionen}</select></td>
			</tr>`;
		})
		.join("");

	const dialog = new frappe.ui.Dialog({
		title: __("Neue Rechnungen im Ordner ({0})", [daten.umbenennen.length]),
		size: "extra-large",
		fields: [
			{
				fieldtype: "HTML",
				fieldname: "tabelle",
				options: `
				<p class="text-muted">${__(
					"Diese PDFs sind noch nicht nach dem Schema „JJJJ-MM-TT - Rechnung - Lieferant - Nummer“ benannt. Bitte prüfen und bestätigen. Danach werden Entwürfe angelegt (nicht gebucht)."
				)}</p>
				<div style="overflow-x:auto"><table class="table table-bordered table-sm">
					<thead><tr>
						<th>${__("Datei")}</th><th>${__("Aktion")}</th><th>${__("Rechnungsdatum")}</th>
						<th>${__("Lieferant")}</th><th>${__("Rechnungsnummer")}</th><th>${__("Privat bezahlt")}</th>
					</tr></thead>
					<tbody>${zeilen}</tbody>
				</table></div>`,
			},
		],
		primary_action_label: __("Ausführen"),
		primary_action() {
			const auftraege = [];
			dialog.$wrapper.find("tbody tr").each(function () {
				const tr = $(this);
				const f = daten.umbenennen[tr.data("i")];
				auftraege.push({
					pfad: f.pfad,
					aktion: tr.find(".eri-aktion").val(),
					datum: tr.find(".eri-datum").val(),
					lieferant: tr.find(".eri-lieferant").val(),
					nummer: tr.find(".eri-nummer").val(),
					kuerzel: tr.find(".eri-kuerzel").val(),
				});
			});
			frappe.call({
				method: "erpnext_eingangsrechnung.api.umbenennen",
				args: { auftraege: auftraege },
				freeze: true,
				freeze_message: __("Dateien werden umbenannt …"),
				callback(r) {
					const fehler = (r.message || []).filter((x) => !x.ok);
					dialog.hide();
					if (fehler.length) {
						frappe.msgprint({
							title: __("Nicht umbenannt"),
							indicator: "orange",
							message: fehler.map((x) => `${esc(x.pfad)}: ${esc(x.meldung)}`).join("<br>"),
						});
					}
					erpnext_eingangsrechnung.erfassen(listview, true);
				},
			});
		},
		secondary_action_label: __("Später"),
		secondary_action() {
			dialog.hide();
			// Ohne Umbenennen trotzdem die fertig benannten Dateien erfassen
			erpnext_eingangsrechnung.erfassen(listview, manuell);
		},
	});
	dialog.onhide = () => {
		erpnext_eingangsrechnung.laeuft = false;
	};
	dialog.show();
};

erpnext_eingangsrechnung.erfassen = function (listview, manuell) {
	erpnext_eingangsrechnung.laeuft = true;
	frappe.call({
		method: "erpnext_eingangsrechnung.api.erfassen",
		freeze: manuell,
		freeze_message: __("Entwürfe werden angelegt …"),
		callback(r) {
			erpnext_eingangsrechnung.laeuft = false;
			const b = r.message || {};
			const esc = frappe.utils.escape_html;
			const link = (name) => `<a href="/app/purchase-invoice/${encodeURIComponent(name)}">${esc(name)}</a>`;
			const teile = [];
			if ((b.angelegt || []).length) {
				teile.push(
					`<b>${__("Neue Entwürfe")}</b><br>` +
						b.angelegt
							.map(
								(x) =>
									`${link(x.rechnung)} – ${esc(x.lieferant)} – ${
										x.betrag != null ? format_currency(x.betrag, "EUR") : __("Betrag fehlt")
									}<div class="text-muted small">${esc(x.datei)}</div>`
							)
							.join("")
				);
			}
			if ((b.verschoben || []).length) {
				teile.push(
					`<b>${__("Bezahlt und verschoben")}</b><br>` +
						b.verschoben.map((x) => `${link(x.rechnung)} → ${esc(x.nach)}`).join("<br>")
				);
			}
			if ((b.fehler || []).length) {
				teile.push(
					`<b style="color:var(--red-600)">${__("Probleme")}</b><br>` +
						b.fehler.map((x) => `${esc(x.datei)}: ${esc(x.meldung)}`).join("<br>")
				);
			}
			if (teile.length) {
				frappe.msgprint({ title: __("Rechnungsordner"), message: teile.join("<hr>"), wide: true });
				listview.refresh();
			} else if (manuell) {
				frappe.show_alert({ message: __("Keine neuen Rechnungen im Ordner."), indicator: "green" });
			}
		},
		error() {
			erpnext_eingangsrechnung.laeuft = false;
		},
	});
};
