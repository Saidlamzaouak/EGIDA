# -*- coding: utf-8 -*-
import base64
import calendar
import logging
import re
from datetime import date

from odoo import models, fields, api, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

try:
    import xlrd
except ImportError:  # pragma: no cover
    xlrd = None

# Indices de colonnes dans la feuille source (constants entre PROPRETE/SURVEILLANCE).
COL_NOM = 0
COL_PRENOM = 1
COL_EMPL = 2
COL_REPOT = 4
COL_FIRST_DAY = 6   # J1
COL_LAST_DAY = 36   # J31 (6 + 31 - 1)

MONTH_NAMES_FR = {
    'janvier': 1, 'fevrier': 2, 'février': 2, 'mars': 3, 'avril': 4,
    'mai': 5, 'juin': 6, 'juillet': 7, 'aout': 8, 'août': 8,
    'septembre': 9, 'octobre': 10, 'novembre': 11, 'decembre': 12,
    'décembre': 12,
}

# Au-delà de 24, une cellule "jour" ne peut pas être des heures réelles d'une
# journée → c'est un cumul / forfait mensuel posé sur un seul jour.
MAX_DAILY_HOURS = 24.0

# Codes jour de repos rencontrés dans « Repot » (surveillance) et parfois
# collés dans la colonne EMPL (ex. « CE/DIMANCHE »).
WEEKDAY_CODES = {
    'L': 'Lundi', 'LUN': 'Lundi', 'LUNDI': 'Lundi',
    'MA': 'Mardi', 'MAR': 'Mardi', 'MARDI': 'Mardi',
    'ME': 'Mercredi', 'MER': 'Mercredi', 'MERCREDI': 'Mercredi',
    'J': 'Jeudi', 'JEU': 'Jeudi', 'JEUDI': 'Jeudi',
    'V': 'Vendredi', 'VEN': 'Vendredi', 'VENDREDI': 'Vendredi',
    'S': 'Samedi', 'SAM': 'Samedi', 'SAMEDI': 'Samedi',
    'D': 'Dimanche', 'DIM': 'Dimanche', 'DIMANCHE': 'Dimanche',
}

# Jetons « chef d'équipe » → rôle Manager (matching par jeton exact, pas
# sous-chaîne, sinon « surveillanCE », « rempla­CEnt », « acCEil » matcheraient).
MANAGER_TOKENS = {'CE', 'CHEF', 'MANAGER', 'SUPERVISEUR', 'SUP'}


