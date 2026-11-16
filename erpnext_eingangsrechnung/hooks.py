app_name = "erpnext_eingangsrechnung"
app_title = "Eingangsrechnungen aus Ordner"
app_publisher = "AffiKSys"
app_description = "Eingangsrechnungen aus einem Ordner als Entwürfe in ERPNext anlegen"
app_email = "m.heide@affiksys.com"
app_license = "mit"
required_apps = ["erpnext"]

# Beim Öffnen der Liste "Eingangsrechnung" den Rechnungsordner prüfen
doctype_list_js = {"Purchase Invoice": "public/js/purchase_invoice_list.js"}

# Optional: stündlich neue (bereits umbenannte) Rechnungen erfassen
scheduler_events = {
    "hourly": ["erpnext_eingangsrechnung.api.stuendlich"],
}
