/** @odoo-module **/

import { Component, useState, useRef, onWillStart, onMounted } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";
import { browser } from "@web/core/browser/browser";
import { Domain } from "@web/core/domain";

/**
 * Barre de filtre par semaine + résumé, affichée au-dessus des colonnes du
 * kanban planning. S'inspire de la maquette filtre_semaine_validation.html.
 *
 * - Charge les semaines (get_planning_weeks) sur le PÉRIMÈTRE PROJET.
 * - Restaure la semaine choisie par l'utilisateur, sinon la semaine en cours.
 * - Un clic sur une pilule recharge le board sur les bornes de la semaine.
 *
 * La semaine active est mémorisée en sessionStorage (clé par projet) car les
 * actions du module (valider / annuler / absent / wizards) se terminent par un
 * `soft_reload` qui détruit puis recrée ce composant : sans persistance, l'état
 * `activeKey` serait perdu et le board reviendrait à la semaine en cours à
 * chaque opération. Seul un clic explicite doit changer de semaine.
 */
export class PlanningWeekFilter extends Component {
    static template = "gs_project_planning.PlanningWeekFilter";
    static props = ["list"];

    setup() {
        this.orm = useService("orm");
        this.scrollRef = useRef("scroll");
        this.state = useState({ weeks: [], activeKey: null, loading: true });

        // Le modèle (RelationalModel) est stable entre les re-rendus : on y
        // mémorise le domaine de base RÉEL (avant tout filtre semaine) pour ne
        // pas le polluer après un premier filtrage.
        const model = this.props.list.model;
        if (model._gsBaseDomain === undefined) {
            model._gsBaseDomain = this.props.list.domain || [];
        }
        this.baseDomain = model._gsBaseDomain;
        this._initialWeek = null;

        // Récupération des semaines pendant le willStart (lecture seule, aucune
        // mutation du modèle). Le filtrage initial est appliqué au onMounted
        // pour ne pas recharger le modèle pendant le rendu.
        onWillStart(async () => {
            await this.loadWeeks();
        });
        onMounted(() => {
            if (this._initialWeek) {
                this.selectWeek(this._initialWeek);
            }
        });
    }

    get projectId() {
        const ctx = this.props.list.context || {};
        return ctx.default_project_id || false;
    }

    /** Clé de mémorisation de la semaine active, cloisonnée par projet : on ne
     * veut pas reporter la semaine d'un projet sur un autre. */
    get storageKey() {
        return `gs_planning_week:${this.projectId || "all"}`;
    }

    /** Semaine mémorisée lors d'un précédent clic, si elle existe encore dans
     * la liste courante (elle peut disparaître si les shifts ont été supprimés
     * ou si l'on a changé de projet). */
    get rememberedWeek() {
        let key = null;
        try {
            key = browser.sessionStorage.getItem(this.storageKey);
        } catch {
            // sessionStorage indisponible (mode privé, quota) : on dégrade
            // proprement sur le comportement par défaut.
            return null;
        }
        if (!key) {
            return null;
        }
        return this.state.weeks.find((w) => w.key === key) || null;
    }

    rememberWeek(week) {
        try {
            browser.sessionStorage.setItem(this.storageKey, week.key);
        } catch {
            // Persistance best-effort : l'absence de stockage ne doit jamais
            // casser le filtrage.
        }
    }

    /** Domaine servant à CALCULER les stats de chaque semaine : périmètre
     * projet seul (indépendant des filtres de statut actifs). */
    get statsDomain() {
        if (this.projectId) {
            return [["project_id", "=", this.projectId]];
        }
        return this.baseDomain;
    }

    async loadWeeks() {
        const weeks = await this.orm.call("planning.slot", "get_planning_weeks", [
            this.statsDomain,
        ]);
        this.state.weeks = weeks || [];
        this.state.loading = false;
        if (!this.state.weeks.length) {
            return;
        }
        // Sélection initiale, par ordre de priorité :
        //   1. la semaine choisie par l'utilisateur (survit aux soft_reload) ;
        //   2. la semaine en cours (premier affichage) ;
        //   3. la dernière semaine disponible.
        // Appliquée au onMounted.
        const current = this.state.weeks.find((w) => w.is_current);
        this._initialWeek =
            this.rememberedWeek ||
            current ||
            this.state.weeks[this.state.weeks.length - 1];
    }

    get activeWeek() {
        return this.state.weeks.find((w) => w.key === this.state.activeKey) || null;
    }

    get summary() {
        const w = this.activeWeek;
        if (!w) {
            return { hours: 0, validated: 0, pending: 0, absent: 0 };
        }
        return {
            hours: w.hours,
            validated: w.validated,
            pending: w.pending,
            absent: w.absent,
        };
    }

    hoursLabel(h) {
        const hh = Math.floor(h || 0);
        const mm = Math.round(((h || 0) - hh) * 60);
        if (!mm) {
            return `${hh} h`;
        }
        return `${hh} h ${String(mm).padStart(2, "0")}`;
    }

    progressPct(w) {
        const denom = w.validated + w.pending;
        if (denom <= 0) {
            return w.validated > 0 ? 100 : 0;
        }
        return Math.round((w.validated / denom) * 100);
    }

    isDone(w) {
        return w.total > 0 && w.pending === 0;
    }

    async selectWeek(week) {
        if (!week) {
            return;
        }
        this.state.activeKey = week.key;
        // Mémorisé immédiatement : c'est cette valeur qui sera relue si une
        // action déclenche un soft_reload juste après.
        this.rememberWeek(week);
        const weekDomain = [
            "&",
            ["start_datetime", ">=", week.domain_start],
            ["start_datetime", "<", week.domain_end],
        ];
        const desired = Domain.and([this.baseDomain, weekDomain]).toList();
        const current = this.props.list.domain || [];
        // On ne recharge que si le domaine change réellement — évite une boucle
        // de rechargement à chaque re-rendu du modèle.
        if (JSON.stringify(current) !== JSON.stringify(desired)) {
            await this.props.list.load({ domain: desired });
        }
    }

    onWeekClick(week) {
        this.selectWeek(week);
    }

    goToday() {
        const current = this.state.weeks.find((w) => w.is_current);
        if (current) {
            this.selectWeek(current);
        }
    }

    scrollWeeks(dir) {
        const el = this.scrollRef.el;
        if (el) {
            el.scrollBy({ left: dir * 220, behavior: "smooth" });
        }
    }
}