class GsPlanningImportWizard(models.TransientModel):
    _name = 'gs.planning.import.wizard'
    _description = "Import du planning détaillé mensuel (.xls)"

    data_file = fields.Binary(string="Fichier .xls", required=True)
    filename = fields.Char(string="Nom du fichier")
    service_type = fields.Selection(
        selection=[
            ('proprete', "Propreté"),
            ('surveillance', "Surveillance"),
        ],
        string="Service", required=True,
        help="Déduit du nom de fichier ; déterminé le rôle par défaut des agents.",
    )
    month = fields.Integer(string="Mois", required=True)
    year = fields.Integer(string="Année", required=True)

    create_missing_employees = fields.Boolean(
        string="Créer les employés manquants", default=True,
    )
    create_missing_projects = fields.Boolean(
        string="Créer les projets manquants", default=True,
    )
    create_partners = fields.Boolean(
        string="Créer les clients (res.partner) manquants", default=True,
    )
    create_planning_lines = fields.Boolean(
        string="Remplir « Planning Resources » depuis l'équipe", default=True,
        help="Crée une ligne d'affectation planning (employé + rôle + shift) "
             "pour chaque agent de l'équipe du projet. L'exclusivité "
             "multi-projets est désactivée à l'import (agents multi-sites).",
    )
    replace_existing = fields.Boolean(
        string="Remplacer les heures déjà importées pour ce mois/service",
        default=True,
        help="Supprime les heures réalisées déjà importées pour le même mois "
             "et le même service avant de réimporter (ré-import idempotent).",
    )

    state = fields.Selection(
        selection=[('choose', 'choose'), ('done', 'done')],
        default='choose',
    )
    report = fields.Html(string="Rapport", readonly=True)

    # ------------------------------------------------------------------ #
    #  Pré-remplissage depuis le nom de fichier
    # ------------------------------------------------------------------ #
    @api.onchange('filename')
    def _onchange_filename(self):
        if not self.filename:
            return
        fname = self.filename
        upper = fname.upper()
        if 'SURVEILLANCE' in upper:
            self.service_type = 'surveillance'
        elif 'PROPRETE' in upper or 'PROPRETÉ' in upper:
            self.service_type = 'proprete'
        # Période : motif MMYYYY (ex. 042026)
        m = re.search(r'(?<!\d)(0[1-9]|1[0-2])(20\d{2})(?!\d)', fname)
        if m:
            self.month = int(m.group(1))
            self.year = int(m.group(2))
        else:
            low = fname.lower()
            for name, num in MONTH_NAMES_FR.items():
                if name in low:
                    self.month = num
                    break
            y = re.search(r'(20\d{2})', fname)
            if y:
                self.year = int(y.group(1))

    # ------------------------------------------------------------------ #
    #  Parsing du fichier .xls
    # ------------------------------------------------------------------ #
    def _read_workbook(self):
        if xlrd is None:
            raise UserError(_(
                "La librairie Python « xlrd » est requise pour lire les "
                "fichiers .xls. Installez-la dans l'environnement Odoo :\n"
                "    pip install xlrd"
            ))
        if not self.data_file:
            raise UserError(_("Veuillez sélectionner un fichier .xls."))
        try:
            content = base64.b64decode(self.data_file)
            return xlrd.open_workbook(file_contents=content)
        except Exception as exc:
            raise UserError(_(
                "Impossible de lire le fichier (.xls binaire attendu) : %s", exc
            ))

    @staticmethod
    def _cell_str(sheet, r, c):
        try:
            v = sheet.cell_value(r, c)
        except IndexError:
            return ''
        if isinstance(v, float):
            # entiers stockés en float (codes ICE, etc.)
            return str(int(v)) if v.is_integer() else str(v)
        return str(v).strip()

    @staticmethod
    def _cell_float(sheet, r, c):
        try:
            v = sheet.cell_value(r, c)
        except IndexError:
            return 0.0
        if isinstance(v, (int, float)):
            return float(v)
        try:
            return float(str(v).replace(',', '.').strip())
        except (ValueError, TypeError):
            return 0.0

    def _parse_blocks(self, sheet):
        """Découpe la feuille en blocs client → liste d'employés + heures/jour."""
        blocks = []
        nrows = sheet.nrows
        r = 0
        while r < nrows:
            if (self._cell_str(sheet, r, COL_NOM) == 'NOM'
                    and self._cell_str(sheet, r, COL_PRENOM) == 'PRENOM'
                    and self._cell_str(sheet, r, COL_EMPL) == 'EMPL'):
                # En-tête client = ligne juste au-dessus.
                client = self._cell_str(sheet, r - 1, COL_NOM) if r > 0 else ''
                site = self._cell_str(sheet, r - 1, COL_PRENOM) if r > 0 else ''
                code = self._cell_str(sheet, r - 1, COL_EMPL) if r > 0 else ''
                employees = []
                er = r + 1
                while er < nrows:
                    nom = self._cell_str(sheet, er, COL_NOM)
                    prenom = self._cell_str(sheet, er, COL_PRENOM)
                    empl = self._cell_str(sheet, er, COL_EMPL)
                    # Fin de bloc.
                    if nom == 'Total heures':
                        break
                    if nom == 'NOM' and prenom == 'PRENOM':
                        er -= 1  # laissera la boucle externe redétecter l'en-tête
                        break
                    if not nom and not prenom:
                        break
                    days = {}
                    for day in range(1, 32):
                        col = COL_FIRST_DAY + (day - 1)
                        if col > COL_LAST_DAY:
                            break
                        h = self._cell_float(sheet, er, col)
                        if h:
                            days[day] = h
                    employees.append({
                        'nom': nom,
                        'prenom': prenom,
                        'empl': empl,
                        'note': self._cell_str(sheet, er, COL_REPOT),
                        'days': days,
                    })
                    er += 1
                blocks.append({
                    'client': client,
                    'site': site,
                    'code': code,
                    'employees': employees,
                })
                r = er + 1
            else:
                r += 1
        return blocks

    # ------------------------------------------------------------------ #
    #  Normalisation / résolution
    # ------------------------------------------------------------------ #
    @staticmethod
    def _norm(text):
        return re.sub(r'\s+', ' ', (text or '')).strip()

    @classmethod
    def _employee_name(cls, emp):
        """Construit le nom complet : « Prénom Nom » en casse Titre."""
        full = "%s %s" % (cls._norm(emp['prenom']), cls._norm(emp['nom']))
        return cls._norm(full).title()

    @classmethod
    def _project_name(cls, block):
        client = cls._norm(block['client'])
        site = cls._norm(block['site'])
        # Le libellé site est souvent une vraie zone (« PARTIES COMMUNES »),
        # parfois une référence devis. On le garde tel quel pour distinguer
        # les sites d'un même client.
        if site and cls._norm(site).upper() != client.upper():
            return "%s — %s" % (client, site)
        return client

    @staticmethod
    def _tokenize(text):
        return [t for t in re.split(r'[\s/,;+.\-]+', (text or '').upper()) if t]

    def _role_for(self, empl_label):
        """Mappe la colonne EMPL vers un planning.role existant.

        Détection « chef » par JETON exact (et non sous-chaîne) pour ne pas
        confondre « CE » avec « surveillanCE » / « remplaCEnt » / « acCEil »."""
        ref = self.env.ref
        role_security = ref('gs_project_planning.planning_role_security', raise_if_not_found=False)
        role_cleaning = ref('gs_project_planning.planning_role_cleaning', raise_if_not_found=False)
        role_manager = ref('gs_project_planning.planning_role_manager', raise_if_not_found=False)
        default = role_security if self.service_type == 'surveillance' else role_cleaning
        if set(self._tokenize(empl_label)) & MANAGER_TOKENS:
            return role_manager or default
        return default

    def _shift_for(self, empl_label):
        """Shift par défaut pour une ligne planning, selon rôle/service."""
        ref = self.env.ref
        if set(self._tokenize(empl_label)) & MANAGER_TOKENS:
            shift = ref('gs_project_planning.shift_manager_day', raise_if_not_found=False)
            if shift:
                return shift
        if self.service_type == 'surveillance':
            return ref('gs_project_planning.shift_security_morning', raise_if_not_found=False)
        return ref('gs_project_planning.shift_cleaning_morning', raise_if_not_found=False)

    @classmethod
    def _extract_weekdays(cls, text):
        """Retourne (jours_de_repos_joinés, jetons_restants_non_jours)."""
        days, rest = [], []
        for t in cls._tokenize(text):
            name = WEEKDAY_CODES.get(t)
            if name:
                if name not in days:
                    days.append(name)
            else:
                rest.append(t)
        return ", ".join(days), rest

    @staticmethod
    def _classify_value(value):
        """Classe une valeur de cellule jour → type d'unité.

        - == 1            → 'day'   (travail à la journée / forfait journalier)
        - > 24            → 'month' (forfait mensuel posé sur un seul jour)
        - sinon (>0)      → 'hour'  (le taux = nombre d'heures)
        """
        if value == 1:
            return 'day'
        if value > MAX_DAILY_HOURS:
            return 'month'
        return 'hour'

    @classmethod
    def _parse_repot(cls, repot):
        """Sépare la colonne « Repot » en (jour_de_repos, note_horaire).

        Si TOUS les jetons correspondent à des codes jour (L, MA, S/D…), c'est
        un jour de repos. Sinon c'est une note horaire/forfait brute.
        """
        raw = cls._norm(repot)
        if not raw:
            return '', ''
        days, rest = cls._extract_weekdays(raw)
        if days and not rest:
            return days, ''
        return '', raw

    @staticmethod
    def _pay_type_for(unit_types):
        """Mode de paie dominant d'une affectation à partir des types jour."""
        if 'month' in unit_types:
            return 'month'
        if unit_types and all(u == 'day' for u in unit_types):
            return 'day'
        if 'day' in unit_types and 'hour' not in unit_types:
            return 'day'
        return 'hour'

    # ------------------------------------------------------------------ #
    #  Boutons
    # ------------------------------------------------------------------ #
    def action_analyze(self):
        self.ensure_one()
        stats = self._process(commit=False)
        self.report = self._render_report(stats, committed=False)
        return self._reopen()

    def action_import(self):
        self.ensure_one()
        stats = self._process(commit=True)
        self.report = self._render_report(stats, committed=True)
        self.state = 'done'
        return self._reopen()

    def _reopen(self):
        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
        }

    # ------------------------------------------------------------------ #
    #  Cœur : matching + création
    # ------------------------------------------------------------------ #
    def _process(self, commit):
        self.ensure_one()
        if not (1 <= (self.month or 0) <= 12):
            raise UserError(_("Mois invalide (1-12)."))
        if not (2000 <= (self.year or 0) <= 2100):
            raise UserError(_("Année invalide."))

        wb = self._read_workbook()
        sheet = wb.sheet_by_index(0)
        blocks = self._parse_blocks(sheet)

        company = self.env.company
        period_first = date(self.year, self.month, 1)
        days_in_month = calendar.monthrange(self.year, self.month)[1]

        stats = {
            'blocks': len(blocks),
            'projects_created': 0, 'projects_matched': 0,
            'partners_created': 0,
            'employees_created': 0, 'employees_matched': 0,
            'assignments': 0, 'planning_lines_created': 0,
            'actual_lines': 0, 'actual_hours': 0.0,
            'daily_paid_cells': 0, 'month_forfait_cells': 0,
            'rest_days_detected': 0,
            'actuals_removed': 0,
            'warnings': [],
        }

        # Import administrateur (menu réservé aux managers) : on élève les
        # droits pour pouvoir créer partenaires / employés / projets sans
        # exiger que l'opérateur ait tous les groupes RH / Contacts.
        Project = self.env['project.project'].sudo()
        Partner = self.env['res.partner'].sudo()
        Employee = self.env['hr.employee'].sudo()
        Assignment = self.env['gs.planning.assignment'].sudo()
        Actual = self.env['gs.planning.actual'].sudo()

        # Remplacement des données du même mois/service (ré-import idempotent).
        if commit and self.replace_existing:
            domain = [
                ('period_month', '=', period_first),
                ('service_type', '=', self.service_type),
                ('company_id', '=', company.id),
            ]
            old = Actual.search(domain)
            stats['actuals_removed'] = len(old)
            old.unlink()
            Assignment.search(domain).unlink()

        # Caches pour ne pas re-créer / re-chercher en boucle.
        partner_cache = {}     # norm client name -> partner (or sentinel)
        project_cache = {}     # project name -> project
        employee_cache = {}    # employee name -> employee
        created_emp_names = set()
        created_proj_names = set()

        def resolve_partner(client_name):
            key = self._norm(client_name).upper()
            if not key:
                return False
            if key in partner_cache:
                return partner_cache[key]
            partner = Partner.search(
                [('name', '=ilike', self._norm(client_name)),
                 ('is_company', '=', True)], limit=1)
            if not partner and self.create_partners:
                if commit:
                    partner = Partner.create({
                        'name': self._norm(client_name),
                        'is_company': True,
                    })
                stats['partners_created'] += 1
            partner_cache[key] = partner
            return partner

        def resolve_project(block):
            name = self._project_name(block)
            key = name.upper()
            if key in project_cache:
                return project_cache[key]
            project = Project.search(
                [('name', '=ilike', name),
                 ('company_id', 'in', [company.id, False])], limit=1)
            if project:
                if name.upper() not in created_proj_names:
                    stats['projects_matched'] += 1
            else:
                if not self.create_missing_projects:
                    stats['warnings'].append(_("Projet absent (non créé) : %s", name))
                    project_cache[key] = False
                    return False
                partner = resolve_partner(block['client'])
                vals = {
                    'name': name,
                    'company_id': company.id,
                    'date_start': period_first,
                    'date': date(self.year, self.month, days_in_month),
                }
                if partner:
                    vals['partner_id'] = partner.id
                if commit:
                    project = Project.create(vals)
                stats['projects_created'] += 1
                created_proj_names.add(name.upper())
            project_cache[key] = project
            return project

        def resolve_employee(emp):
            name = self._employee_name(emp)
            if not name:
                return False
            key = name.upper()
            if key in employee_cache:
                return employee_cache[key]
            employee = Employee.search([('name', '=ilike', name)], limit=1)
            if employee:
                stats['employees_matched'] += 1
            else:
                if not self.create_missing_employees:
                    stats['warnings'].append(_("Employé absent (non créé) : %s", name))
                    employee_cache[key] = False
                    return False
                role = self._role_for(emp['empl'])
                vals = {'name': name, 'company_id': company.id}
                if role:
                    vals['default_planning_role_id'] = role.id
                if commit:
                    employee = Employee.create(vals)
                stats['employees_created'] += 1
                created_emp_names.add(key)
            employee_cache[key] = employee
            return employee

        # Deux niveaux :
        #  - assignments : 1 entrée par (projet, employé) pour le mois (header)
        #  - actual_agg  : 1 entrée par (projet, employé, jour) (détail)
        # En dry-run on n'a pas d'ids → on indexe sur les noms normalisés.
        assignments = {}
        actual_agg = {}
        project_lines = {}   # project.id -> {employee.id: empl_label}
        for block in blocks:
            if not self._norm(block['client']):
                continue
            project = resolve_project(block)
            pkey = project.id if (commit and project) else self._project_name(block).upper()
            team_ids = set()
            for emp in block['employees']:
                employee = resolve_employee(emp)
                ekey = (employee.id if (commit and employee)
                        else self._employee_name(emp).upper())
                if not ekey:
                    continue
                role = self._role_for(emp['empl'])
                rest_day, sched_note = self._parse_repot(emp['note'])
                # Repos parfois collé dans EMPL (ex. « CE/DIMANCHE »).
                if not rest_day:
                    empl_days, _rest = self._extract_weekdays(emp['empl'])
                    if empl_days:
                        rest_day = empl_days
                akey = (pkey, ekey)

                asg = assignments.get(akey)
                if asg is None:
                    asg = {
                        'project_id': project.id if (commit and project) else False,
                        'employee_id': employee.id if (commit and employee) else False,
                        'role_id': role.id if role else False,
                        'function_label': emp['empl'],
                        'period_month': period_first,
                        'service_type': self.service_type,
                        'rest_day': rest_day,
                        'schedule_note': sched_note,
                        'company_id': company.id,
                        '_units': [],
                    }
                    assignments[akey] = asg
                else:
                    if rest_day and not asg['rest_day']:
                        asg['rest_day'] = rest_day
                    if sched_note and not asg['schedule_note']:
                        asg['schedule_note'] = sched_note

                if commit and employee:
                    team_ids.add(employee.id)
                    if project:
                        project_lines.setdefault(project.id, {}).setdefault(
                            employee.id, emp['empl'])

                for day, value in emp['days'].items():
                    if day > days_in_month:
                        continue
                    utype = self._classify_value(value)
                    asg['_units'].append(utype)
                    if utype == 'hour':
                        stats['actual_hours'] += value
                    elif utype == 'day':
                        stats['daily_paid_cells'] += 1
                    else:
                        stats['month_forfait_cells'] += 1

                    dkey = (pkey, ekey, day)
                    dv = actual_agg.get(dkey)
                    if dv:
                        dv['quantity'] += value
                        dv['unit_type'] = self._classify_value(dv['quantity'])
                    else:
                        actual_agg[dkey] = {
                            '_akey': akey,
                            'project_id': project.id if (commit and project) else False,
                            'employee_id': employee.id if (commit and employee) else False,
                            'role_id': role.id if role else False,
                            'function_label': emp['empl'],
                            'date': date(self.year, self.month, day),
                            'period_month': period_first,
                            'quantity': value,
                            'unit_type': utype,
                            'note': emp['note'],
                            'service_type': self.service_type,
                            'company_id': company.id,
                        }
            # Équipe du projet : ajoute les agents du bloc.
            if commit and project and team_ids:
                project.write({
                    'allowed_employee_ids': [(4, eid) for eid in team_ids],
                })

        # Finalisation : mode de paie + comptages.
        for asg in assignments.values():
            asg['pay_type'] = self._pay_type_for(asg.pop('_units'))
            if asg['rest_day']:
                stats['rest_days_detected'] += 1
        stats['assignments'] = len(assignments)
        stats['actual_lines'] = len(actual_agg)

        if commit:
            # 1) Créer les affectations (header), récupérer leurs ids.
            akey_to_id = {}
            order, create_vals = [], []
            for akey, asg in assignments.items():
                if not (asg['project_id'] and asg['employee_id']):
                    continue
                order.append(akey)
                create_vals.append(asg)
            if create_vals:
                recs = Assignment.create(create_vals)
                for akey, rec in zip(order, recs):
                    akey_to_id[akey] = rec.id

            # 2) Créer le détail jour, rattaché à son affectation.
            day_vals = []
            for dv in actual_agg.values():
                akey = dv.pop('_akey')
                if not (dv['project_id'] and dv['employee_id']):
                    continue
                dv['assignment_id'] = akey_to_id.get(akey)
                day_vals.append(dv)
            if day_vals:
                Actual.create(day_vals)

            # 3) Remplir « Planning Resources » (gs.project.planning.line)
            #    pour tous les projets, sans doublon. Exclusivité multi-projets
            #    désactivée (agents multi-sites).
            if self.create_planning_lines:
                Line = self.env['gs.project.planning.line'].sudo().with_context(
                    skip_planning_exclusivity=True)
                line_vals = []
                for pid, emp_map in project_lines.items():
                    existing = set(Line.search(
                        [('project_id', '=', pid)]).employee_id.ids)
                    for eid, empl in emp_map.items():
                        if eid in existing:
                            continue
                        role = self._role_for(empl)
                        shift = self._shift_for(empl)
                        if not (role and shift):
                            stats['warnings'].append(_(
                                "Ligne planning ignorée (rôle/shift introuvable) "
                                "pour l'employé #%s.", eid))
                            continue
                        line_vals.append({
                            'project_id': pid,
                            'employee_id': eid,
                            'role_id': role.id,
                            'shift_id': shift.id,
                        })
                if line_vals:
                    Line.create(line_vals)
                stats['planning_lines_created'] = len(line_vals)
        elif self.create_planning_lines:
            # Dry-run : approximation = 1 ligne par affectation (employé/projet).
            stats['planning_lines_created'] = stats['assignments']

        return stats

    # ------------------------------------------------------------------ #
    #  Rendu du rapport HTML
    # ------------------------------------------------------------------ #
    def _render_report(self, stats, committed):
        title = (_("Import terminé") if committed
                 else _("Analyse (aucune écriture)"))
        css_alert = 'alert-success' if committed else 'alert-info'
        verb_created = _("créés") if committed else _("à créer")
        verb_removed = _("supprimées") if committed else _("seraient supprimées")

        rows = [
            (_("Blocs client lus"), stats['blocks']),
            (_("Clients (res.partner) %s") % verb_created, stats['partners_created']),
            (_("Projets %s") % verb_created, stats['projects_created']),
            (_("Projets rapprochés (existants)"), stats['projects_matched']),
            (_("Employés %s") % verb_created, stats['employees_created']),
            (_("Employés rapprochés (existants)"), stats['employees_matched']),
            (_("Affectations mensuelles"), stats['assignments']),
            (_("· dont jour de repos détecté"), stats['rest_days_detected']),
            (_("Lignes Planning Resources %s") % verb_created, stats['planning_lines_created']),
            (_("Lignes détail (jour)"), stats['actual_lines']),
            (_("· cellules « travail à la journée » (=1)"), stats['daily_paid_cells']),
            (_("· cellules « forfait mensuel » (>24)"), stats['month_forfait_cells']),
            (_("Total heures (mode horaire)"), round(stats['actual_hours'], 2)),
            (_("Anciennes lignes %s") % verb_removed, stats['actuals_removed']),
        ]
        body = "".join(
            "<tr><td>%s</td><td class='text-end fw-bold'>%s</td></tr>" % (lbl, val)
            for lbl, val in rows
        )
        warn = ""
        if stats['warnings']:
            items = "".join("<li>%s</li>" % w for w in stats['warnings'][:50])
            extra = ""
            if len(stats['warnings']) > 50:
                extra = _("<p>… et %d autres avertissements.</p>") % (
                    len(stats['warnings']) - 50)
            warn = ("<div class='alert alert-warning mt-2'>"
                    "<strong>%s</strong><ul>%s</ul>%s</div>") % (
                _("Avertissements"), items, extra)
        return (
            "<div class='alert %s'><strong>%s</strong></div>"
            "<table class='table table-sm table-striped'>%s</table>%s"
        ) % (css_alert, title, body, warn)
