/**
 * Polyfill presse-papier pour instances servies en HTTP (contexte non sécurisé).
 *
 * En HTTP simple, le navigateur n'expose pas `navigator.clipboard`, donc l'appel
 * `browser.navigator.clipboard.writeText(...)` du dialogue d'erreur Odoo lève
 * « Cannot read properties of undefined (reading 'writeText') ».
 *
 * `browser.navigator` (@web/core/browser/browser) EST le `navigator` global :
 * polyfiller `window.navigator.clipboard` corrige donc aussi tous les widgets
 * « copier » d'Odoo. On n'agit que si l'API native est absente — en HTTPS,
 * l'implémentation d'origine est conservée.
 *
 * Fichier volontairement écrit en script simple (pas de `@odoo-module`) afin
 * d'être exécuté dès le chargement du bundle, avant tout dialogue d'erreur.
 */
(function () {
    "use strict";

    const nav = window.navigator;
    if (nav.clipboard && typeof nav.clipboard.writeText === "function") {
        return; // Secure context : rien à faire.
    }

    function legacyCopy(text) {
        return new Promise(function (resolve, reject) {
            const textarea = document.createElement("textarea");
            textarea.value = text == null ? "" : String(text);
            textarea.setAttribute("readonly", "");
            // Hors écran, sans provoquer de scroll ni de flash visuel.
            textarea.style.position = "fixed";
            textarea.style.top = "-1000px";
            textarea.style.left = "0";
            textarea.style.opacity = "0";
            document.body.appendChild(textarea);
            textarea.focus();
            textarea.select();
            textarea.setSelectionRange(0, textarea.value.length);
            let ok = false;
            try {
                ok = document.execCommand("copy");
            } catch (err) {
                document.body.removeChild(textarea);
                reject(err);
                return;
            }
            document.body.removeChild(textarea);
            if (ok) {
                resolve();
            } else {
                reject(new Error("document.execCommand('copy') a échoué"));
            }
        });
    }

    try {
        Object.defineProperty(nav, "clipboard", {
            configurable: true,
            enumerable: false,
            value: {
                writeText: legacyCopy,
                readText: function () {
                    // Lecture non supportée par le fallback : on renvoie du vide
                    // pour ne pas casser les appelants qui l'attendent.
                    return Promise.resolve("");
                },
            },
        });
    } catch (err) {
        // Rare : navigateur refusant de redéfinir navigator.clipboard.
        // eslint-disable-next-line no-console
        console.warn(
            "[web_clipboard_http_fallback] impossible de polyfiller navigator.clipboard :",
            err
        );
    }
})();
