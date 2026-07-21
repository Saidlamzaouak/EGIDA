# -*- coding: utf-8 -*-
{
    'name': "Web Clipboard HTTP Fallback",
    'summary': "Bouton « copier » fonctionnel sur les instances servies en HTTP",
    'description': """
Web Clipboard HTTP Fallback
===========================
Sur une instance servie en **HTTP non sécurisé** (ex: http://IP:port), le
navigateur n'expose pas l'API `navigator.clipboard` (réservée aux « secure
contexts » : HTTPS ou localhost). Résultat : tout bouton « copier » d'Odoo —
notamment celui du dialogue d'erreur — lève :

    TypeError: Cannot read properties of undefined (reading 'writeText')
    at ClientErrorDialog.onClickClipboard

Ce module installe un **polyfill** `navigator.clipboard.writeText` basé sur
`document.execCommand('copy')` UNIQUEMENT quand l'API native est absente.
En HTTPS, l'implémentation native est conservée intacte.

⚠ Ceci est un contournement : la vraie solution reste de servir Odoo en HTTPS.
    """,
    'author': "AH",
    'website': "https://www.metraco.ma",
    'category': 'Technical',
    'version': '18.0.1.0.0',
    'depends': ['web'],
    'assets': {
        'web.assets_backend': [
            'web_clipboard_http_fallback/static/src/js/clipboard_http_fallback.js',
        ],
        'web.assets_frontend': [
            'web_clipboard_http_fallback/static/src/js/clipboard_http_fallback.js',
        ],
    },
    'installable': True,
    'application': False,
    'auto_install': False,
    'license': 'LGPL-3',
}
