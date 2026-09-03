// AUTO-GENERATED — do not edit. Source: packages/contracts/schema/action_result.json
import { z } from "zod"

export const ActionResultSchema = z.object({ "action_id": z.string(), "error": z.union([z.string(), z.null()]).default(null), "executed_at": z.union([z.string().datetime({ offset: true }), z.null()]).default(null), "rejection_reason": z.union([z.enum(["run_mismatch","tab_mismatch","origin_mismatch","stale_sequence","stale_observation","missing_approval","policy_block","unsupported"]), z.null()]).default(null), "status": z.enum(["EXECUTED","DUPLICATE","REJECTED","FAILED"]) }).strict()
export type ActionResult = z.infer<typeof ActionResultSchema>
