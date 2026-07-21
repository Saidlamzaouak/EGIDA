/** @odoo-module **/

import { KanbanRenderer } from "@web/views/kanban/kanban_renderer";
import { PlanningSlotKanbanHeader } from "./planning_slot_kanban_header";
import { PlanningWeekFilter } from "./planning_slot_week_filter";

export class PlanningSlotKanbanRenderer extends KanbanRenderer {
    static template = "gs_project_planning.PlanningSlotKanbanRenderer";
}

PlanningSlotKanbanRenderer.components = {
    ...KanbanRenderer.components,
    KanbanHeader: PlanningSlotKanbanHeader,
    PlanningWeekFilter,
};
