# -*- coding: utf-8 -*-
import base64
import io
import logging
import re
from collections import defaultdict

from odoo import models, fields, api, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

try:
    import openpyxl
except ImportError:  # pragma: no cover
    openpyxl = None

try:
    import xlrd  # lecture des vrais .xls (format OLE2)
except ImportError:  # pragma: no cover
    xlrd = None

# Colonnes du fichier source (0-indexées) :
#   0 N° Facture | 1 Date | 2 Société | 3 libellé | 4 Qte | 5 PU_HT | 6 ttc
COL_NUM, COL_DATE, COL_SOC, COL_LIB, COL_QTE, COL_PU, COL_TTC = range(7)

DOC_RE = re.compile(r"^(ES|AV)\d+")
CODE_RE = re.compile(r"^\s*(\d+)\s+(.*)$")

# Écart toléré (en devise) entre le total facture et la somme des lignes.
TOL = 0.5


def _fix_enc(value):
    """Corrige le mojibake résiduel de l'export (N� -> N°)."""
    if value is None:
        return ""
    return str(value).replace("�", "°").replace("�", "°").strip()


def _is_num(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _is_total_row(row):
    """Vrai si la ligne est une ligne de total/sous-total.

    Règle robuste et indépendante du format (.xls / .xlsx) : sur une ligne
    normale, la colonne Société contient toujours du TEXTE (nom du client) ;
    sur une ligne de total, elle contient un NOMBRE (le montant total) ou est
    vide. On ne peut pas se fier à la colonne Date : en .xls elle est TOUJOURS
    numérique (numéro de série Excel)."""
    soc = row[COL_SOC] if len(row) > COL_SOC else None
    lib = row[COL_LIB] if len(row) > COL_LIB else None
    if _is_num(soc):
        return True
    # Société vide + libellé vide + un montant en col Date -> total dégénéré.
    if (soc in (None, "") and (lib in (None, ""))
            and _is_num(row[COL_DATE] if len(row) > COL_DATE else None)):
        return True
    return False


class GsInvoiceImportWizard(models.TransientModel):
    _name = 'gs.invoice.import.wizard'
    _description = "Import factures/avoirs surveillance (.xls/.xlsx)"

    data_file = fields.Binary(string="Fichier", required=True)
    filename = fields.Char(string="Nom du fichier")

    journal_id = fields.Many2one(
        'account.journal', string="Journal de vente",
        domain="[('type', '=', 'sale')]", required=True,
        default=lambda self: self.env['account.journal'].search(
            [('type', '=', 'sale')], limit=1),
    )
    tax_id = fields.Many2one(
        'account.tax', string="TVA à appliquer",
        domain="[('type_tax_use', '=', 'sale')]",
        default=lambda self: self._default_tax(),
        help="Taxe de vente ajoutée à chaque ligne (montants source = HT). "
             "Laisser vide pour importer sans taxe.",
    )
    product_id = fields.Many2one(
        'product.product', string="Article de prestation",
        help="Article de service porté par les lignes de facture (pour "
             "déterminer les comptes comptables). Créé automatiquement si vide.",
    )
    create_missing_partners = fields.Boolean(
        string="Créer les clients manquants", default=True,
    )
    keep_detail_lines = fields.Boolean(
        string="Conserver le détail des lignes", default=True,
        help="Si coché : pour les factures dont le détail somme au total, "
             "les lignes détaillées sont créées. Sinon, une seule ligne = total.",
    )
    skip_zero = fields.Boolean(
        string="Ignorer les documents à 0", default=True,
    )
    batch_size = fields.Integer(
        string="Taille des lots", default=20,
        help="Nombre de factures créées entre deux sauvegardes (commit). "
             "Évite les transactions trop longues et les coupures de "
             "connexion sur les gros imports. L'import est relançable : les "
             "factures déjà créées (même n°) sont automatiquement ignorées.",
    )
    max_per_run = fields.Integer(
        string="Max. par exécution", default=200,
        help="Nombre maximum de factures créées à chaque clic sur "
             "« Importer ». Garde la requête sous le timeout serveur pour "
             "éviter « Connexion perdue ». S'il reste des factures, recliquez "
             "« Importer » : il reprend automatiquement (0 = pas de limite).",
    )

    state = fields.Selection(
        [('draft', 'Configuration'), ('preview', 'Aperçu')],
        default='draft',
    )
    preview_html = fields.Html(string="Rapport de contrôle", readonly=True)

    @api.model
    def _default_tax(self):
        tax = self.env['account.tax'].search([
            ('type_tax_use', '=', 'sale'),
            ('amount', '=', 20.0),
            ('amount_type', '=', 'percent'),
        ], limit=1)
        return tax.id if tax else False

    # ------------------------------------------------------------------ #
    #  Parsing
    # ------------------------------------------------------------------ #
    def _read_rows(self):
        """Lit la 1re feuille et renvoie (rows, datemode).

        Détecte le format par la signature binaire :
          - 'PK..'      -> .xlsx  (openpyxl), dates = datetime, datemode None
          - 0xD0CF11E0  -> .xls   (xlrd),    dates = série Excel, datemode 0/1
        """
        if not self.data_file:
            raise UserError(_("Veuillez sélectionner un fichier."))
        raw = base64.b64decode(self.data_file)

        if raw[:2] == b"PK":  # classeur OOXML (.xlsx)
            if openpyxl is None:
                raise UserError(_(
                    "openpyxl est requis pour lire les .xlsx "
                    "(pip install openpyxl)."))
            try:
                wb = openpyxl.load_workbook(
                    io.BytesIO(raw), read_only=True, data_only=True)
            except Exception as exc:
                raise UserError(_("Fichier .xlsx illisible : %s", exc))
            ws = wb.worksheets[0]
            rows = [list(r) for r in ws.iter_rows(values_only=True)]
            return rows[1:], None

        if raw[:4] == b"\xd0\xcf\x11\xe0":  # vrai .xls (OLE2)
            if xlrd is None:
                raise UserError(_(
                    "xlrd est requis pour lire les anciens .xls "
                    "(pip install xlrd). Sinon ré-enregistrez le fichier "
                    "au format .xlsx."))
            try:
                wb = xlrd.open_workbook(file_contents=raw)
            except Exception as exc:
                raise UserError(_("Fichier .xls illisible : %s", exc))
            sh = wb.sheet_by_index(0)
            rows = [[sh.cell_value(r, c) for c in range(sh.ncols)]
                    for r in range(sh.nrows)]
            return rows[1:], wb.datemode

        raise UserError(_(
            "Format de fichier non reconnu. Formats acceptés : .xlsx ou .xls."))

    def _cell_to_date(self, value, datemode):
        """Convertit une cellule Date en date Python (gère datetime openpyxl
        et numéro de série Excel xlrd)."""
        if value in (None, ""):
            return None
        if hasattr(value, "date"):  # datetime (openpyxl)
            return value.date()
        if _is_num(value) and xlrd is not None and datemode is not None:
            try:
                return xlrd.xldate.xldate_as_datetime(value, datemode).date()
            except Exception:  # pragma: no cover
                return None
        return None

    def _group_documents(self, rows):
        """Regroupe par N° de document, en ignorant les lignes de sous-total
        (col N° = nom de client, ne matchant pas ES.../AV...)."""
        blocks = defaultdict(list)
        order = []
        skipped = 0
        for r in rows:
            num = r[COL_NUM] if len(r) > COL_NUM else None
            if not num:
                continue
            if not DOC_RE.match(str(num)):
                skipped += 1
                continue
            if num not in blocks:
                order.append(num)
            blocks[num].append(r)
        return [(n, blocks[n]) for n in order], skipped

    def _parse_partner(self, soc):
        soc = _fix_enc(soc)
        m = CODE_RE.match(soc)
        if m:
            return m.group(1), m.group(2).strip()
        return "", soc

    def _build_document(self, num, block, datemode=None):
        """Reconstruit un document (facture/avoir) depuis son bloc de lignes."""
        inv_date = None
        partner_code = partner_name = ""
        for r in block:
            if _is_total_row(r):  # ligne de total : Société numérique
                continue
            if not inv_date:
                inv_date = self._cell_to_date(r[COL_DATE], datemode)
            if not partner_name and r[COL_SOC] and str(r[COL_SOC]) != "0":
                partner_code, partner_name = self._parse_partner(r[COL_SOC])

        # lignes chiffrées + textes descriptifs
        lines, texts = [], []
        for r in block:
            if _is_total_row(r):
                continue
            lib = _fix_enc(r[COL_LIB])
            qte, pu, ttc = r[COL_QTE], r[COL_PU], r[COL_TTC]
            if lib and _is_num(qte) and qte and _is_num(ttc) and ttc:
                lines.append({
                    "name": lib,
                    "qty": float(qte),
                    "pu": float(pu) if _is_num(pu) else 0.0,
                    "amount": float(ttc),
                })
            elif lib:
                texts.append(lib)

        # Le montant total de la ligne de total est en 2e colonne montant
        # (COL_SOC) : il vaut TOUJOURS la somme des lignes de détail.
        # ⚠️ NE PAS lire COL_DATE : cette 1re colonne contient parfois une
        # valeur partielle erronée (ex. ES260525 : COL_DATE=11847 alors que
        # le vrai total COL_SOC=23694 = 11847 JOUR + 11847 NUIT).
        total = None
        for r in block:
            if not _is_total_row(r):
                continue
            if _is_num(r[COL_SOC]):
                total = float(r[COL_SOC])
            elif _is_num(r[COL_DATE]):
                total = float(r[COL_DATE])
            break

        sum_lines = round(sum(l["amount"] for l in lines), 2)

        diag = ""
        if total is None:
            amount_ht = sum_lines
            use_detail = bool(lines)
            if not lines:
                diag = "SANS_LIGNE_NI_TOTAL"
        else:
            amount_ht = round(total, 2)
            if not lines:
                use_detail = False
                diag = "SANS_LIGNE_CHIFFREE"
            elif abs(sum_lines - total) <= TOL:
                use_detail = True
            else:
                use_detail = False
                diag = "ECART_TOTAL_LIGNES"

        if use_detail and self.keep_detail_lines:
            doc_lines = lines
        else:
            label = next((t for t in texts if "PRESTATION" in t.upper()),
                         texts[0] if texts else _("Prestation de surveillance"))
            doc_lines = [{"name": label, "qty": 1.0,
                          "pu": amount_ht, "amount": amount_ht}]

        return {
            "num": str(num),
            "move_type": "out_refund" if str(num).startswith("AV") else "out_invoice",
            "date": inv_date,
            "partner_code": partner_code,
            "partner_name": partner_name,
            "amount_ht": amount_ht,
            "lines": doc_lines,
            "diag": diag,
        }

    def _parse(self):
        rows, datemode = self._read_rows()
        docs_raw, skipped = self._group_documents(rows)
        docs = [self._build_document(n, b, datemode) for n, b in docs_raw]
        return docs, skipped

    # ------------------------------------------------------------------ #
    #  Aperçu
    # ------------------------------------------------------------------ #
    def action_preview(self):
        self.ensure_one()
        docs, skipped_subtotal = self._parse()

        n_fact = sum(1 for d in docs if d["move_type"] == "out_invoice")
        n_avoir = sum(1 for d in docs if d["move_type"] == "out_refund")
        zeros = [d for d in docs if d["amount_ht"] == 0.0]
        ecarts = [d for d in docs if d["diag"] == "ECART_TOTAL_LIGNES"]
        no_line = [d for d in docs if d["diag"] in
                   ("SANS_LIGNE_CHIFFREE", "SANS_LIGNE_NI_TOTAL")]
        importable = [d for d in docs
                      if not (self.skip_zero and d["amount_ht"] == 0.0)]
        total_ht = sum(d["amount_ht"] for d in importable)

        # rapprochement clients
        names = {d["partner_name"] for d in docs if d["partner_name"]}
        found, missing = [], []
        for name in sorted(names):
            if self._find_partner(name):
                found.append(name)
            else:
                missing.append(name)

        tax_rate = self.tax_id.amount if self.tax_id else 0.0
        total_ttc = total_ht * (1 + tax_rate / 100.0)

        rows_html = "".join(
            "<tr><td>%s</td><td>%s</td><td>%s</td><td style='text-align:right'>"
            "%s</td><td style='text-align:right'>%.2f</td><td>%s</td></tr>" % (
                d["num"],
                d["date"].strftime("%d/%m/%Y") if d["date"] else "—",
                (d["partner_name"] or "—")[:40],
                "Avoir" if d["move_type"] == "out_refund" else "Facture",
                d["amount_ht"],
                d["diag"] or "OK",
            )
            for d in docs[:60]
        )

        self.preview_html = _("""
            <div>
              <h3>Rapport de contrôle</h3>
              <ul>
                <li><b>%(ndocs)s documents</b> (%(nfact)s factures, %(navoir)s avoirs)</li>
                <li>Lignes de sous-total ignorées : %(skipped)s</li>
                <li>Clients distincts : <b>%(nclients)s</b>
                    — trouvés : %(nfound)s, à créer : <b>%(nmissing)s</b></li>
                <li>Documents à 0 : %(nzero)s %(zero_note)s</li>
                <li>Factures « total ≠ détail » (ramenées au total) : %(necart)s</li>
                <li>Sans ligne chiffrée : %(nnoline)s</li>
                <li><b>Total HT importable : %(ht)s</b></li>
                <li>Total TTC estimé (TVA %(rate)s%%) : %(ttc)s</li>
              </ul>
              <p><b>Clients à créer :</b> %(missing_list)s</p>
              <h4>Aperçu des 60 premiers documents</h4>
              <table class="table table-sm">
                <thead><tr><th>N°</th><th>Date</th><th>Client</th>
                <th>Type</th><th>HT</th><th>Contrôle</th></tr></thead>
                <tbody>%(rows)s</tbody>
              </table>
            </div>
        """) % {
            "ndocs": len(docs), "nfact": n_fact, "navoir": n_avoir,
            "skipped": skipped_subtotal, "nclients": len(names),
            "nfound": len(found), "nmissing": len(missing),
            "nzero": len(zeros),
            "zero_note": _("(ignorés)") if self.skip_zero else _("(importés)"),
            "necart": len(ecarts), "nnoline": len(no_line),
            "ht": "{:,.2f}".format(total_ht),
            "rate": tax_rate, "ttc": "{:,.2f}".format(total_ttc),
            "missing_list": ", ".join(missing[:40]) or _("aucun"),
            "rows": rows_html,
        }
        self.state = 'preview'
        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
        }

    def _reopen(self):
        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
        }

    # ------------------------------------------------------------------ #
    #  Import
    # ------------------------------------------------------------------ #
    def _find_partner(self, name):
        return self.env['res.partner'].search(
            [('name', '=ilike', name)], limit=1)

    def _get_or_create_partner(self, name, code):
        partner = self._find_partner(name)
        if partner:
            return partner
        if not self.create_missing_partners:
            raise UserError(_(
                "Client introuvable : « %s ». Activez « Créer les clients "
                "manquants » ou créez-le manuellement.", name))
        return self.env['res.partner'].create({
            'name': name,
            'ref': code or False,
            'customer_rank': 1,
            'company_type': 'company',
        })

    def _get_product(self):
        if self.product_id:
            return self.product_id
        product = self.env['product.product'].search(
            [('default_code', '=', 'PREST_SURV')], limit=1)
        if product:
            return product
        return self.env['product.product'].create({
            'name': _("Prestation de surveillance"),
            'default_code': 'PREST_SURV',
            'type': 'service',
            'sale_ok': True,
            'purchase_ok': False,
        })

    def action_import(self):
        self.ensure_one()
        docs, _skipped = self._parse()
        product = self._get_product()
        taxes = self.tax_id and [(6, 0, [self.tax_id.id])] or [(5, 0, 0)]
        Move = self.env['account.move']

        # Idempotence : on saute les documents dont le n° (ref) est déjà
        # présent comme facture/avoir. Permet de relancer l'import après une
        # coupure sans créer de doublons — il reprend là où il s'est arrêté.
        wanted_refs = [d["num"] for d in docs if d["num"]]
        already = set(Move.search([
            ('ref', 'in', wanted_refs),
            ('move_type', 'in', ('out_invoice', 'out_refund')),
        ]).mapped('ref'))

        # Documents éligibles (hors 0 / sans client) — sert au calcul du reste.
        eligible = [
            d for d in docs
            if not (self.skip_zero and d["amount_ht"] == 0.0)
            and d["partner_name"]
        ]
        remaining_before = sum(1 for d in eligible if d["num"] not in already)

        batch_size = self.batch_size or 20
        max_per_run = self.max_per_run or 0  # 0 = pas de limite
        partner_cache = {}
        created = Move
        n_created = n_skipped_exist = n_skipped_zero = n_failed = 0
        pending = 0  # documents créés depuis le dernier commit

        for d in docs:
            if self.skip_zero and d["amount_ht"] == 0.0:
                n_skipped_zero += 1
                continue
            if not d["partner_name"]:
                n_skipped_zero += 1
                continue
            if d["num"] in already:
                n_skipped_exist += 1
                continue

            # Plafond par exécution : on s'arrête pour rester sous le timeout.
            if max_per_run and n_created >= max_per_run:
                break

            # Chaque facture dans son savepoint : un échec isolé n'annule pas
            # le lot en cours ni ne gèle la transaction.
            try:
                with self.env.cr.savepoint():
                    key = d["partner_name"].lower()
                    partner = partner_cache.get(key)
                    if not partner:
                        partner = self._get_or_create_partner(
                            d["partner_name"], d["partner_code"])
                        partner_cache[key] = partner

                    line_cmds = [(0, 0, {
                        'product_id': product.id,
                        'name': l["name"] or product.name,
                        'quantity': l["qty"],
                        'price_unit': l["pu"],
                        'tax_ids': taxes,
                    }) for l in d["lines"]]

                    move = Move.create({
                        'move_type': d["move_type"],
                        'partner_id': partner.id,
                        'invoice_date': d["date"],
                        'ref': d["num"],
                        'journal_id': self.journal_id.id,
                        'invoice_line_ids': line_cmds,
                    })
                created |= move
                already.add(d["num"])
                n_created += 1
                pending += 1
            except Exception as exc:  # pragma: no cover
                n_failed += 1
                _logger.warning("Import facture %s échoué : %s", d["num"], exc)

            # Commit tous les `batch_size` documents pour éviter une
            # transaction trop longue (coupure de connexion / timeout HTTP).
            if pending >= batch_size:
                self.env.cr.commit()
                pending = 0

        # Commit final du dernier lot partiel.
        if pending:
            self.env.cr.commit()

        remaining = remaining_before - n_created
        _logger.info(
            "Import factures : %s créées, %s restantes, %s déjà présentes, "
            "%s ignorées (0/sans client), %s en échec.",
            n_created, remaining, n_skipped_exist, n_skipped_zero, n_failed)

        if remaining > 0:
            status = _(
                "<div class='alert alert-warning'><h3>Import partiel — "
                "%(c)s créée(s), <b>%(r)s restante(s)</b></h3>"
                "<p>➡️ Recliquez sur <b>« Importer »</b> pour continuer "
                "(reprise automatique, sans doublon).</p></div>"
            ) % {'c': n_created, 'r': remaining}
        else:
            status = _(
                "<div class='alert alert-success'><h3>Import terminé "
                "(brouillon)</h3></div>")

        self.preview_html = status + _(
            "<ul>"
            "<li><b>%(c)s</b> facture(s)/avoir(s) créé(s) cette exécution</li>"
            "<li>%(e)s déjà présent(s) — ignoré(s) (idempotence)</li>"
            "<li>%(z)s ignoré(s) (montant 0 ou sans client)</li>"
            "<li>%(f)s en échec (voir logs serveur)</li>"
            "</ul><p>Lots de %(b)s (sauvegarde intermédiaire), "
            "max %(m)s par exécution.</p>"
        ) % {'c': n_created, 'e': n_skipped_exist, 'z': n_skipped_zero,
             'f': n_failed, 'b': batch_size, 'm': max_per_run or _("illimité")}
        self.state = 'preview'

        # Tant qu'il reste des factures, on garde le wizard ouvert pour
        # permettre de recliquer « Importer ». Sinon on montre le résultat.
        if remaining > 0:
            return self._reopen()
        return {
            'type': 'ir.actions.act_window',
            'name': _("Factures importées (brouillon)"),
            'res_model': 'account.move',
            'view_mode': 'list,form',
            'domain': [('ref', 'in', [d["num"] for d in eligible])],
        }
