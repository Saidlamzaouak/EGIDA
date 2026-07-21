/** @odoo-module **/

import { KanbanHeader } from "@web/views/kanban/kanban_header";
import { useService } from "@web/core/utils/hooks";

export class PlanningSlotKanbanHeader extends KanbanHeader {
    static template = "gs_project_planning.PlanningSlotKanbanHeader";

    setup() {
        super.setup();
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");
    }

    get group() {
        // En Odoo 18, KanbanHeader reçoit `props.group` (le Group spécifique)
        // ET `props.list` (la liste ROOT du kanban). Il faut utiliser `group`
        // pour accéder aux infos de la colonne.
        return this.props.group || this.props.list;
    }

    // Jours en français indexés sur DateTime.weekday de Luxon (1=Lundi … 7=Dimanche)
    static WEEKDAYS_FR = [
        "Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche",
    ];

    get groupName() {
        const base = super.groupName;
        // Préfixe le nom du jour : « Lundi 13 juil. 2026 ».
        if (this.isGroupedByDate && typeof base === "string") {
            const v = this.group && this.group.value;
            if (v && typeof v.weekday === "number") {
                const day = this.constructor.WEEKDAYS_FR[v.weekday - 1];
                if (day) {
                    return `${day} ${base}`;
                }
            }
        }
        return base;
    }

    get isGroupedByDate() {
        const grp = this.group;
        const gbf = grp && (grp.groupByField || grp._config?.groupBy);
        if (gbf && gbf.name) {
            return gbf.name === "start_datetime";
        }
        // Fallback : si on a un displayName et qu'on est groupé sur start_datetime
        const list = this.props.list;
        const rootGroupBy = list && list.groupBy && list.groupBy[0];
        return !!(rootGroupBy && rootGroupBy.startsWith("start_datetime"));
    }

    get groupRecords() {
        const grp = this.group;
        if (!grp) return [];
        // Group.records est le tableau des records de ce groupe
        if (Array.isArray(grp.records)) {
            return grp.records;
        }
        // Fallback : Group.list.records (DynamicRecordList interne)
        if (grp.list && Array.isArray(grp.list.records)) {
            return grp.list.records;
        }
        return [];
    }

    get hasPendingRecords() {
        return this.groupRecords.some((r) => {
            const data = (r && r.data) || {};
            return !data.is_validated && !data.is_absent;
        });
    }

    get isDayLocked() {
        // Journée « verrouillée » = elle a des shifts et ils sont TOUS validés
        // ou absents. Dans ce cas on n'autorise plus l'ajout d'agent.
        const records = this.groupRecords;
        if (!records.length) {
            return false;
        }
        return records.every((r) => {
            const d = (r && r.data) || {};
            return d.is_validated || d.is_absent;
        });
    }

    get canAddAgent() {
        return this.isGroupedByDate && !!this.projectId && !this.isDayLocked;
    }

    get projectId() {
        // project_id injecté via le contexte de l'action (default_project_id)
        const ctx =
            (this.group && this.group.context) ||
            (this.props.list && this.props.list.context) ||
            {};
        return ctx.default_project_id || false;
    }

    get dayISO() {
        // group.value est un DateTime Luxon (dernier instant de la journée
        // locale) pour un regroupement start_datetime:day.
        const v = this.group && this.group.value;
        if (v && typeof v.toFormat === "function") {
            return v.toFormat("yyyy-LL-dd");
        }
        return false;
    }

    async onAddAgent() {
        const projectId = this.projectId;
        const dayISO = this.dayISO;
        if (!projectId) {
            this.notification.add(
                "Projet non identifié : ouvrez le planning depuis la fiche d'un projet.",
                { type: "warning" }
            );
            return;
        }
        if (!dayISO) {
            this.notification.add("Journée non identifiée pour cette colonne.", {
                type: "warning",
            });
            return;
        }
        const action = await this.orm.call(
            "planning.slot",
            "action_open_add_agent_wizard",
            [dayISO, projectId]
        );
        if (action && typeof action === "object") {
            await this.action.doAction(action);
        }
    }

    get totalHours() {
        let total = 0;
        for (const r of this.groupRecords) {
            const d = (r && r.data) || {};
            total += d.duration_hours || 0;
        }
        return total;
    }

    get totalHoursLabel() {
        const total = this.totalHours;
        const h = Math.floor(total);
        const m = Math.round((total - h) * 60);
        if (m === 0) return `${h} h`;
        return `${h} h ${String(m).padStart(2, "0")}`;
    }

    get effectiveCount() {
        return this.groupRecords.filter((r) => {
            const d = (r && r.data) || {};
            return !d.is_absent;
        }).length;
    }

    get absentCount() {
        return this.groupRecords.filter((r) => {
            const d = (r && r.data) || {};
            return d.is_absent;
        }).length;
    }

    get replacementCount() {
        return this.groupRecords.filter((r) => {
            const d = (r && r.data) || {};
            // replaces_slot_id est un Many2one — valeur tronquée ou [id, name]
            const rep = d.replaces_slot_id;
            if (!rep) return false;
            if (Array.isArray(rep)) return !!rep[0];
            if (typeof rep === "object") return !!rep.resId;
            return !!rep;
        }).length;
    }

    async onValidateDay() {
        const grp = this.group;
        if (!grp) {
            this.notification.add(
                "Colonne non identifiée.",
                { type: "warning" }
            );
            return;
        }

        const records = this.groupRecords;

        // Log diagnostic visible via F12 → Console
        // eslint-disable-next-line no-console
        console.log("GS validate-group", {
            propsKeys: Object.keys(this.props),
            groupLabel: grp.displayName || grp.value || "(?)",
            recordsInGroup: records.length,
            groupKeys: Object.keys(grp),
            hasNestedList: !!grp.list,
            nestedListKeys: grp.list ? Object.keys(grp.list) : null,
        });

        if (!records.length) {
            this.notification.add(
                "Aucun shift chargé dans cette colonne.",
                { type: "warning" }
            );
            return;
        }

        // On ne garde que les non-validés et non-absents
        const pendingIds = records
            .filter((r) => {
                const d = (r && r.data) || {};
                return !d.is_validated && !d.is_absent;
            })
            .map((r) => r.resId)
            .filter((id) => id);

        if (!pendingIds.length) {
            this.notification.add(
                "Tous les shifts de cette colonne sont déjà validés ou absents.",
                { type: "info" }
            );
            return;
        }

        const result = await this.orm.call(
            "planning.slot",
            "action_validate_batch_for_payroll",
            [pendingIds]
        );
        if (result && typeof result === "object") {
            await this.action.doAction(result);
            return;
        }
        // Fallback refresh
        const root = this.props.list && this.props.list.model && this.props.list.model.root;
        if (root) {
            await root.load();
        }
    }
}
