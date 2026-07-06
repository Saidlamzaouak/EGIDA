# -*- coding: utf-8 -*-
"""Import des couples Client / Site / Superviseur (fichier #4).

Crée (ou met à jour) un project.project par SITE, nommé « CLIENT — SITE »
— strictement le même format que le wizard d'import du planning
(_project_name) afin que l'import de planning matche ensuite ces projets.

Le superviseur (colonne CONTROLEUR : codes courts type HBIL, TARIK…) est
mappé vers l'utilisateur responsable du projet (project.project.user_id).
Les codes n'étant pas des noms d'utilisateur, un mapping code → utilisateur
est saisi dans le wizard entre l'analyse et l'import.
"""
import base64
import io
import re

from odoo import models, fields, api, _
from odoo.exceptions import UserError

try:
    import openpyxl
except ImportError:  # pragma: no cover
    openpyxl = None

# Colonnes du fichier : 0 CLIENT | 1 SITE / REFERENCE | 2 CONTROLEUR
COL_CLIENT, COL_SITE, COL_CTRL = range(3)


def _norm(text):
    return re.sub(r'\s+', ' ', (text or '')).strip()


def _project_name(client, site):
    """Identique à gs.planning.import.wizard._project_name."""
    client = _norm(client)
    site = _norm(site)
    if site and site.upper() != client.upper():
        return "%s — %s" % (client, site)
    return client


