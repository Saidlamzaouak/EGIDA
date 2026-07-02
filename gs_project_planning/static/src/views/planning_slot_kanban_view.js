/** @odoo-module **/

import { registry } from "@web/core/registry";
import { kanbanView } from "@web/views/kanban/kanban_view";
import { PlanningSlotKanbanRenderer } from "./planning_slot_kanban_renderer";

export const planningSlotKanbanView = {
    ...kanbanView,
    Renderer: PlanningSlotKanbanRenderer,
};

registry.category("views").add("planning_slot_kanban_gs", planningSlotKanbanView);
