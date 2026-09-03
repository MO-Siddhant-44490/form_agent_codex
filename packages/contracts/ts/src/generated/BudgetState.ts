// AUTO-GENERATED — do not edit. Source: packages/contracts/schema/budget_state.json
import { z } from "zod"

export const BudgetStateSchema = z.object({ "max_model_calls": z.number().int().gt(0), "max_retries_per_action": z.number().int().gt(0), "max_steps": z.number().int().gt(0), "max_wall_clock_seconds": z.number().int().gt(0), "model_calls_used": z.number().int().gte(0).default(0), "steps_used": z.number().int().gte(0).default(0) }).strict().describe("Hard caps that force termination with a classified outcome (invariant 12).")
export type BudgetState = z.infer<typeof BudgetStateSchema>