class GsProjectSiteImportWizard(models.TransientModel):
    _name = 'gs.project.site.import.wizard'
    _description = "Import Clients / Sites / Superviseurs"

    data_file = fields.Binary(string="Fichier (.xlsx)", required=True)
    filename = fields.Char()
    state = fields.Selection(
        [('draft', 'Fichier'), ('map', 'Mapping superviseurs'), ('done', 'Terminé')],
        default='draft',
    )
    create_missing_partners = fields.Boolean(
        string="Créer les clients manquants", default=True,
    )
    update_existing = fields.Boolean(
        string="Mettre à jour les projets existants", default=True,
        help="Si coché, met à jour le client et le responsable des projets "
             "déjà présents. Sinon, ne touche qu'aux projets nouvellement créés.",
    )
    create_missing_projects = fields.Boolean(
        string="Créer les projets absents", default=False,
        help="Décoché = mode « affectation seule » : on n'affecte le "
             "responsable (chef de projet) qu'aux projets DÉJÀ créés et on "
             "signale ceux introuvables, sans créer de nouveau projet. "
             "Cochez pour créer aussi les projets manquants.",
    )
    mapping_line_ids = fields.One2many(
        'gs.project.site.import.mapping', 'wizard_id',
        string="Superviseurs",
    )
    preview_html = fields.Html(readonly=True)

    # ------------------------------------------------------------------ #
    def _read_rows(self):
        if openpyxl is None:
            raise UserError(_("openpyxl requis pour lire le .xlsx."))
        if not self.data_file:
            raise UserError(_("Sélectionnez un fichier."))
        raw = base64.b64decode(self.data_file)
        if raw[:2] != b"PK":
            raise UserError(_("Format attendu : .xlsx (classeur Excel récent)."))
        wb = openpyxl.load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
        ws = wb.worksheets[0]
        rows = list(ws.iter_rows(values_only=True))
        return rows[1:]  # saute l'entête

    def _iter_sites(self, rows):
        """Génère (client, site, controleur) pour chaque ligne exploitable."""
        for r in rows:
            client = _norm(r[COL_CLIENT] if len(r) > COL_CLIENT else '')
            if not client:
                continue
            site = _norm(r[COL_SITE] if len(r) > COL_SITE else '')
            ctrl = _norm(r[COL_CTRL] if len(r) > COL_CTRL else '')
            yield client, site, ctrl

    def action_analyze(self):
        self.ensure_one()
        rows = self._read_rows()
        sites = list(self._iter_sites(rows))
        if not sites:
            raise UserError(_("Aucune ligne exploitable dans le fichier."))

        # codes superviseurs distincts + comptage
        from collections import Counter
        ctrl_counter = Counter(c for _, _, c in sites if c)
        self.mapping_line_ids.unlink()
        Users = self.env['res.users']
        lines = []
        for code, count in sorted(ctrl_counter.items()):
            user = Users.search([('name', '=ilike', code)], limit=1)
            lines.append((0, 0, {
                'controleur_code': code,
                'site_count': count,
                'user_id': user.id if user else False,
            }))
        self.mapping_line_ids = lines

        clients = {c for c, _, _ in sites}

        # Liaison avec les projets existants : combien de sites retrouvent
        # un projet déjà créé (par nom « CLIENT — SITE »), lesquels manquent.
        Project = self.env['project.project']
        matched, not_found = [], []
        seen_names = set()
        for client, site, _ctrl in sites:
            name = _project_name(client, site)
            if name in seen_names:
                continue
            seen_names.add(name)
            if Project.search([('name', '=ilike', name)], limit=1):
                matched.append(name)
            else:
                not_found.append(name)

        self.preview_html = _(
            "<div><h4>Analyse</h4><ul>"
            "<li>%(nsites)s ligne(s) site</li>"
            "<li>%(nclients)s client(s) distinct(s)</li>"
            "<li>%(nctrl)s superviseur(s) distinct(s) — mappez chacun "
            "à un utilisateur ci-dessous</li>"
            "</ul>"
            "<h4>Liaison avec les projets existants</h4><ul>"
            "<li>✅ %(nmatch)s projet(s) retrouvé(s) → recevront le responsable</li>"
            "<li>❓ %(nnf)s introuvable(s) %(nf_note)s</li>"
            "</ul>"
            "<p><b>Projets introuvables :</b> %(nf_list)s</p>"
            "<p>Superviseurs auto-reconnus : %(auto)s</p></div>"
        ) % {
            'nsites': len(sites), 'nclients': len(clients),
            'nctrl': len(ctrl_counter),
            'nmatch': len(matched), 'nnf': len(not_found),
            'nf_note': (_("(seront créés)") if self.create_missing_projects
                        else _("(ignorés — mode affectation seule)")),
            'nf_list': ", ".join(not_found[:40]) or _("aucun"),
            'auto': ", ".join(
                l[2]['controleur_code'] for l in lines if l[2]['user_id']
            ) or _("aucun (à mapper manuellement)"),
        }
        self.state = 'map'
        return self._reopen()

    def action_import(self):
        self.ensure_one()
        rows = self._read_rows()
        Project = self.env['project.project']
        Partner = self.env['res.partner']

        ctrl_map = {
            l.controleur_code.upper(): l.user_id
            for l in self.mapping_line_ids if l.user_id
        }
        partner_cache = {}
        created = updated = not_found = no_super = 0
        not_found_names = []
        created_projects = self.env['project.project']

        for client, site, ctrl in self._iter_sites(rows):
            name = _project_name(client, site)

            # client (partner société)
            pkey = client.upper()
            partner = partner_cache.get(pkey)
            if not partner:
                partner = Partner.search(
                    [('name', '=ilike', client), ('is_company', '=', True)],
                    limit=1)
                if not partner and self.create_missing_partners:
                    partner = Partner.create({
                        'name': client, 'is_company': True, 'customer_rank': 1,
                    })
                partner_cache[pkey] = partner

            user = ctrl_map.get(ctrl.upper()) if ctrl else False
            if ctrl and not user:
                no_super += 1

            project = Project.search([('name', '=ilike', name)], limit=1)
            if project:
                if self.update_existing:
                    vals = {}
                    if partner and project.partner_id != partner:
                        vals['partner_id'] = partner.id
                    if user and project.user_id != user:
                        vals['user_id'] = user.id
                    if vals:
                        project.write(vals)
                        updated += 1
            elif self.create_missing_projects:
                vals = {'name': name}
                if partner:
                    vals['partner_id'] = partner.id
                if user:
                    vals['user_id'] = user.id
                project = Project.create(vals)
                created_projects |= project
                created += 1
            else:
                # Mode « affectation seule » : projet introuvable, on signale.
                not_found += 1
                if len(not_found_names) < 40:
                    not_found_names.append(name)

        self.state = 'done'
        self.preview_html = _(
            "<div><h4>Import terminé</h4><ul>"
            "<li>✅ %(u)s projet(s) mis à jour (responsable affecté)</li>"
            "<li>%(c)s projet(s) créé(s)</li>"
            "<li>❓ %(nf)s site(s) sans projet correspondant %(nf_note)s</li>"
            "<li>⚠️ %(ns)s ligne(s) avec un superviseur non mappé "
            "(responsable non affecté)</li>"
            "</ul>%(nf_list)s</div>"
        ) % {
            'u': updated, 'c': created, 'nf': not_found, 'ns': no_super,
            'nf_note': _("(non créés)") if not self.create_missing_projects else '',
            'nf_list': (_("<p><b>Sites introuvables :</b> %s</p>")
                        % ", ".join(not_found_names)) if not_found_names else '',
        }

        if created_projects:
            return {
                'type': 'ir.actions.act_window',
                'name': _("Projets/sites importés"),
                'res_model': 'project.project',
                'view_mode': 'list,form',
                'domain': [('id', 'in', created_projects.ids)],
            }
        return self._reopen()

    def _reopen(self):
        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
        }


class GsProjectSiteImportMapping(models.TransientModel):
    _name = 'gs.project.site.import.mapping'
    _description = "Mapping superviseur → utilisateur"

    wizard_id = fields.Many2one(
        'gs.project.site.import.wizard', ondelete='cascade')
    controleur_code = fields.Char(string="Code superviseur", readonly=True)
    site_count = fields.Integer(string="Nb sites", readonly=True)
    user_id = fields.Many2one(
        'res.users', string="Utilisateur responsable",
        help="Utilisateur Odoo affecté comme responsable (user_id) des "
             "projets de ce superviseur.",
    )
