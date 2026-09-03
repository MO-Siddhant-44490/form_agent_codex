// AUTO-GENERATED — do not edit. Source: packages/contracts/schema/field_mapping_batch.json
import { z } from "zod"

export const FieldMappingBatchSchema = z.object({ "mappings": z.array(z.object({ "confidence": z.number().gte(0).lte(1), "fact_key": z.union([z.string(), z.null()]).default(null), "field_id": z.string(), "needs_clarification": z.boolean().default(false), "reason": z.union([z.string(), z.null()]).default(null), "selected_option_value": z.union([z.string(), z.null()]).default(null) }).strict().describe("One proposed field->fact assignment. `selected_option_value` carries a\nmodel-chosen option for enumerated controls; it must exist among the\nfield's observed options or policy discards the mapping.")).optional() }).strict()
export type FieldMappingBatch = z.infer<typeof FieldMappingBatchSchema>
