/** @odoo-module **/

import { KanbanRenderer } from "@web/views/kanban/kanban_renderer";
import { PlanningSlotKanbanHeader } from "./planning_slot_kanban_header";

export class PlanningSlotKanbanRenderer extends KanbanRenderer {}

PlanningSlotKanbanRenderer.components = {
    ...KanbanRenderer.components,
    KanbanHeader: PlanningSlotKanbanHeader,
};
