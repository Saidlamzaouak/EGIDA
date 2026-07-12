/** @odoo-module **/

import { RelationalModel } from "@web/model/relational_model/relational_model";

/**
 * Modèle kanban dédié au dashboard planning.
 *
 * Par défaut, Odoo replie automatiquement toute colonne au-delà de la 10ᵉ
 * colonne ouverte (RelationalModel.MAX_NUMBER_OPENED_GROUPS = 10). Résultat :
 * quand on groupe par jour sur une longue période, seules les 10 premières
 * journées restent visibles, les suivantes s'affichent repliées ("0 h /
 * 0 effectif") car leurs enregistrements ne sont pas chargés.
 *
 * Ici on veut que TOUTES les journées restent dépliées et visibles, comme la
 * colonne du 14 janv. On lève donc simplement le plafond.
 */
export class PlanningSlotKanbanModel extends RelationalModel {}

PlanningSlotKanbanModel.MAX_NUMBER_OPENED_GROUPS = Number.MAX_SAFE_INTEGER;
