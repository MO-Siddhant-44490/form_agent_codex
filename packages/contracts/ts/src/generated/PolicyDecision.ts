// AUTO-GENERATED — do not edit. Source: packages/contracts/schema/policy_decision.json
import { z } from "zod"

export const PolicyDecisionSchema = z.object({ "action_id": z.string(), "decision": z.enum(["ALLOW","BLOCK"]), "detail": z.union([z.string(), z.null()]).default(null), "rule": z.union([z.enum(["credential_field","human_only_field","hidden_field","unknown_target","value_without_provenance","origin_mismatch","submit_without_approval","unsupported_option"]), z.null()]).default(null) }).strict()
export type PolicyDecision = z.infer<typeof PolicyDecisionSchema>
