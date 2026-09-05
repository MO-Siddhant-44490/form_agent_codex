// AUTO-GENERATED — do not edit. Source: packages/contracts/schema/user_question.json
import { z } from "zod"

export const UserQuestionSchema = z.object({ "fact_keys": z.array(z.string()).optional(), "field_id": z.union([z.string(), z.null()]).default(null), "kind": z.enum(["missing_fact","ambiguous_mapping","conflicting_facts","low_confidence","sensitive_mapping"]), "options": z.union([z.array(z.string()), z.null()]).default(null), "prompt": z.string(), "question_id": z.string() }).strict()
export type UserQuestion = z.infer<typeof UserQuestionSchema>
