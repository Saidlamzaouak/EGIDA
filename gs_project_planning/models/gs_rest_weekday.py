# -*- coding: utf-8 -*-
from odoo import models, fields


class GsRestWeekday(models.Model):
    """Référentiel des jours de la semaine, utilisé pour les jours de repos.

    Permet d'affecter PLUSIEURS jours de repos à un employé (ex. Samedi +
    Dimanche), là où une simple sélection n'en autorisait qu'un.
    """
    _name = 'gs.rest.weekday'
    _description = "Jour de la semaine (repos)"
    _order = 'dayofweek'

    name = fields.Char(string="Jour", required=True, translate=True)
    dayofweek = fields.Integer(
        string="N° du jour", required=True, index=True,
        help="Aligné sur date.weekday() de Python : 0 = Lundi … 6 = Dimanche.",
    )

    _sql_constraints = [
        ('uniq_dayofweek', 'unique(dayofweek)',
         "Un jour de la semaine ne peut être défini qu'une seule fois."),
    ]
