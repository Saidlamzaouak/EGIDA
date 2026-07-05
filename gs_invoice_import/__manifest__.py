# -*- coding: utf-8 -*-
{
    'name': "GS Import Factures Surveillance",
    'summary': "Import des factures/avoirs clients depuis l'export 'detfactures' (.xls/.xlsx)",
    'description': """
GS Import Factures Surveillance
================================
Wizard d'import des factures de vente de prestations de surveillance depuis
l'extract 'detfactures.xls facture surveillance'.

Fonctionnement en 2 temps (aucune écriture avant validation) :
  1. « Analyser » : parse le fichier, regroupe par facture, rapproche les
     clients, et affiche un rapport de contrôle SANS rien créer.
  2. « Importer (brouillon) » : crée les factures/avoirs en BROUILLON pour
     relecture avant comptabilisation.

Règles métier :
  - Documents 'ES...' = factures (out_invoice), 'AV...' = avoirs (out_refund).
  - Lignes de sous-total par client (col N° = nom client) ignorées.
  - Total du fichier = montant HT de référence. Si le détail des lignes
    somme au total → lignes détaillées ; sinon → une ligne récapitulative.
  - TVA (20 % par défaut) ajoutée à la création.
  - Clients rapprochés par nom ; les manquants sont créés.
    """,
    'author': "AH",
    'website': "https://www.metraco.ma",
    'category': 'Accounting',
    'version': '18.0.1.3.0',
    'depends': ['account'],
    'data': [
        'security/ir.model.access.csv',
        'wizards/invoice_import_wizard_views.xml',
    ],
    'installable': True,
    'application': False,
    'license': 'LGPL-3',
}
