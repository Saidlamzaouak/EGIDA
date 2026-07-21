/** @odoo-module **/

import { RelationalModel } from "@web/model/relational_model/relational_model";

/**
 * Modèle kanban dédié au dashboard planning.
 *
 * Odoo replie automatiquement toute colonne au-delà de la 10ᵉ colonne ouverte
 * (RelationalModel.MAX_NUMBER_OPENED_GROUPS = 10). Combiné au filtre par
 * semaine, on veut au contraire que TOUS les jours de la semaine sélectionnée
 * restent dépliés et visibles. On lève donc le plafond.
 */
export class PlanningSlotKanbanModel extends RelationalModel {}

PlanningSlotKanbanModel.MAX_NUMBER_OPENED_GROUPS = Number.MAX_SAFE_INTEGER;
